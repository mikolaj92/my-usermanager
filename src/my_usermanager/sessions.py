"""Session principal values and storage helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Protocol, Self, cast, override, runtime_checkable

from my_usermanager.models import (
    ExternalIdentity,
    Permission,
    ValidationError,
    validate_identifier,
)

__all__: Final[tuple[str, ...]] = (
    "SESSION_PRINCIPAL_KEY",
    "InvalidSessionPrincipalError",
    "SessionClaimValue",
    "SessionPrincipal",
    "SessionTokenStore",
    "clear_session_principal",
    "clear_token_principal",
    "principal_template_context",
    "read_session_principal",
    "read_token_principal",
    "refresh_session_principal",
    "write_session_principal",
    "write_token_principal",
)

type SessionClaimValue = str | int | float | bool | None
type SessionData = Mapping[str, object]
type MutableSessionData = MutableMapping[str, object]

SESSION_PRINCIPAL_KEY: Final = "my_usermanager.principal"


def _empty_claims() -> Mapping[str, SessionClaimValue]:
    claims: dict[str, SessionClaimValue] = {}
    return MappingProxyType(claims)


@dataclass(frozen=True, slots=True)
class InvalidSessionPrincipalError(ValueError):
    """Raised when serialized session principal data is malformed."""

    field_name: str
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable validation message."""
        return f"{self.field_name}: {self.reason}"


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    """Typed current-user value safe for sessions, dependencies, and templates."""

    user_id: str
    username: str | None = None
    display_name: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[Permission] = field(default_factory=frozenset)
    external_identities: frozenset[ExternalIdentity] = field(default_factory=frozenset)
    claims: Mapping[str, SessionClaimValue] = field(default_factory=_empty_claims)

    def __post_init__(self) -> None:
        """Validate and freeze all nested values."""
        _ = _require_identifier(self.user_id, field_name="user_id")
        if self.username is not None:
            _ = _require_identifier(self.username, field_name="username")
        if self.display_name is not None:
            _ = _require_text(self.display_name, field_name="display_name")
        object.__setattr__(
            self,
            "roles",
            frozenset(
                _require_identifier(role, field_name="role") for role in self.roles
            ),
        )
        object.__setattr__(self, "permissions", frozenset(self.permissions))
        object.__setattr__(
            self,
            "external_identities",
            frozenset(self.external_identities),
        )
        object.__setattr__(self, "claims", _frozen_claims(self.claims))

    @classmethod
    def from_session(cls, payload: SessionData) -> Self:
        """Parse a JSON-safe session payload into a typed principal."""
        return cls(
            user_id=_required_string(payload.get("user_id"), field_name="user_id"),
            username=_optional_string(payload.get("username"), field_name="username"),
            display_name=_optional_string(
                payload.get("display_name"),
                field_name="display_name",
            ),
            roles=frozenset(_string_items(payload.get("roles"), field_name="roles")),
            permissions=frozenset(
                Permission(name)
                for name in _string_items(
                    payload.get("permissions"),
                    field_name="permissions",
                )
            ),
            external_identities=_external_identity_items(
                payload.get("external_identities"),
            ),
            claims=_claim_items(payload.get("claims")),
        )

    def to_session(self) -> dict[str, object]:
        """Return a JSON-safe session payload."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "roles": sorted(self.roles),
            "permissions": sorted(permission.name for permission in self.permissions),
            "external_identities": [
                {"provider": identity.provider, "subject": identity.subject}
                for identity in sorted(
                    self.external_identities,
                    key=lambda identity: (identity.provider, identity.subject),
                )
            ],
            "claims": dict(sorted(self.claims.items())),
        }

    def has_role(self, role_name: str) -> bool:
        """Return whether the principal has a role by name."""
        checked_role = _require_identifier(role_name, field_name="role_name")
        return checked_role in self.roles

    def has_permission(self, permission: str | Permission) -> bool:
        """Return whether the principal has a direct projected permission."""
        checked_permission = (
            permission if isinstance(permission, Permission) else Permission(permission)
        )
        return checked_permission in self.permissions


@runtime_checkable
class SessionTokenStore(Protocol):
    """Store seam for DB-backed opaque session-token cookies."""

    def get(self, token: str) -> SessionPrincipal | None:
        """Return the principal for an opaque token or None."""
        ...

    def save(self, token: str, principal: SessionPrincipal) -> SessionPrincipal:
        """Persist a principal for an opaque token."""
        ...

    def delete(self, token: str) -> None:
        """Delete an opaque token session if present."""
        ...


def write_session_principal(
    session: MutableSessionData,
    principal: SessionPrincipal,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> SessionPrincipal:
    """Write a principal into a dict-like request.session backend."""
    session[key] = principal.to_session()
    return principal


def read_session_principal(
    session: SessionData,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> SessionPrincipal | None:
    """Read a principal from a dict-like session, returning None if malformed."""
    payload = session.get(key)
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        return None
    try:
        return SessionPrincipal.from_session(cast("SessionData", payload))
    except (InvalidSessionPrincipalError, ValidationError):
        return None


def clear_session_principal(
    session: MutableSessionData,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> None:
    """Clear the current principal from a dict-like session."""
    _ = session.pop(key, None)


def refresh_session_principal(
    session: MutableSessionData,
    refresh: Callable[[SessionPrincipal], SessionPrincipal | None],
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> SessionPrincipal | None:
    """Refresh or clear the current principal using caller-owned lookup logic."""
    principal = read_session_principal(session, key=key)
    if principal is None:
        return None
    refreshed = refresh(principal)
    if refreshed is None:
        clear_session_principal(session, key=key)
        return None
    return write_session_principal(session, refreshed, key=key)


def read_token_principal(
    token: str | None,
    store: SessionTokenStore,
) -> SessionPrincipal | None:
    """Read a DB-backed principal for an opaque cookie token."""
    if token is None or token == "":
        return None
    return store.get(_require_identifier(token, field_name="session_token"))


def write_token_principal(
    token: str,
    principal: SessionPrincipal,
    store: SessionTokenStore,
) -> SessionPrincipal:
    """Persist a DB-backed principal for an opaque cookie token."""
    checked_token = _require_identifier(token, field_name="session_token")
    return store.save(checked_token, principal)


def clear_token_principal(token: str | None, store: SessionTokenStore) -> None:
    """Delete a DB-backed principal for an opaque cookie token."""
    if token is None or token == "":
        return
    store.delete(_require_identifier(token, field_name="session_token"))


def principal_template_context(principal: SessionPrincipal | None) -> dict[str, object]:
    """Return a small template context for layouts."""
    return {
        "current_user": principal,
        "is_authenticated": principal is not None,
    }


def _frozen_claims(
    claims: Mapping[str, SessionClaimValue],
) -> Mapping[str, SessionClaimValue]:
    frozen: dict[str, SessionClaimValue] = {}
    for key, value in claims.items():
        _ = _require_identifier(key, field_name="claim")
        _ = _require_claim_value(value, field_name=f"claims.{key}")
        frozen[key] = value
    return MappingProxyType(frozen)


def _required_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise InvalidSessionPrincipalError(field_name, "must be a string")
    return value


def _optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name=field_name)


def _string_items(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise InvalidSessionPrincipalError(field_name, "must be a list")
    values = cast("list[object] | tuple[object, ...]", value)
    items: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise InvalidSessionPrincipalError(field_name, "items must be strings")
        items.append(item)
    return tuple(items)


def _external_identity_items(value: object) -> frozenset[ExternalIdentity]:
    if value is None:
        return frozenset()
    if not isinstance(value, list | tuple):
        field_name = "external_identities"
        reason = "must be a list"
        raise InvalidSessionPrincipalError(field_name, reason)
    values = cast("list[object] | tuple[object, ...]", value)
    identities: list[ExternalIdentity] = []
    for item in values:
        if not isinstance(item, Mapping):
            field_name = "external_identities"
            reason = "items must be objects"
            raise InvalidSessionPrincipalError(
                field_name,
                reason,
            )
        identity_payload = cast("Mapping[str, object]", item)
        identities.append(
            ExternalIdentity(
                provider=_required_string(
                    identity_payload.get("provider"),
                    field_name="provider",
                ),
                subject=_required_string(
                    identity_payload.get("subject"),
                    field_name="subject",
                ),
            ),
        )
    return frozenset(identities)


def _claim_items(value: object) -> Mapping[str, SessionClaimValue]:
    if value is None:
        return _empty_claims()
    if not isinstance(value, Mapping):
        field_name = "claims"
        reason = "must be an object"
        raise InvalidSessionPrincipalError(field_name, reason)
    claim_payload = cast("Mapping[object, object]", value)
    claims: dict[str, SessionClaimValue] = {}
    for key, claim_value in claim_payload.items():
        if not isinstance(key, str):
            field_name = "claims"
            reason = "keys must be strings"
            raise InvalidSessionPrincipalError(field_name, reason)
        claims[key] = _require_claim_value(
            claim_value,
            field_name=f"claims.{key}",
        )
    return MappingProxyType(claims)


def _require_identifier(value: str, *, field_name: str) -> str:
    try:
        return validate_identifier(value, field_name=field_name)
    except ValidationError as exc:
        raise InvalidSessionPrincipalError(field_name, exc.reason) from exc


def _require_text(value: str, *, field_name: str) -> str:
    if value == "":
        raise InvalidSessionPrincipalError(field_name, "must not be empty")
    if value != value.strip():
        raise InvalidSessionPrincipalError(
            field_name,
            "must not have leading or trailing whitespace",
        )
    if any(character in value for character in "\r\n\t"):
        raise InvalidSessionPrincipalError(
            field_name,
            "must not contain control whitespace",
        )
    return value


def _require_claim_value(value: object, *, field_name: str) -> SessionClaimValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise InvalidSessionPrincipalError(
        field_name,
        "must be a string, number, boolean, or null",
    )
