# ruff: noqa: EM101, PLR0913, S106, TRY003
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.middleware.sessions import SessionMiddleware

from my_usermanager.adapters.fastapi import current_user
from my_usermanager.adapters.oidc import (
    MemoryOidcFlowStore,
    OidcJwksCache,
    OidcRelyingParty,
    complete_authorization_code,
    create_s256_code_challenge,
    load_openid_provider_metadata,
    verify_id_token,
)
from my_usermanager.adapters.oidc_fastapi import (
    build_oidc_callback_router,
    build_oidc_login_router,
)
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.sessions import SessionPrincipal
from my_usermanager.subjects import oidc_external_identity


def test_oidc_flow_store_issues_one_time_state_with_nonce_and_pkce() -> None:
    store = MemoryOidcFlowStore()
    started = store.start(now=1_000.0)

    consumed = store.consume(started.state, now=1_010.0)

    assert consumed.nonce == started.nonce
    assert consumed.verifier == started.verifier
    assert len(consumed.nonce) >= 32
    assert len(consumed.verifier) >= 43
    with pytest.raises(PermissionError, match="authentication unavailable"):
        store.consume(started.state, now=1_011.0)


def test_expired_oidc_flow_cannot_be_consumed() -> None:
    store = MemoryOidcFlowStore(ttl_seconds=30)
    started = store.start(now=1_000.0)

    with pytest.raises(PermissionError, match="authentication unavailable"):
        store.consume(started.state, now=1_031.0)


def _rsa_key() -> RSAKey:
    return RSAKey.generate_key(
        2048,
        parameters={"use": "sig", "alg": "RS256"},
        private=True,
        auto_kid=True,
    )


def _b64url(value: dict[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _id_token(
    key: RSAKey,
    *,
    issuer: str = "https://auth.example.test",
    audience: str = "app",
    subject: str = "user-1",
    nonce: str = "nonce-123",
    alg: str = "RS256",
    extra: dict[str, object] | None = None,
) -> str:
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
        "auth_time": now,
    }
    if extra:
        claims.update(extra)
    return jwt.encode({"alg": alg, "kid": key.kid}, claims, key)


def test_id_token_verification_requires_iss_aud_exp_and_nonce() -> None:
    key = _rsa_key()
    token = _id_token(key)
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())

    claims = verify_id_token(
        token,
        key=key,
        issuer="https://auth.example.test",
        audience="app",
        nonce="nonce-123",
        now=now,
    )

    assert claims["sub"] == "user-1"
    assert claims["iss"] == "https://auth.example.test"
    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            token,
            key=key,
            issuer="https://other.example.test",
            audience="app",
            nonce="nonce-123",
            now=now,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            token,
            key=key,
            issuer="https://auth.example.test",
            audience="other-app",
            nonce="nonce-123",
            now=now,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            token,
            key=key,
            issuer="https://auth.example.test",
            audience="app",
            nonce="wrong-nonce",
            now=now,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            token,
            key=key,
            issuer="https://auth.example.test",
            audience="app",
            nonce="nonce-123",
            now=now + 400,
        )


def test_jwks_cache_rotates_keys_and_limits_unknown_kid_refreshes() -> None:
    current = _rsa_key()
    rotated = _rsa_key()
    fetches: list[str] = []

    def fetch_jwks() -> dict[str, object]:
        fetches.append(current.kid or "")
        return {"keys": [current.as_dict(private=False)]}

    cache = OidcJwksCache(fetch_jwks, ttl_seconds=60, max_unknown_kid_refreshes=1)
    now = 1_000.0
    token = _id_token(current)
    claims = verify_id_token(
        token,
        key=cache.key_for(current.kid or "", now=now),
        issuer="https://auth.example.test",
        audience="app",
        nonce="nonce-123",
        now=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
    )
    assert claims["sub"] == "user-1"
    assert fetches == [current.kid]

    current = rotated
    rotated_token = _id_token(rotated)
    rotated_claims = verify_id_token(
        rotated_token,
        key=cache.key_for(rotated.kid or "", now=now),
        issuer="https://auth.example.test",
        audience="app",
        nonce="nonce-123",
        now=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
    )
    assert rotated_claims["sub"] == "user-1"
    assert fetches == [fetches[0], rotated.kid]

    with pytest.raises(PermissionError, match="authentication unavailable"):
        cache.key_for("still-unknown", now=now)
    assert len(fetches) == 3
    with pytest.raises(PermissionError, match="authentication unavailable"):
        cache.key_for("still-unknown", now=now)
    assert len(fetches) == 3

    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            _id_token(rotated),
            key=cache.key_for(rotated.kid or "", now=now),
            issuer="https://auth.example.test",
            audience="app",
            nonce="nonce-123",
            now=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) + 400,
        )


def test_openid_discovery_uses_host_issuer_and_fails_closed() -> None:
    issuer = "https://auth.example.test"
    fetched: list[str] = []

    def fetch(url: str) -> dict[str, object]:
        fetched.append(url)
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "jwks_uri": f"{issuer}/oauth/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    metadata = load_openid_provider_metadata(issuer, fetch=fetch)
    assert metadata.issuer == issuer
    assert metadata.authorization_endpoint == f"{issuer}/oauth/authorize"
    assert metadata.jwks_uri == f"{issuer}/oauth/jwks"
    assert fetched == [f"{issuer}/.well-known/openid-configuration"]

    def mismatched(url: str) -> dict[str, object]:
        del url
        return {
            "issuer": "https://other.example.test",
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "jwks_uri": f"{issuer}/oauth/jwks",
            "token_endpoint": f"{issuer}/oauth/token",
        }

    with pytest.raises(PermissionError, match="authentication unavailable"):
        load_openid_provider_metadata(issuer, fetch=mismatched)
    with pytest.raises(PermissionError, match="authentication unavailable"):
        load_openid_provider_metadata("https://attacker.example.test", fetch=fetch)
    with pytest.raises(ValueError, match="issuer"):
        load_openid_provider_metadata("http://auth.example.test", fetch=fetch)

    def boom(url: str) -> dict[str, object]:
        del url
        raise TimeoutError("discovery timed out")

    with pytest.raises(PermissionError, match="authentication unavailable"):
        load_openid_provider_metadata(issuer, fetch=boom)


def test_jwks_cache_fails_closed_when_fetch_raises() -> None:
    def fetch_jwks() -> dict[str, object]:
        raise TimeoutError("jwks unavailable")

    cache = OidcJwksCache(fetch_jwks, ttl_seconds=60)
    with pytest.raises(PermissionError, match="authentication unavailable"):
        cache.key_for("any", now=1_000.0)


def test_id_token_azp_must_match_client_when_present_or_when_aud_is_a_list() -> None:
    key = _rsa_key()
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())

    matching = verify_id_token(
        _id_token(key, extra={"azp": "app"}),
        key=key,
        issuer="https://auth.example.test",
        audience="app",
        nonce="nonce-123",
        now=now,
    )
    assert matching["azp"] == "app"

    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            _id_token(key, extra={"azp": "other-app"}),
            key=key,
            issuer="https://auth.example.test",
            audience="app",
            nonce="nonce-123",
            now=now,
        )

    listed = verify_id_token(
        _id_token(key, extra={"aud": ["app", "other"], "azp": "app"}),
        key=key,
        issuer="https://auth.example.test",
        audience="app",
        nonce="nonce-123",
        now=now,
    )
    assert listed["azp"] == "app"
    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            _id_token(key, extra={"aud": ["app", "other"]}),
            key=key,
            issuer="https://auth.example.test",
            audience="app",
            nonce="nonce-123",
            now=now,
        )


def test_id_token_signed_with_alg_none_is_rejected() -> None:
    key = _rsa_key()
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    payload = {
        "iss": "https://auth.example.test",
        "aud": "app",
        "sub": "user-1",
        "nonce": "nonce-123",
        "iat": now,
        "exp": now + 300,
    }
    token = ".".join(
        (
            _b64url({"alg": "none"}),
            _b64url(payload),
            "",
        )
    )

    with pytest.raises(PermissionError, match="authentication unavailable"):
        verify_id_token(
            token,
            key=key,
            issuer="https://auth.example.test",
            audience="app",
            nonce="nonce-123",
            now=now,
        )


class _IdentityStore:
    def __init__(self, user: User) -> None:
        self._user = user

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        if identity in self._user.external_identities:
            return self._user
        return None

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        del user_id, identity
        raise AssertionError("OIDC completion must not auto-link identities")


def test_authorization_code_completion_maps_issuer_sub_to_local_principal() -> None:
    issuer = "https://auth.example.test"
    identity = oidc_external_identity(issuer=issuer, subject="user-1")
    user = User(
        user_id="local-1",
        username="alice",
        external_identities=frozenset({identity}),
    )
    store = _IdentityStore(user)
    key = _rsa_key()
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    flows = MemoryOidcFlowStore()
    flow = flows.start(now=float(now))
    token = _id_token(key, nonce=flow.nonce)

    def redeem_code(code: str, *, verifier: str) -> str:
        assert code == "auth-code"
        assert verifier == flow.verifier
        return token

    relying_party = OidcRelyingParty(
        issuer=issuer,
        audience="app",
        key=key,
        store=store,
        project=lambda item: SessionPrincipal(
            user_id=item.user_id,
            username=item.username,
        ),
    )
    principal = complete_authorization_code(
        code="auth-code",
        state=flow.state,
        flows=flows,
        redeem_code=redeem_code,
        relying_party=relying_party,
        now=float(now),
    )

    assert principal.user_id == "local-1"
    assert principal.username == "alice"
    with pytest.raises(PermissionError, match="authentication unavailable"):
        complete_authorization_code(
            code="auth-code",
            state=flow.state,
            flows=flows,
            redeem_code=redeem_code,
            relying_party=relying_party,
            now=float(now + 1),
        )


def test_oidc_callback_writes_local_session_principal() -> None:
    issuer = "https://auth.example.test"
    identity = oidc_external_identity(issuer=issuer, subject="user-1")
    user = User(
        user_id="local-1",
        username="alice",
        external_identities=frozenset({identity}),
    )
    identities = _IdentityStore(user)
    key = _rsa_key()
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    flows = MemoryOidcFlowStore()
    flow = flows.start(now=float(now))
    token = _id_token(key, nonce=flow.nonce)
    redeemed: list[str] = []

    def redeem_code(code: str, *, verifier: str) -> str:
        redeemed.append(code)
        assert verifier == flow.verifier
        return token

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(
        build_oidc_callback_router(
            relying_party=OidcRelyingParty(
                issuer=issuer,
                audience="app",
                key=key,
                store=identities,
                project=lambda item: SessionPrincipal(
                    user_id=item.user_id,
                    username=item.username,
                ),
            ),
            flows=flows,
            redeem_code=redeem_code,
            now=lambda: float(now),
        )
    )

    @app.get("/me")
    def me(request: Request) -> dict[str, str | None]:
        principal = current_user(request)
        return {"user_id": None if principal is None else principal.user_id}

    client = TestClient(app, base_url="https://app.example.test")
    response = client.get(
        "/oidc/callback",
        params={"code": "auth-code", "state": flow.state},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert redeemed == ["auth-code"]
    assert client.get("/me").json() == {"user_id": "local-1"}
    replay = client.get(
        "/oidc/callback",
        params={"code": "auth-code", "state": flow.state},
        follow_redirects=False,
    )
    assert replay.status_code == 401


def test_oidc_login_starts_authorization_code_pkce_at_the_issuer() -> None:
    issuer = "https://auth.example.test"
    flows = MemoryOidcFlowStore()
    app = FastAPI()
    app.include_router(
        build_oidc_login_router(
            relying_party=OidcRelyingParty(
                issuer=issuer,
                audience="app",
                key=_rsa_key(),
                store=_IdentityStore(
                    User(user_id="local-1", username="alice"),
                ),
                project=lambda item: SessionPrincipal(user_id=item.user_id),
            ),
            flows=flows,
            authorization_endpoint=f"{issuer}/oauth/authorize",
            redirect_uri="https://app.example.test/oidc/callback",
            now=lambda: 1_000.0,
        )
    )
    client = TestClient(app, base_url="https://app.example.test")

    started = client.get("/oidc/login", follow_redirects=False)

    assert started.status_code == 302
    location = urlsplit(started.headers["location"])
    assert location.scheme == "https"
    assert location.netloc == "auth.example.test"
    assert location.path == "/oauth/authorize"
    query = parse_qs(location.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["app"]
    assert query["redirect_uri"] == ["https://app.example.test/oidc/callback"]
    assert query["scope"] == ["openid"]
    assert query["code_challenge_method"] == ["S256"]
    stored = flows.consume(query["state"][0], now=1_010.0)
    assert stored.nonce == query["nonce"][0]
    assert query["code_challenge"] == [create_s256_code_challenge(stored.verifier)]
