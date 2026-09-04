"""Domain invariants that keep at least one active administrator available."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from my_usermanager.claims import ADMIN_ACCESS_PERMISSION
from my_usermanager.models import Grant, Permission, User, validate_identifier
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import UserQuery

if TYPE_CHECKING:
    from collections.abc import Callable

    from my_usermanager.stores import GrantStore, RoleStore, UserStore

__all__: Final[tuple[str, ...]] = (
    "AdminAccessPredicate",
    "LastAdministratorError",
    "count_active_administrators",
    "ensure_account_deactivation_allowed",
    "ensure_admin_revoke_allowed",
    "grants_confer_admin",
    "remaining_grants_after",
)

_LAST_ADMIN_REASON: Final = "cannot remove the last active admin"


@dataclass(frozen=True, slots=True)
class LastAdministratorError(PermissionError):
    """Raised when a mutation would leave zero active administrators."""

    user_id: str
    action: str
    reason: str = _LAST_ADMIN_REASON

    @override
    def __str__(self) -> str:
        """Return a stable audit-friendly message."""
        return f"{self.reason}: action={self.action} user={self.user_id}"


@dataclass(frozen=True, slots=True)
class AdminAccessPredicate:
    """Host-declared definition of which grants confer administrative access.

    A grant qualifies only when it is global and matches one of:

    - a direct permission in ``permissions``
    - a role name in ``role_names``
    - a stored role that includes any permission in ``permissions``

    Defaults match the built-in ``admin`` role and ``admin.access`` permission.
    Hosts with custom administrative catalogs should construct an explicit
    predicate and pass it to ``UserManager`` / ``GrantAdminService``.
    """

    role_names: frozenset[str] = frozenset({ADMIN_ROLE_NAME})
    permissions: frozenset[Permission] = frozenset({ADMIN_ACCESS_PERMISSION})

    def grant_qualifies(self, grant: Grant, *, roles: RoleStore) -> bool:
        """Return whether one grant confers global administrative access."""
        if not grant.scope.is_global():
            return False
        permission = grant.permission
        if permission is not None:
            return permission in self.permissions
        role_name = grant.role_name
        if role_name is None:
            return False
        if role_name in self.role_names:
            return True
        role = roles.get(role_name)
        return role is not None and any(
            permission in role.permissions for permission in self.permissions
        )

    def grants_qualify(
        self,
        grants: tuple[Grant, ...],
        *,
        roles: RoleStore,
    ) -> bool:
        """Return whether any grant in the collection confers admin access."""
        return any(self.grant_qualifies(grant, roles=roles) for grant in grants)


def grants_confer_admin(
    grants: tuple[Grant, ...],
    *,
    roles: RoleStore,
    predicate: AdminAccessPredicate | None = None,
) -> bool:
    """Return whether grants include at least one qualifying admin grant."""
    access = AdminAccessPredicate() if predicate is None else predicate
    return access.grants_qualify(grants, roles=roles)


def remaining_grants_after(
    grants: GrantStore,
    removed_grant: Grant,
) -> tuple[Grant, ...]:
    """Return a user's grants as they would exist after removing one grant."""
    return tuple(
        grant
        for grant in grants.list_grants_for_user(removed_grant.user_id)
        if grant != removed_grant
    )


def count_active_administrators(  # noqa: PLR0913
    *,
    users: UserStore,
    grants: GrantStore,
    roles: RoleStore,
    predicate: AdminAccessPredicate | None = None,
    exclude_user_id: str | None = None,
    grants_for_user: tuple[str, tuple[Grant, ...]] | None = None,
) -> int:
    """Count active users that currently hold qualifying administrative access.

    Pending, disabled, and deleted users never count. Scoped grants never count.
    ``grants_for_user`` overrides the stored grant list for one user so callers
    can evaluate a revoke before mutating storage.
    """
    access = AdminAccessPredicate() if predicate is None else predicate
    excluded = (
        None
        if exclude_user_id is None
        else validate_identifier(exclude_user_id, field_name="user_id")
    )
    limit = max(users.count_active(), 1)
    active_users = users.list(
        limit=limit,
        offset=0,
        query=UserQuery(status="active"),
    )
    count = 0
    for user in active_users:
        if not user.is_active:
            continue
        if excluded is not None and user.user_id == excluded:
            continue
        if grants_for_user is not None and user.user_id == grants_for_user[0]:
            user_grants = grants_for_user[1]
        else:
            user_grants = grants.list_grants_for_user(user.user_id)
        if access.grants_qualify(user_grants, roles=roles):
            count += 1
    return count


def ensure_account_deactivation_allowed(  # noqa: PLR0913
    *,
    user: User,
    users: UserStore,
    grants: GrantStore,
    roles: RoleStore,
    action: str,
    predicate: AdminAccessPredicate | None = None,
) -> None:
    """Reject disabling or soft-deleting the final active administrator."""
    if not user.is_active:
        return
    access = AdminAccessPredicate() if predicate is None else predicate
    user_grants = grants.list_grants_for_user(user.user_id)
    if not access.grants_qualify(user_grants, roles=roles):
        return
    remaining = count_active_administrators(
        users=users,
        grants=grants,
        roles=roles,
        predicate=access,
        exclude_user_id=user.user_id,
    )
    if remaining == 0:
        raise LastAdministratorError(user_id=user.user_id, action=action)


def ensure_admin_revoke_allowed(  # noqa: PLR0913
    *,
    actor_id: str,
    grant: Grant,
    users: UserStore,
    grants: GrantStore,
    roles: RoleStore,
    predicate: AdminAccessPredicate | None = None,
    on_unsafe: Callable[[str, str, Grant, str], Exception] | None = None,
) -> None:
    """Reject revoking a grant when it would remove the final active admin.

    ``on_unsafe`` lets ``GrantAdminService`` preserve its existing
    ``UnsafeGrantMutationError`` shape while sharing the domain check.
    """
    access = AdminAccessPredicate() if predicate is None else predicate
    if not access.grant_qualifies(grant, roles=roles):
        return
    remaining_for_target = remaining_grants_after(grants, grant)
    if access.grants_qualify(remaining_for_target, roles=roles):
        return
    actor = validate_identifier(actor_id, field_name="actor_id")
    remaining_admins = count_active_administrators(
        users=users,
        grants=grants,
        roles=roles,
        predicate=access,
        grants_for_user=(grant.user_id, remaining_for_target),
    )
    if remaining_admins == 0:
        if on_unsafe is not None:
            raise on_unsafe(actor, grant.user_id, grant, _LAST_ADMIN_REASON)
        raise LastAdministratorError(user_id=grant.user_id, action="revoke")
