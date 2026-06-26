"""Synchronous storage contracts for Wave 2 user-manager adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, override, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

from ny_usermanager.models import (
    AuditEvent,
    Grant,
    Permission,
    Role,
    Scope,
    User,
    ValidationError,
)

__all__: Final[tuple[str, ...]] = (
    "AuditFilters",
    "AuditStore",
    "DuplicateAuditEventError",
    "DuplicateGrantError",
    "DuplicateUserError",
    "GrantNotFoundError",
    "GrantStore",
    "InvalidPageError",
    "RoleStore",
    "SessionRevoker",
    "StoreError",
    "UserNotFoundError",
    "UserQuery",
    "UserStore",
)


class StoreError(Exception):
    """Base class for deterministic storage contract failures."""


@dataclass(frozen=True, slots=True)
class InvalidPageError(StoreError):
    """Raised when a store receives invalid pagination parameters."""

    field_name: str
    value: int
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable pagination validation message."""
        return f"{self.field_name}: {self.reason}: {self.value}"


@dataclass(frozen=True, slots=True)
class DuplicateUserError(StoreError):
    """Raised when creating a user that already exists."""

    user_id: str

    @override
    def __str__(self) -> str:
        """Return a stable duplicate-user message."""
        return f"duplicate user: {self.user_id}"


@dataclass(frozen=True, slots=True)
class UserNotFoundError(StoreError):
    """Raised when mutating a user that does not exist."""

    user_id: str

    @override
    def __str__(self) -> str:
        """Return a stable missing-user message."""
        return f"user not found: {self.user_id}"


@dataclass(frozen=True, slots=True)
class DuplicateGrantError(StoreError):
    """Raised when adding a grant that already exists."""

    grant: Grant

    @override
    def __str__(self) -> str:
        """Return a stable duplicate-grant message."""
        return f"duplicate grant: {_grant_message(self.grant)}"


@dataclass(frozen=True, slots=True)
class GrantNotFoundError(StoreError):
    """Raised when removing a grant that does not exist."""

    grant: Grant

    @override
    def __str__(self) -> str:
        """Return a stable missing-grant message."""
        return f"grant not found: {_grant_message(self.grant)}"


@dataclass(frozen=True, slots=True)
class DuplicateAuditEventError(StoreError):
    """Raised when appending an audit event whose event_id already exists."""

    event_id: str

    @override
    def __str__(self) -> str:
        """Return a stable duplicate-audit-event message."""
        return f"duplicate audit event: {self.event_id}"


@dataclass(frozen=True, slots=True)
class UserQuery:
    """Typed filters for user listing without manager policy decisions."""

    text: str | None = None
    disabled: bool | None = None
    system: bool | None = None
    scope: Scope | None = None


@dataclass(frozen=True, slots=True)
class AuditFilters:
    """Typed filters for audit event listing and later audit-reader pages."""

    actor_id: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    result: str | None = None
    request_id: str | None = None
    scope: Scope | None = None
    since: datetime | None = None
    until: datetime | None = None

    def __post_init__(self) -> None:
        """Validate timestamp filters at the typed boundary."""
        if self.since is not None:
            _validate_aware_timestamp(self.since, field_name="since")
        if self.until is not None:
            _validate_aware_timestamp(self.until, field_name="until")
        if (
            self.since is not None
            and self.until is not None
            and self.since > self.until
        ):
            field_name = "audit_filters"
            reason = "since must be before or equal to until"
            raise ValidationError(field_name, reason)


@runtime_checkable
class UserStore(Protocol):
    """Synchronous user storage contract for Wave 2 adapters."""

    def create(self, user: User) -> User:
        """Store a new user or raise DuplicateUserError."""
        ...

    def get(self, user_id: str) -> User | None:
        """Return a user by id or None when missing."""
        ...

    def update(self, user: User) -> User:
        """Replace an existing user or raise UserNotFoundError."""
        ...

    def list(self, *, limit: int, offset: int, query: UserQuery) -> tuple[User, ...]:
        """Return a deterministic page of users matching query filters."""
        ...

    def count_active(self) -> int:
        """Return the number of non-disabled users for later safety checks."""
        ...


@runtime_checkable
class RoleStore(Protocol):
    """Synchronous role catalogue contract for Wave 2 adapters."""

    def get(self, role_name: str) -> Role | None:
        """Return a role by name or None when missing."""
        ...

    def list(self) -> tuple[Role, ...]:
        """Return all roles in deterministic order."""
        ...


@runtime_checkable
class GrantStore(Protocol):
    """Synchronous grant storage contract for role and permission grants."""

    def add_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Store a role grant or raise DuplicateGrantError."""
        ...

    def remove_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Remove a role grant or raise GrantNotFoundError."""
        ...

    def add_permission_grant(
        self,
        user_id: str,
        permission: Permission,
        scope: Scope,
    ) -> Grant:
        """Store a direct permission grant or raise DuplicateGrantError."""
        ...

    def remove_permission_grant(
        self,
        user_id: str,
        permission: Permission,
        scope: Scope,
    ) -> Grant:
        """Remove a direct permission grant or raise GrantNotFoundError."""
        ...

    def list_grants_for_user(self, user_id: str) -> tuple[Grant, ...]:
        """Return all grants for a user in deterministic order."""
        ...


@runtime_checkable
class AuditStore(Protocol):
    """Synchronous append-only audit event storage contract."""

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append an audit event or raise DuplicateAuditEventError."""
        ...

    def list(
        self,
        *,
        limit: int,
        offset: int,
        filters: AuditFilters,
    ) -> tuple[AuditEvent, ...]:
        """Return a deterministic append-order page of audit events."""
        ...


@runtime_checkable
class SessionRevoker(Protocol):
    """Host-provided sync hook for future session revocation."""

    def revoke_sessions(self, user_id: str) -> None:
        """Revoke host-owned sessions for a user without storing tokens here."""


def _validate_aware_timestamp(timestamp: datetime, *, field_name: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        reason = "must be timezone-aware"
        raise ValidationError(field_name, reason)


def _grant_message(grant: Grant) -> str:
    role_name = grant.role_name
    if role_name is not None:
        return f"{grant.user_id}:role:{role_name}"
    permission = grant.permission
    if permission is not None:
        return f"{grant.user_id}:permission:{permission.name}"
    return f"{grant.user_id}:invalid-grant"
