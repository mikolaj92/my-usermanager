"""Cheap later swap: change the issuer URL, keep the local account.

No second process and no Keycloak. The product route sees only a local
principal. A second OpenID Provider is another HTTPS issuer plus an explicit
(issuer, sub) link.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.middleware.sessions import SessionMiddleware

from my_usermanager.adapters.fastapi import current_user, write_current_user
from my_usermanager.adapters.oidc import (
    MemoryOidcFlowStore,
    OidcRelyingParty,
    complete_authorization_code,
)
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.sessions import SessionPrincipal
from my_usermanager.subjects import (
    ExternalIdentityConflictError,
    oidc_external_identity,
)

LOCAL_ISSUER = "https://app.example.test"
REMOTE_ISSUER = "https://idp.example.test"


def notes_owner(request: Request) -> dict[str, str | None]:
    """Product domain: local user_id only, never a provider SDK type."""
    principal = current_user(request)
    return {"owner": None if principal is None else principal.user_id}


class _LinkedStore:
    def __init__(self, user: User) -> None:
        self._users = {user.user_id: user}
        self._links = dict.fromkeys(user.external_identities, user.user_id)

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        user_id = self._links.get(identity)
        if user_id is None:
            return None
        return self._users[user_id]

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        existing = self._links.get(identity)
        if existing is not None and existing != user_id:
            raise ExternalIdentityConflictError(
                identity=identity,
                existing_user_id=existing,
                requested_user_id=user_id,
            )
        user = self._users[user_id]
        updated = replace(
            user,
            external_identities=user.external_identities | {identity},
        )
        self._users[user_id] = updated
        self._links[identity] = user_id
        return updated


def _rsa_key() -> RSAKey:
    return RSAKey.generate_key(
        2048,
        parameters={"use": "sig", "alg": "RS256"},
        private=True,
        auto_kid=True,
    )


def _id_token(key: RSAKey, *, issuer: str, nonce: str) -> str:
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    return jwt.encode(
        {"alg": "RS256", "kid": key.kid},
        {
            "iss": issuer,
            "aud": "app",
            "sub": "person-1",
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
        },
        key,
    )


def _complete(
    *,
    issuer: str,
    key: RSAKey,
    store: _LinkedStore,
    now: float,
) -> SessionPrincipal:
    flows = MemoryOidcFlowStore()
    flow = flows.start(now=now)
    token = _id_token(key, issuer=issuer, nonce=flow.nonce)
    return complete_authorization_code(
        code="auth-code",
        state=flow.state,
        flows=flows,
        redeem_code=lambda _code, *, verifier: token,
        relying_party=OidcRelyingParty(
            issuer=issuer,
            audience="app",
            key=key,
            store=store,
            project=lambda item: SessionPrincipal(
                user_id=item.user_id,
                username=item.username,
            ),
        ),
        now=now,
    )


def test_product_domain_does_not_import_my_auth() -> None:
    source = inspect.getsource(notes_owner)
    assert "my_auth" not in source
    assert "Passkey" not in source
    assert "current_user" in source


def test_same_app_keeps_local_user_when_issuer_url_changes() -> None:
    now = float(int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()))
    local_identity = oidc_external_identity(issuer=LOCAL_ISSUER, subject="person-1")
    remote_identity = oidc_external_identity(issuer=REMOTE_ISSUER, subject="person-1")
    user = User(
        user_id="local-1",
        username="alice",
        external_identities=frozenset({local_identity}),
    )
    store = _LinkedStore(user)
    local_key = _rsa_key()
    remote_key = _rsa_key()

    first = _complete(issuer=LOCAL_ISSUER, key=local_key, store=store, now=now)
    assert first.user_id == "local-1"

    with pytest.raises(PermissionError, match="authentication unavailable"):
        _complete(issuer=REMOTE_ISSUER, key=remote_key, store=store, now=now + 1)

    linked = store.link_external_identity(
        user_id="local-1",
        identity=remote_identity,
    )
    assert linked.user_id == "local-1"
    second = _complete(
        issuer=REMOTE_ISSUER,
        key=remote_key,
        store=store,
        now=now + 2,
    )
    assert second.user_id == "local-1"
    assert second.username == "alice"
    assert "my_auth" not in sys.modules


def test_product_route_sees_the_same_owner_after_issuer_swap() -> None:
    now = float(int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()))
    store = _LinkedStore(
        User(
            user_id="local-1",
            username="alice",
            external_identities=frozenset(
                {
                    oidc_external_identity(issuer=LOCAL_ISSUER, subject="person-1"),
                    oidc_external_identity(issuer=REMOTE_ISSUER, subject="person-1"),
                }
            ),
        )
    )
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")  # noqa: S106
    app.get("/notes")(notes_owner)
    client = TestClient(app, base_url="https://app.example.test")

    @app.post("/login-as")
    def login_as(request: Request, issuer: str) -> dict[str, str]:
        principal = _complete(
            issuer=issuer,
            key=_rsa_key(),
            store=store,
            now=now,
        )
        write_current_user(request, principal)
        return {"user_id": principal.user_id}

    assert client.post("/login-as", params={"issuer": LOCAL_ISSUER}).json() == {
        "user_id": "local-1"
    }
    assert client.get("/notes").json() == {"owner": "local-1"}
    assert client.post("/login-as", params={"issuer": REMOTE_ISSUER}).json() == {
        "user_id": "local-1"
    }
    assert client.get("/notes").json() == {"owner": "local-1"}
