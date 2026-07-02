"""Admin grant-management service helpers shared by host applications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, override

from my_usermanager.manager import (
    PermissionGrantRequest,
    RoleGrantRequest,
    UserManager,
)
from my_usermanager.models import Grant, Permission, Scope, User, ValidationError
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import (
    DuplicateGrantError,
    GrantNotFoundError,
    UserNotFoundError,
    UserQuery,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__: Final[tuple[str, ...]] = (
    "AdminGrantService",
    "GrantChange",
    "SelfDemotionError",
    "UserAccessSummary",
)

GrantAction = Literal["grant", "revoke"]

_DUPLICATE_GRANT_REASON: Final = "duplicate-grant"
_GRANT_NOT_FOUND_REASON: Final = "grant-not-found"


@dataclass(frozen=True, slots=True)
class SelfDemotionError(PermissionError):
    """Raised when an actor tries to revoke their own admin role."""

    actor_id: str
    role_name: str

    @override
    def __str__(self) -> str:
        """Return a stable message suitable for audit logs and tests."""
        return (
            f"actor {self.actor_id!r} may not revoke their own {self.role_name!r} role"
        )


@dataclass(frozen=True, slots=True)
class GrantChange:
    """Auditable outcome of one grant or revoke attempt."""

    action: GrantAction
    grant: Grant
    changed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class UserAccessSummary:
    """Snapshot of one user's grants and projected claims for admin UIs."""

    user: User
    grants: tuple[Grant, ...]
    role_names: tuple[str, ...]
    direct_permissions: tuple[Permission, ...]
    claims: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdminGrantService:
    """Reusable admin grant-management helpers on top of UserManager rules."""

    manager: UserManager

    def list_user_access(
        self,
        *,
        limit: int,
        offset: int,
        query: UserQuery | None = None,
    ) -> tuple[UserAccessSummary, ...]:
        """Return a page of users with their grants and projected claims."""
        user_query = UserQuery() if query is None else query
        users = self.manager.users.list(limit=limit, offset=offset, query=user_query)
        return tuple(self._summarize(user) for user in users)

    def user_access(self, user_id: str) -> UserAccessSummary:
        """Return one user's grants and projected claims."""
        user = self.manager.users.get(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return self._summarize(user)

    def grant_role(self, *, actor_id: str, request: RoleGrantRequest) -> GrantChange:
        """Grant a role, treating an already-present grant as a no-op."""
        try:
            grant = self.manager.grant_role(actor_id=actor_id, request=request)
        except DuplicateGrantError as error:
            return GrantChange(
                action="grant",
                grant=error.grant,
                changed=False,
                reason=_DUPLICATE_GRANT_REASON,
            )
        return GrantChange(action="grant", grant=grant, changed=True)

    def revoke_role(self, *, actor_id: str, request: RoleGrantRequest) -> GrantChange:
        """Revoke a role with self-demotion protection for the admin role."""
        if request.role_name == ADMIN_ROLE_NAME and actor_id == request.target_user_id:
            raise SelfDemotionError(actor_id, request.role_name)
        try:
            grant = self.manager.revoke_role(actor_id=actor_id, request=request)
        except GrantNotFoundError as error:
            return GrantChange(
                action="revoke",
                grant=error.grant,
                changed=False,
                reason=_GRANT_NOT_FOUND_REASON,
            )
        return GrantChange(action="revoke", grant=grant, changed=True)

    def grant_permission(
        self,
        *,
        actor_id: str,
        request: PermissionGrantRequest,
    ) -> GrantChange:
        """Grant a permission, treating an already-present grant as a no-op."""
        try:
            grant = self.manager.grant_permission(actor_id=actor_id, request=request)
        except DuplicateGrantError as error:
            return GrantChange(
                action="grant",
                grant=error.grant,
                changed=False,
                reason=_DUPLICATE_GRANT_REASON,
            )
        return GrantChange(action="grant", grant=grant, changed=True)

    def revoke_permission(
        self,
        *,
        actor_id: str,
        request: PermissionGrantRequest,
    ) -> GrantChange:
        """Revoke a permission, treating a missing grant as a no-op."""
        try:
            grant = self.manager.revoke_permission(actor_id=actor_id, request=request)
        except GrantNotFoundError as error:
            return GrantChange(
                action="revoke",
                grant=error.grant,
                changed=False,
                reason=_GRANT_NOT_FOUND_REASON,
            )
        return GrantChange(action="revoke", grant=grant, changed=True)

    def set_cumulative_permissions(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        ordered_permissions: Sequence[Permission],
        count: int,
        scope: Scope,
    ) -> tuple[GrantChange, ...]:
        """Grant the first ``count`` levelled permissions and revoke the rest."""
        if count < 0 or count > len(ordered_permissions):
            field_name = "count"
            reason = "must be between zero and the number of ordered permissions"
            raise ValidationError(field_name, reason)
        changes: list[GrantChange] = []
        for index, permission in enumerate(ordered_permissions):
            request = PermissionGrantRequest(
                target_user_id=target_user_id,
                permission=permission,
                scope=scope,
            )
            if index < count:
                changes.append(
                    self.grant_permission(actor_id=actor_id, request=request),
                )
            else:
                changes.append(
                    self.revoke_permission(actor_id=actor_id, request=request),
                )
        return tuple(changes)

    def _summarize(self, user: User) -> UserAccessSummary:
        grants = self.manager.grants.list_grants_for_user(user.user_id)
        role_names = tuple(
            grant.role_name for grant in grants if grant.role_name is not None
        )
        direct_permissions = tuple(
            grant.permission for grant in grants if grant.permission is not None
        )
        return UserAccessSummary(
            user=user,
            grants=grants,
            role_names=role_names,
            direct_permissions=direct_permissions,
            claims=self._projected_claims(grants),
        )

    def _projected_claims(self, grants: tuple[Grant, ...]) -> tuple[str, ...]:
        names: set[str] = set()
        for grant in grants:
            permission = grant.permission
            if permission is not None:
                names.add(permission.name)
                continue
            role_name = grant.role_name
            if role_name is None:
                continue
            role = self.manager.roles.get(role_name)
            if role is None:
                continue
            names.update(role_permission.name for role_permission in role.permissions)
        return tuple(sorted(names))
