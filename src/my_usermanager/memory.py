"""Process-local in-memory stores for tests and development."""

from __future__ import annotations

from typing import ClassVar, Final

from my_usermanager.models import (
    AuditEvent,
    Grant,
    Permission,
    Role,
    Scope,
    User,
    validate_identifier,
)
from my_usermanager.permissions import BUILTIN_ROLES
from my_usermanager.stores import (
    AuditFilters,
    DuplicateAuditEventError,
    DuplicateGrantError,
    DuplicateUserError,
    GrantNotFoundError,
    InvalidPageError,
    UserNotFoundError,
    UserQuery,
)

__all__: Final[tuple[str, ...]] = (
    "MemoryAuditStore",
    "MemoryGrantStore",
    "MemoryRoleStore",
    "MemoryUserStore",
)


class MemoryUserStore:
    """Process-local UserStore implementation with no durability guarantees."""

    __slots__: ClassVar[tuple[str, ...]] = ("_users",)

    _users: dict[str, User]

    def __init__(self) -> None:
        """Create an empty process-local user store."""
        self._users = {}

    def create(self, user: User) -> User:
        """Store a new user or raise DuplicateUserError."""
        if user.user_id in self._users:
            raise DuplicateUserError(user.user_id)
        self._users[user.user_id] = user
        return user

    def get(self, user_id: str) -> User | None:
        """Return a user by id or None when missing."""
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        return self._users.get(checked_user_id)

    def update(self, user: User) -> User:
        """Replace an existing user or raise UserNotFoundError."""
        if user.user_id not in self._users:
            raise UserNotFoundError(user.user_id)
        self._users[user.user_id] = user
        return user

    def list(self, *, limit: int, offset: int, query: UserQuery) -> tuple[User, ...]:
        """Return users sorted by user_id after applying query filters."""
        _validate_page(limit=limit, offset=offset)
        matching_users = (
            user for user in self._users.values() if _matches_user_query(user, query)
        )
        ordered_users = sorted(matching_users, key=lambda user: user.user_id)
        return tuple(ordered_users[offset : offset + limit])

    def count_active(self) -> int:
        """Return the number of non-disabled users."""
        return sum(1 for user in self._users.values() if not user.disabled)


class MemoryRoleStore:
    """Process-local RoleStore exposing the exact Wave 1 built-in roles."""

    __slots__: ClassVar[tuple[str, ...]] = ("_roles",)

    _roles: dict[str, Role]

    def __init__(self) -> None:
        """Create a role store containing only built-in roles."""
        self._roles = dict(BUILTIN_ROLES)

    def get(self, role_name: str) -> Role | None:
        """Return a role by name or None when missing."""
        checked_role_name = validate_identifier(role_name, field_name="role_name")
        return self._roles.get(checked_role_name)

    def list(self) -> tuple[Role, ...]:
        """Return built-in roles sorted by role name."""
        return tuple(sorted(self._roles.values(), key=lambda role: role.name))


class MemoryGrantStore:
    """Process-local GrantStore implementation with deterministic ordering."""

    __slots__: ClassVar[tuple[str, ...]] = ("_grants",)

    _grants: set[Grant]

    def __init__(self) -> None:
        """Create an empty process-local grant store."""
        self._grants = set()

    def add_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Store a role grant or raise DuplicateGrantError."""
        grant = Grant.for_role(user_id, role_name, scope)
        self._add_grant(grant)
        return grant

    def remove_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Remove a role grant or raise GrantNotFoundError."""
        grant = Grant.for_role(user_id, role_name, scope)
        self._remove_grant(grant)
        return grant

    def add_permission_grant(
        self,
        user_id: str,
        permission: Permission,
        scope: Scope,
    ) -> Grant:
        """Store a direct permission grant or raise DuplicateGrantError."""
        grant = Grant.for_permission(user_id, permission, scope)
        self._add_grant(grant)
        return grant

    def remove_permission_grant(
        self,
        user_id: str,
        permission: Permission,
        scope: Scope,
    ) -> Grant:
        """Remove a direct permission grant or raise GrantNotFoundError."""
        grant = Grant.for_permission(user_id, permission, scope)
        self._remove_grant(grant)
        return grant

    def list_grants_for_user(self, user_id: str) -> tuple[Grant, ...]:
        """Return grants for a user sorted by scope and target."""
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        grants = (grant for grant in self._grants if grant.user_id == checked_user_id)
        return tuple(sorted(grants, key=_grant_sort_key))

    def _add_grant(self, grant: Grant) -> None:
        if grant in self._grants:
            raise DuplicateGrantError(grant)
        self._grants.add(grant)

    def _remove_grant(self, grant: Grant) -> None:
        if grant not in self._grants:
            raise GrantNotFoundError(grant)
        self._grants.remove(grant)


class MemoryAuditStore:
    """Process-local AuditStore preserving append order without persistence."""

    __slots__: ClassVar[tuple[str, ...]] = ("_event_ids", "_events")

    _event_ids: set[str]
    _events: list[AuditEvent]

    def __init__(self) -> None:
        """Create an empty process-local audit store."""
        self._event_ids = set()
        self._events = []

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append an audit event or raise DuplicateAuditEventError."""
        if event.event_id in self._event_ids:
            raise DuplicateAuditEventError(event.event_id)
        self._event_ids.add(event.event_id)
        self._events.append(event)
        return event

    def list(
        self,
        *,
        limit: int,
        offset: int,
        filters: AuditFilters,
    ) -> tuple[AuditEvent, ...]:
        """Return append-ordered audit events after applying filters."""
        _validate_page(limit=limit, offset=offset)
        matching_events = (
            event for event in self._events if _matches_audit_filters(event, filters)
        )
        events = tuple(matching_events)
        return events[offset : offset + limit]


def _validate_page(*, limit: int, offset: int) -> None:
    if limit < 0:
        field_name = "limit"
        reason = "must be greater than or equal to zero"
        raise InvalidPageError(field_name, limit, reason)
    if offset < 0:
        field_name = "offset"
        reason = "must be greater than or equal to zero"
        raise InvalidPageError(field_name, offset, reason)


def _matches_user_query(user: User, query: UserQuery) -> bool:
    if query.disabled is not None and user.disabled != query.disabled:
        return False
    if query.system is not None and user.system != query.system:
        return False
    if query.scope is not None and user.scope != query.scope:
        return False
    if query.text is None:
        return True
    needle = query.text.casefold()
    searchable_fields = (
        user.user_id,
        user.username or "",
        user.first_name or "",
        user.last_name or "",
        user.display_name or "",
        user.email or "",
    )
    return any(needle in field.casefold() for field in searchable_fields)


def _grant_sort_key(grant: Grant) -> tuple[str, str, str, str, str]:
    scope_type = "" if grant.scope.scope_type is None else grant.scope.scope_type
    scope_id = "" if grant.scope.scope_id is None else grant.scope.scope_id
    role_name = grant.role_name
    if role_name is not None:
        return (grant.user_id, scope_type, scope_id, "role", role_name)
    permission = grant.permission
    if permission is not None:
        return (grant.user_id, scope_type, scope_id, "permission", permission.name)
    return (grant.user_id, scope_type, scope_id, "invalid", "")


def _matches_audit_filters(event: AuditEvent, filters: AuditFilters) -> bool:
    return all(
        (
            filters.actor_id is None or event.actor_id == filters.actor_id,
            filters.action is None or event.action == filters.action,
            filters.target_type is None or event.target_type == filters.target_type,
            filters.target_id is None or event.target_id == filters.target_id,
            filters.result is None or event.result == filters.result,
            filters.request_id is None or event.request_id == filters.request_id,
            filters.scope is None or event.scope == filters.scope,
            filters.since is None or event.timestamp >= filters.since,
            filters.until is None or event.timestamp <= filters.until,
        ),
    )
