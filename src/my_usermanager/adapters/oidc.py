# ruff: noqa: D107, PLR0913, TC003
"""Optional OpenID Connect relying-party helpers.

This module is the host-owned RP seam: authorization-code + S256 PKCE state,
verified ``(issuer, sub)`` mapping, and a FastAPI callback. It does not mint
tokens or implement an OpenID Provider. Importing it must stay explicit so
``import my_usermanager`` remains free of Authlib and FastAPI.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol
from urllib.parse import urlsplit

from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from my_usermanager.authentication import resolve_authenticated_principal
from my_usermanager.subjects import AuthenticatedSubject, oidc_external_identity

if TYPE_CHECKING:
    from joserfc.jwk import KeyFlexible

    from my_usermanager.models import ExternalIdentity, User
    from my_usermanager.sessions import SessionPrincipal
    from my_usermanager.subjects import ExternalIdentityUserStore

__all__: Final[tuple[str, ...]] = (
    "MemoryOidcFlowStore",
    "OidcAuthorizationFlow",
    "OidcCodeRedeemer",
    "OidcFlowStore",
    "OidcJwksCache",
    "OidcProviderMetadata",
    "OidcRelyingParty",
    "complete_authorization_code",
    "create_s256_code_challenge",
    "load_openid_provider_metadata",
    "verify_id_token",
)

_DEFAULT_TTL_SECONDS: Final = 300.0
_UNAVAILABLE: Final = "authentication unavailable"
_TTL_ERROR: Final = "ttl_seconds must be positive"
_DEFAULT_UNKNOWN_KID_REFRESHES: Final = 1
_ISSUER_HTTPS_ERROR: Final = "issuer must be an absolute HTTPS URL"
_UNKNOWN_KID_REFRESH_ERROR: Final = "max_unknown_kid_refreshes must be non-negative"


def create_s256_code_challenge(code_verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for an ASCII verifier."""
    digest = sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class OidcAuthorizationFlow:
    """One-time authorization-code values bound to a browser login attempt."""

    state: str
    nonce: str
    verifier: str


class OidcFlowStore(Protocol):
    """Host-owned storage for authorization state, nonce, and PKCE verifier."""

    def start(self, *, now: float) -> OidcAuthorizationFlow:
        """Issue a new one-time authorization flow."""
        ...

    def consume(self, state: str, *, now: float) -> OidcAuthorizationFlow:
        """Return and delete a still-valid flow, or raise PermissionError."""
        ...


@dataclass(frozen=True, slots=True)
class OidcProviderMetadata:
    """Host-trusted OpenID Provider endpoints from discovery."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


def load_openid_provider_metadata(
    issuer: str,
    *,
    fetch: Callable[[str], Mapping[str, object]],
) -> OidcProviderMetadata:
    """Fetch discovery for a host-configured issuer, or fail closed."""
    _require_https_issuer(issuer)
    document_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        document = fetch(document_url)
        metadata = OidcProviderMetadata(
            issuer=_require_https_url(document.get("issuer"), field_name="issuer"),
            authorization_endpoint=_require_https_url(
                document.get("authorization_endpoint"),
                field_name="authorization_endpoint",
            ),
            token_endpoint=_require_https_url(
                document.get("token_endpoint"),
                field_name="token_endpoint",
            ),
            jwks_uri=_require_https_url(
                document.get("jwks_uri"), field_name="jwks_uri"
            ),
        )
    except ValueError:
        raise
    except Exception as extra:
        raise PermissionError(_UNAVAILABLE) from extra
    if metadata.issuer != issuer:
        raise PermissionError(_UNAVAILABLE)
    algorithms = document.get("id_token_signing_alg_values_supported")
    if algorithms is not None and (
        not isinstance(algorithms, list) or "RS256" not in algorithms
    ):
        raise PermissionError(_UNAVAILABLE)
    return metadata


def _require_https_issuer(value: str) -> None:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError(_ISSUER_HTTPS_ERROR)


def _require_https_url(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise PermissionError(_UNAVAILABLE)
    del field_name
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.fragment:
        raise PermissionError(_UNAVAILABLE)
    return value


class OidcCodeRedeemer(Protocol):
    """Host-owned token-endpoint call that returns a verified ID token string."""

    def __call__(self, code: str, *, verifier: str) -> str:
        """Redeem an authorization code with the stored PKCE verifier."""
        ...


@dataclass(frozen=True, slots=True)
class OidcRelyingParty:
    """Configured issuer, audience, and local-account mapping for one RP."""

    issuer: str
    audience: str
    key: KeyFlexible
    store: ExternalIdentityUserStore
    project: Callable[[User], SessionPrincipal]


class OidcJwksCache:
    """Refreshable JWKS cache that fails closed on fetch or unknown kid."""

    def __init__(
        self,
        fetch_jwks: Callable[[], Mapping[str, object]],
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_unknown_kid_refreshes: int = _DEFAULT_UNKNOWN_KID_REFRESHES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(_TTL_ERROR)
        if max_unknown_kid_refreshes < 0:
            raise ValueError(_UNKNOWN_KID_REFRESH_ERROR)
        self._fetch_jwks = fetch_jwks
        self._ttl_seconds = ttl_seconds
        self._max_unknown_kid_refreshes = max_unknown_kid_refreshes
        self._expires_at = 0.0
        self._keys: KeySet | None = None
        self._unknown_kid_refreshes: dict[str, int] = {}

    def key_for(self, kid: str, *, now: float) -> KeyFlexible:
        """Return the current key for ``kid``, with one refresh per unknown id."""
        keys = self._keys_at(now)
        found = self._find(keys, kid)
        if found is not None:
            self._unknown_kid_refreshes.pop(kid, None)
            return found
        attempts = self._unknown_kid_refreshes.get(kid, 0)
        if attempts >= self._max_unknown_kid_refreshes:
            raise PermissionError(_UNAVAILABLE)
        self._unknown_kid_refreshes[kid] = attempts + 1
        keys = self._refresh(now)
        found = self._find(keys, kid)
        if found is None:
            raise PermissionError(_UNAVAILABLE)
        self._unknown_kid_refreshes.pop(kid, None)
        return found

    def _keys_at(self, now: float) -> KeySet:
        if self._keys is None or now >= self._expires_at:
            return self._refresh(now)
        return self._keys

    def _refresh(self, now: float) -> KeySet:
        try:
            payload = self._fetch_jwks()
            keys = KeySet.import_key_set(payload)
        except Exception as extra:
            raise PermissionError(_UNAVAILABLE) from extra
        self._keys = keys
        self._expires_at = now + self._ttl_seconds
        return keys

    @staticmethod
    def _find(keys: KeySet, kid: str) -> KeyFlexible | None:
        try:
            return keys.get_by_kid(kid)
        except InvalidKeyIdError:
            return None


class MemoryOidcFlowStore:
    """Process-local authorization-flow store for tests and development."""

    def __init__(self, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        if ttl_seconds <= 0:
            raise ValueError(_TTL_ERROR)
        self._ttl_seconds = ttl_seconds
        self._flows: dict[str, tuple[float, OidcAuthorizationFlow]] = {}

    def start(self, *, now: float) -> OidcAuthorizationFlow:
        """Issue a new one-time authorization flow."""
        self._expire(now)
        flow = OidcAuthorizationFlow(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            verifier=secrets.token_urlsafe(48),
        )
        self._flows[flow.state] = (now + self._ttl_seconds, flow)
        return flow

    def consume(self, state: str, *, now: float) -> OidcAuthorizationFlow:
        """Return and delete a still-valid flow, or raise PermissionError."""
        self._expire(now)
        record = self._flows.pop(state, None)
        if record is None:
            raise PermissionError(_UNAVAILABLE)
        expires_at, flow = record
        if now >= expires_at:
            raise PermissionError(_UNAVAILABLE)
        return flow

    def _expire(self, now: float) -> None:
        expired = [
            state for state, (expires_at, _) in self._flows.items() if now >= expires_at
        ]
        for state in expired:
            del self._flows[state]


def verify_id_token(
    token: str,
    *,
    key: KeyFlexible,
    issuer: str,
    audience: str,
    nonce: str,
    now: int,
    leeway: int = 30,
) -> Mapping[str, object]:
    """Verify an RS256 ID token and return its claims, or fail closed."""
    try:
        decoded = jwt.decode(token, key, algorithms=["RS256"])
        claims_request = JWTClaimsRegistry(
            now=now,
            leeway=leeway,
            iss={"essential": True, "value": issuer},
            aud={"essential": True, "value": audience},
            sub={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        claims_request.validate(decoded.claims)
    except Exception as extra:
        raise PermissionError(_UNAVAILABLE) from extra
    claims = decoded.claims
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise PermissionError(_UNAVAILABLE)
    _require_authorized_party(claims, audience=audience)
    return claims


def _require_authorized_party(claims: Mapping[str, object], *, audience: str) -> None:
    authorized_party = claims.get("azp")
    audiences = claims.get("aud")
    listed = isinstance(audiences, list) and len(audiences) > 1
    if authorized_party is None:
        if listed:
            raise PermissionError(_UNAVAILABLE)
        return
    if authorized_party != audience:
        raise PermissionError(_UNAVAILABLE)


def complete_authorization_code(
    *,
    code: str,
    state: str,
    flows: OidcFlowStore,
    redeem_code: OidcCodeRedeemer,
    relying_party: OidcRelyingParty,
    now: float,
) -> SessionPrincipal:
    """Consume a one-time flow, redeem the code, and resolve the local user."""
    flow = flows.consume(state, now=now)
    try:
        id_token = redeem_code(code, verifier=flow.verifier)
    except Exception as extra:
        raise PermissionError(_UNAVAILABLE) from extra
    if not isinstance(id_token, str) or not id_token:
        raise PermissionError(_UNAVAILABLE)
    claims = verify_id_token(
        id_token,
        key=relying_party.key,
        issuer=relying_party.issuer,
        audience=relying_party.audience,
        nonce=flow.nonce,
        now=int(now),
    )
    subject = claims["sub"]
    if not isinstance(subject, str):
        raise PermissionError(_UNAVAILABLE)
    identity = oidc_external_identity(issuer=relying_party.issuer, subject=subject)
    authenticated = AuthenticatedSubject(
        provider=identity.provider,
        subject=identity.subject,
        user_id=_existing_local_user_id(relying_party.store, identity),
    )
    return resolve_authenticated_principal(
        authenticated,
        store=relying_party.store,
        project=relying_party.project,
    )


def _existing_local_user_id(
    store: ExternalIdentityUserStore,
    identity: ExternalIdentity,
) -> str:
    user = store.resolve_external_identity(identity)
    if user is None:
        raise PermissionError(_UNAVAILABLE)
    return user.user_id
