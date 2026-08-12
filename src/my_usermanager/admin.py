"""Admin helpers for listing and mutating user grants."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, override

from my_usermanager.claims import GrantClaims, GrantClaimsProjector
from my_usermanager.last_admin import (
    AdminAccessPredicate,
    ensure_admin_revoke_allowed,
)
from my_usermanager.models import Grant, Permission, Scope, User, validate_identifier
from my_usermanager.stores import GrantStore, RoleStore, UserQuery, UserStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

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
    admin_predicate: AdminAccessPredicate = field(default_factory=AdminAccessPredicate)
    atomic: Callable[[], AbstractContextManager[object]] | None = None

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
        with self._atomic_boundary():
            self._check_admin_revoke_safety(actor_id=actor_id, grant=grant)
            revoked = self.grants.remove_role_grant(
                target_user_id,
                role_name,
                grant_scope,
            )
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
        with self._atomic_boundary():
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
        ensure_admin_revoke_allowed(
            actor_id=actor_id,
            grant=grant,
            users=self.users,
            grants=self.grants,
            roles=self.roles,
            predicate=self.admin_predicate,
            on_unsafe=UnsafeGrantMutationError,
        )

    def _atomic_boundary(self) -> AbstractContextManager[object]:
        if self.atomic is None:
            return nullcontext()
        return self.atomic()

    def _projector(self) -> GrantClaimsProjector:
        if self.projector is not None:
            return self.projector
        return GrantClaimsProjector(roles=self.roles, grants=self.grants)


def _scope_or_global(scope: Scope | None) -> Scope:
    return Scope.global_() if scope is None else scope


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
