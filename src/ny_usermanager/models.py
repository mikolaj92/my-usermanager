"""Framework-neutral domain values for users, roles, grants, and audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from re import Pattern
from re import compile as compile_pattern
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Self, override

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime


__all__: Final[tuple[str, ...]] = (
    "AuditEvent",
    "ExternalIdentity",
    "Grant",
    "Permission",
    "Role",
    "Scope",
    "User",
    "ValidationError",
    "is_valid_permission_name",
    "validate_identifier",
    "validate_permission_name",
)

_PERMISSION_NAME_PATTERN: Final[Pattern[str]] = compile_pattern(
    r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+",
)


@dataclass(frozen=True, slots=True)
class ValidationError(ValueError):
    """Raised when a public value object receives malformed input."""

    field_name: str
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable human-readable validation message."""
        return f"{self.field_name}: {self.reason}"


def validate_identifier(value: str, *, field_name: str) -> str:
    """Validate a non-empty identifier-like value and return it unchanged."""
    if value == "":
        reason = "must not be empty"
        raise ValidationError(field_name, reason)
    if value != value.strip():
        reason = "must not have leading or trailing whitespace"
        raise ValidationError(field_name, reason)
    if any(character.isspace() for character in value):
        reason = "must not contain whitespace"
        raise ValidationError(field_name, reason)
    return value


def is_valid_permission_name(name: str) -> bool:
    """Return whether a permission name is a valid namespaced string."""
    return _PERMISSION_NAME_PATTERN.fullmatch(name) is not None


def validate_permission_name(name: str) -> str:
    """Validate a permission name and return it unchanged."""
    if not is_valid_permission_name(name):
        field_name = "permission"
        reason = "must be lowercase namespace segments separated by dots"
        raise ValidationError(
            field_name,
            reason,
        )
    return name


def _validate_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if value == "":
        reason = "must not be empty"
        raise ValidationError(field_name, reason)
    if value != value.strip():
        reason = "must not have leading or trailing whitespace"
        raise ValidationError(field_name, reason)
    if any(character in value for character in "\r\n\t"):
        reason = "must not contain control whitespace"
        raise ValidationError(field_name, reason)
    return value


def _empty_metadata() -> Mapping[str, str]:
    empty: dict[str, str] = {}
    return MappingProxyType(empty)


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """Reference to an identity owned by an external authentication provider."""

    provider: str
    subject: str

    def __post_init__(self) -> None:
        """Validate provider and subject identifiers after dataclass creation."""
        _ = validate_identifier(self.provider, field_name="provider")
        _ = validate_identifier(self.subject, field_name="subject")


@dataclass(frozen=True, slots=True)
class Permission:
    """Stable namespaced permission value."""

    name: str

    def __post_init__(self) -> None:
        """Validate the permission name after dataclass creation."""
        _ = validate_permission_name(self.name)


_EMPTY_IDENTITIES: Final[frozenset[ExternalIdentity]] = frozenset()
_EMPTY_PERMISSIONS: Final[frozenset[Permission]] = frozenset()


@dataclass(frozen=True, slots=True)
class Scope:
    """Authorization scope where None/None represents a global grant."""

    scope_type: str | None = None
    scope_id: str | None = None

    def __post_init__(self) -> None:
        """Validate global or fully-populated scoped values."""
        has_scope_type = self.scope_type is not None
        has_scope_id = self.scope_id is not None
        if has_scope_type != has_scope_id:
            field_name = "scope"
            reason = "scope_type and scope_id must both be set or both be None"
            raise ValidationError(
                field_name,
                reason,
            )
        if self.scope_type is not None and self.scope_id is not None:
            _ = validate_identifier(self.scope_type, field_name="scope_type")
            _ = validate_identifier(self.scope_id, field_name="scope_id")

    @classmethod
    def global_(cls) -> Self:
        """Create the global None/None scope."""
        return cls()

    @classmethod
    def scoped(cls, scope_type: str, scope_id: str) -> Self:
        """Create a non-global scope with both scope components populated."""
        return cls(scope_type=scope_type, scope_id=scope_id)

    def is_global(self) -> bool:
        """Return whether this scope is global."""
        return self.scope_type is None and self.scope_id is None

    def allows(self, requested_scope: Scope) -> bool:
        """Return whether this grant scope authorizes the requested scope."""
        return self.is_global() or self == requested_scope


@dataclass(frozen=True, slots=True)
class User:
    """Stored user profile whose authentication is owned by the host app."""

    user_id: str
    external_identities: frozenset[ExternalIdentity] = _EMPTY_IDENTITIES
    display_name: str | None = None
    email: str | None = None
    disabled: bool = False
    system: bool = False
    scope: Scope = field(default_factory=Scope.global_)

    def __post_init__(self) -> None:
        """Validate stored profile values after dataclass creation."""
        _ = validate_identifier(self.user_id, field_name="user_id")
        _ = _validate_optional_text(self.display_name, field_name="display_name")
        _ = _validate_optional_text(self.email, field_name="email")


@dataclass(frozen=True, slots=True)
class Role:
    """Named bundle of permissions."""

    name: str
    permissions: frozenset[Permission] = _EMPTY_PERMISSIONS

    def __post_init__(self) -> None:
        """Validate the role name after dataclass creation."""
        _ = validate_identifier(self.name, field_name="role_name")


@dataclass(frozen=True, slots=True)
class Grant:
    """Role or direct permission grant for a user and scope."""

    user_id: str
    role_name: str | None = None
    permission: Permission | None = None
    scope: Scope = field(default_factory=Scope.global_)

    def __post_init__(self) -> None:
        """Validate that the grant targets exactly one role or permission."""
        _ = validate_identifier(self.user_id, field_name="user_id")
        has_role = self.role_name is not None
        has_permission = self.permission is not None
        if has_role == has_permission:
            field_name = "grant"
            reason = "exactly one of role_name or permission must be set"
            raise ValidationError(
                field_name,
                reason,
            )
        if self.role_name is not None:
            _ = validate_identifier(self.role_name, field_name="role_name")

    @classmethod
    def for_role(cls, user_id: str, role_name: str, scope: Scope | None = None) -> Self:
        """Create a role grant for a user."""
        grant_scope = Scope.global_() if scope is None else scope
        return cls(user_id=user_id, role_name=role_name, scope=grant_scope)

    @classmethod
    def for_permission(
        cls,
        user_id: str,
        permission: Permission,
        scope: Scope | None = None,
    ) -> Self:
        """Create a direct permission grant for a user."""
        grant_scope = Scope.global_() if scope is None else scope
        return cls(user_id=user_id, permission=permission, scope=grant_scope)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Append-only audit event for high-risk authorization mutations."""

    event_id: str
    timestamp: datetime
    actor_id: str
    action: str
    target_type: str
    target_id: str
    scope: Scope
    result: str
    reason: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    metadata: Mapping[str, str] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        """Validate audit fields and snapshot caller-owned metadata."""
        _ = validate_identifier(self.event_id, field_name="event_id")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            field_name = "timestamp"
            reason = "must be timezone-aware"
            raise ValidationError(field_name, reason)
        _ = validate_identifier(self.actor_id, field_name="actor_id")
        _ = validate_identifier(self.action, field_name="action")
        _ = validate_identifier(self.target_type, field_name="target_type")
        _ = validate_identifier(self.target_id, field_name="target_id")
        _ = validate_identifier(self.result, field_name="result")
        _ = _validate_optional_text(self.reason, field_name="reason")
        _ = _validate_optional_text(self.request_id, field_name="request_id")
        _ = _validate_optional_text(self.ip_address, field_name="ip_address")
        _ = _validate_optional_text(self.user_agent, field_name="user_agent")
        metadata_snapshot: dict[str, str] = dict(self.metadata)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(metadata_snapshot),
        )
