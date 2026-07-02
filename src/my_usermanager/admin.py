"""Admin helpers for listing and mutating user grants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, override

from my_usermanager.claims import (
    ADMIN_ACCESS_PERMISSION,
    GrantClaims,
    GrantClaimsProjector,
)
from my_usermanager.models import Grant, Permission, Scope, User, validate_identifier
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import GrantStore, RoleStore, UserQuery, UserStore

__all__: Final[tuple[str, ...]] = (
    "AdminGrantOperation",
    "AdminUserGrantSummary",
    "GrantAdminService",
    "UnsafeGrantMutationError",
)

type GrantAction = Literal[
    "grant_role",
    "revoke_role",
    "grant_permission",
    "revoke_permission",
]

_LAST_ADMIN_REASON: Final = "cannot remove the last active admin"
_SELF_DEMOTION_REASON: Final = "cannot remove your own last admin grant"


@dataclass(frozen=True, slots=True)
class UnsafeGrantMutationError(PermissionError):
    """Raised when a grant mutation would leave admin access unsafe."""

    actor_id: str
    target_user_id: str
    grant: Grant
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable audit-friendly message."""
        return (
            f"{self.reason}: actor={self.actor_id} "
            f"target={self.target_user_id} grant={_grant_label(self.grant)}"
        )


@dataclass(frozen=True, slots=True)
class AdminGrantOperation:
    """Successful admin grant mutation result."""

    action: GrantAction
    actor_id: str
    target_user_id: str
    grant: Grant


@dataclass(frozen=True, slots=True)
class AdminUserGrantSummary:
    """User row data useful for simple admin UIs and audit views."""

    user: User
    grants: tuple[Grant, ...]
    projection: GrantClaims


@dataclass(frozen=True, slots=True)
class GrantAdminService:
    """Small service for admin grant lists and safe grant mutations."""

    users: UserStore
    roles: RoleStore
    grants: GrantStore
    projector: GrantClaimsProjector | None = None

    def list_users(
        self,
        *,
        limit: int,
        offset: int = 0,
        query: UserQuery | None = None,
    ) -> tuple[AdminUserGrantSummary, ...]:
        """List users with grants and projected session claims."""
        user_query = UserQuery() if query is None else query
        users = self.users.list(limit=limit, offset=offset, query=user_query)
        return tuple(self.summary_for_user(user) for user in users)

    def summary_for_user(self, user: User) -> AdminUserGrantSummary:
        """Return grants and projected claims for one user."""
        grants = self.grants.list_grants_for_user(user.user_id)
        projection = self._projector().project(user.user_id)
        return AdminUserGrantSummary(user=user, grants=grants, projection=projection)

    def grant_role(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        role_name: str,
        scope: Scope | None = None,
    ) -> AdminGrantOperation:
        """Grant a role and return an auditable success result."""
        grant_scope = _scope_or_global(scope)
        grant = self.grants.add_role_grant(target_user_id, role_name, grant_scope)
        return self._operation("grant_role", actor_id, target_user_id, grant)

    def revoke_role(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        role_name: str,
        scope: Scope | None = None,
    ) -> AdminGrantOperation:
        """Revoke a role after checking admin self/last-admin safety."""
        grant_scope = _scope_or_global(scope)
        grant = Grant.for_role(target_user_id, role_name, grant_scope)
        self._check_admin_revoke_safety(actor_id=actor_id, grant=grant)
        revoked = self.grants.remove_role_grant(target_user_id, role_name, grant_scope)
        return self._operation("revoke_role", actor_id, target_user_id, revoked)

    def grant_permission(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        permission: Permission,
        scope: Scope | None = None,
    ) -> AdminGrantOperation:
        """Grant a direct permission and return an auditable success result."""
        grant_scope = _scope_or_global(scope)
        grant = self.grants.add_permission_grant(
            target_user_id,
            permission,
            grant_scope,
        )
        return self._operation("grant_permission", actor_id, target_user_id, grant)

    def revoke_permission(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        permission: Permission,
        scope: Scope | None = None,
    ) -> AdminGrantOperation:
        """Revoke a direct permission after checking admin safety."""
        grant_scope = _scope_or_global(scope)
        grant = Grant.for_permission(target_user_id, permission, grant_scope)
        self._check_admin_revoke_safety(actor_id=actor_id, grant=grant)
        revoked = self.grants.remove_permission_grant(
            target_user_id,
            permission,
            grant_scope,
        )
        return self._operation("revoke_permission", actor_id, target_user_id, revoked)

    def _operation(
        self,
        action: GrantAction,
        actor_id: str,
        target_user_id: str,
        grant: Grant,
    ) -> AdminGrantOperation:
        return AdminGrantOperation(
            action=action,
            actor_id=validate_identifier(actor_id, field_name="actor_id"),
            target_user_id=validate_identifier(
                target_user_id,
                field_name="target_user_id",
            ),
            grant=grant,
        )

    def _check_admin_revoke_safety(self, *, actor_id: str, grant: Grant) -> None:
        if not _is_global_admin_grant(grant, role_store=self.roles):
            return
        if _grants_make_global_admin(
            _remaining_grants(self.grants, grant),
            role_store=self.roles,
        ):
            return
        actor = validate_identifier(actor_id, field_name="actor_id")
        if actor == grant.user_id:
            raise UnsafeGrantMutationError(
                actor_id=actor,
                target_user_id=grant.user_id,
                grant=grant,
                reason=_SELF_DEMOTION_REASON,
            )
        if self._active_admin_count_after(grant) == 0:
            raise UnsafeGrantMutationError(
                actor_id=actor,
                target_user_id=grant.user_id,
                grant=grant,
                reason=_LAST_ADMIN_REASON,
            )

    def _active_admin_count_after(self, removed_grant: Grant) -> int:
        limit = max(self.users.count_active(), 1)
        active_users = self.users.list(
            limit=limit,
            offset=0,
            query=UserQuery(disabled=False),
        )
        count = 0
        for user in active_users:
            if user.user_id == removed_grant.user_id:
                grants = _remaining_grants(self.grants, removed_grant)
            else:
                grants = self.grants.list_grants_for_user(user.user_id)
            if _grants_make_global_admin(grants, role_store=self.roles):
                count += 1
        return count

    def _projector(self) -> GrantClaimsProjector:
        if self.projector is not None:
            return self.projector
        return GrantClaimsProjector(roles=self.roles, grants=self.grants)


def _remaining_grants(grants: GrantStore, removed_grant: Grant) -> tuple[Grant, ...]:
    return tuple(
        grant
        for grant in grants.list_grants_for_user(removed_grant.user_id)
        if grant != removed_grant
    )


def _scope_or_global(scope: Scope | None) -> Scope:
    return Scope.global_() if scope is None else scope


def _grants_make_global_admin(
    grants: tuple[Grant, ...],
    *,
    role_store: RoleStore,
) -> bool:
    return any(_is_global_admin_grant(grant, role_store=role_store) for grant in grants)


def _is_global_admin_grant(grant: Grant, *, role_store: RoleStore) -> bool:
    if not grant.scope.is_global():
        return False
    if grant.permission == ADMIN_ACCESS_PERMISSION:
        return True
    role_name = grant.role_name
    if role_name is None:
        return False
    if role_name == ADMIN_ROLE_NAME:
        return True
    role = role_store.get(role_name)
    return role is not None and ADMIN_ACCESS_PERMISSION in role.permissions


def _grant_label(grant: Grant) -> str:
    if grant.role_name is not None:
        target = f"role:{grant.role_name}"
    elif grant.permission is not None:
        target = f"permission:{grant.permission.name}"
    else:
        target = "invalid"
    scope = (
        "global"
        if grant.scope.is_global()
        else (f"{grant.scope.scope_type}:{grant.scope.scope_id}")
    )
    return f"{grant.user_id}:{target}:{scope}"
