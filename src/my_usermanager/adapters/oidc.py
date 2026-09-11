# ruff: noqa: D107, PLR0913
"""Optional OpenID Connect relying-party helpers.

This module is the host-owned RP seam: authorization-code + S256 PKCE state,
verified ``(issuer, sub)`` mapping, and a FastAPI callback. It does not mint
tokens or implement an OpenID Provider. Importing it must stay explicit so
``import my_usermanager`` remains free of Authlib and FastAPI.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

from joserfc import jwt
from joserfc.jwt import JWTClaimsRegistry

from my_usermanager.authentication import resolve_authenticated_principal
from my_usermanager.subjects import AuthenticatedSubject, oidc_external_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from joserfc.jwk import KeyFlexible

    from my_usermanager.models import ExternalIdentity, User
    from my_usermanager.sessions import SessionPrincipal
    from my_usermanager.subjects import ExternalIdentityUserStore

__all__: Final[tuple[str, ...]] = (
    "MemoryOidcFlowStore",
    "OidcAuthorizationFlow",
    "OidcCodeRedeemer",
    "OidcFlowStore",
    "OidcRelyingParty",
    "complete_authorization_code",
    "create_s256_code_challenge",
    "verify_id_token",
)

_DEFAULT_TTL_SECONDS: Final = 300.0
_UNAVAILABLE: Final = "authentication unavailable"
_TTL_ERROR: Final = "ttl_seconds must be positive"


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
    subject = decoded.claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise PermissionError(_UNAVAILABLE)
    return decoded.claims


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
