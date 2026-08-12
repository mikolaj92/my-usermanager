"""Safe framework-neutral user management facade."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from datetime import date

from my_usermanager.last_admin import (
    AdminAccessPredicate,
    ensure_account_deactivation_allowed,
)
from my_usermanager.models import (
    Gender,
    Grant,
    Permission,
    Scope,
    User,
    validate_birth_date,
    validate_gender,
    validate_identifier,
)
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import (
    DuplicateUsernameError,
    GrantStore,
    RoleStore,
    UserNotFoundError,
    UserStore,
)

__all__ = [
    "AccountTransitionError",
    "AuthorizationError",
    "PermissionGrantRequest",
    "RoleGrantRequest",
    "UserManager",
    "UserProfileUpdate",
]

ADMIN_ACCESS_PERMISSION = Permission("admin.access")
PROFILE_UPDATE_ACTION = "profile.update"
ROLE_ASSIGN_PERMISSION = Permission("roles.assign")
PERMISSION_GRANT_PERMISSION = Permission("permissions.grant")
PERMISSION_REVOKE_PERMISSION = Permission("permissions.revoke")


@dataclass(frozen=True, slots=True)
class AccountTransitionError(ValueError):
    """Raised when an account lifecycle transition is illegal."""

    current: str
    requested: str

    @override
    def __str__(self) -> str:
        """Return the rejected transition."""
        return f"illegal account transition: {self.current} -> {self.requested}"


@dataclass(frozen=True, slots=True)
class AuthorizationError(PermissionError):
    """Raised when an actor is not allowed to perform an action."""

    actor_id: str
    action: str
    target_id: str

    @override
    def __str__(self) -> str:
        """Return a stable message suitable for audit logs and tests."""
        return (
            f"actor {self.actor_id!r} is not authorized to perform "
            f"{self.action!r} on {self.target_id!r}"
        )


@dataclass(frozen=True, slots=True)
class UserProfileUpdate:
    """Replacement values for the user-editable profile fields.

    ``username`` is always required (public handle; passkey is the secret).
    ``birth_date`` and ``gender`` are optional.
    """

    username: str
    first_name: str = ""
    last_name: str = ""
    display_name: str | None = None
    email: str | None = None
    birth_date: date | None = None
    gender: Gender | None = None

    def __post_init__(self) -> None:
        """Validate profile values that have public validators."""
        _ = validate_identifier(self.username, field_name="username")
        _ = validate_birth_date(self.birth_date)
        _ = validate_gender(self.gender)


@dataclass(frozen=True, slots=True)
class RoleGrantRequest:
    """Request to grant or revoke a role for a target user."""

    target_user_id: str
    role_name: str
    scope: Scope

    def __post_init__(self) -> None:
        """Validate target user and role identifiers."""
        _ = validate_identifier(self.target_user_id, field_name="target_user_id")
        _ = validate_identifier(self.role_name, field_name="role_name")


@dataclass(frozen=True, slots=True)
class PermissionGrantRequest:
    """Request to grant or revoke a direct permission for a target user."""

    target_user_id: str
    permission: Permission
    scope: Scope

    def __post_init__(self) -> None:
        """Validate the target user identifier."""
        _ = validate_identifier(self.target_user_id, field_name="target_user_id")


@dataclass(frozen=True, slots=True)
class UserManager:
    """Safe facade for profile updates and administrator access changes."""

    users: UserStore
    roles: RoleStore
    grants: GrantStore
    admin_predicate: AdminAccessPredicate = field(default_factory=AdminAccessPredicate)
    atomic: Callable[[], AbstractContextManager[object]] | None = None

    def transition_account(self, *, user_id: str, status: str) -> User:
        """Apply one explicit legal account lifecycle transition.

        Disabling or soft-deleting the final active administrator fails closed.
        Provide ``atomic`` (for example SQLite ``BEGIN IMMEDIATE``) so the
        last-admin check and user update commit as one mutation.
        """
        with self._atomic_boundary():
            user = self.users.get(user_id)
            if user is None:
                raise UserNotFoundError(user_id)
            transitions: dict[str, frozenset[str]] = {
                "pending": frozenset({"active", "disabled"}),
                "active": frozenset({"disabled", "deleted"}),
                "disabled": frozenset({"active", "deleted"}),
                "deleted": frozenset(),
            }
            current = user.status or "active"
            if status not in transitions.get(current, frozenset()):
                raise AccountTransitionError(current, status)
            if status in {"disabled", "deleted"}:
                ensure_account_deactivation_allowed(
                    user=user,
                    users=self.users,
                    grants=self.grants,
                    roles=self.roles,
                    action="disable" if status == "disabled" else "delete",
                    predicate=self.admin_predicate,
                )
            return self.users.update(
                replace(
                    user,
                    status=status,
                    disabled=status in {"disabled", "deleted"},
                )
            )

    def _atomic_boundary(self) -> AbstractContextManager[object]:
        if self.atomic is None:
            return nullcontext()
        return self.atomic()

    def soft_delete_account(self, *, user_id: str) -> User:
        """Make an account irreversibly inactive while retaining its audit identity."""
        return self.transition_account(user_id=user_id, status="deleted")

    def hard_delete_account(self, *, user_id: str) -> None:
        """Purge a previously soft-deleted account through a capable store."""
        user = self.users.get(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        if user.status != "deleted":
            raise AccountTransitionError(user.status or "active", "purged")
        delete = getattr(self.users, "delete", None)
        if not callable(delete):
            message = "user store does not support hard deletion"
            raise TypeError(message)
        _ = delete(user_id)

    def update_own_profile(self, *, actor_id: str, update: UserProfileUpdate) -> User:
        """Update the authenticated user's own basic profile fields."""
        return self.update_profile(
            actor_id=actor_id,
            target_user_id=actor_id,
            update=update,
        )

    def update_profile(
        self,
        *,
        actor_id: str,
        target_user_id: str,
        update: UserProfileUpdate,
    ) -> User:
        """Update a profile only when the actor is updating their own user."""
        _ = validate_identifier(actor_id, field_name="actor_id")
        _ = validate_identifier(target_user_id, field_name="target_user_id")
        if actor_id != target_user_id:
            raise AuthorizationError(actor_id, PROFILE_UPDATE_ACTION, target_user_id)
        user = self.users.get(target_user_id)
        if user is None:
            raise UserNotFoundError(target_user_id)
        existing = self.users.get_by_username(update.username)
        if existing is not None and existing.user_id != target_user_id:
            raise DuplicateUsernameError(update.username)
        first_name = update.first_name or None
        last_name = update.last_name or None
        updated = User(
            user_id=user.user_id,
            username=update.username,
            external_identities=user.external_identities,
            first_name=first_name,
            last_name=last_name,
            display_name=update.display_name,
            email=update.email,
            birth_date=update.birth_date,
            gender=update.gender,
            disabled=user.disabled,
            status=user.status,
            system=user.system,
            scope=user.scope,
        )
        return self.users.update(updated)

    def grant_role(self, *, actor_id: str, request: RoleGrantRequest) -> Grant:
        """Grant a role after verifying the actor can assign roles."""
        self._require_permission(
            actor_id=actor_id,
            permission=ROLE_ASSIGN_PERMISSION,
            target_user_id=request.target_user_id,
            scope=request.scope,
        )
        return self.grants.add_role_grant(
            user_id=request.target_user_id,
            role_name=request.role_name,
            scope=request.scope,
        )

    def revoke_role(self, *, actor_id: str, request: RoleGrantRequest) -> Grant:
        """Revoke a role after verifying the actor can assign roles."""
        self._require_permission(
            actor_id=actor_id,
            permission=ROLE_ASSIGN_PERMISSION,
            target_user_id=request.target_user_id,
            scope=request.scope,
        )
        return self.grants.remove_role_grant(
            user_id=request.target_user_id,
            role_name=request.role_name,
            scope=request.scope,
        )

    def grant_permission(
        self,
        *,
        actor_id: str,
        request: PermissionGrantRequest,
    ) -> Grant:
        """Grant a direct permission after verifying grant authority."""
        self._require_permission(
            actor_id=actor_id,
            permission=PERMISSION_GRANT_PERMISSION,
            target_user_id=request.target_user_id,
            scope=request.scope,
        )
        return self.grants.add_permission_grant(
            user_id=request.target_user_id,
            permission=request.permission,
            scope=request.scope,
        )

    def revoke_permission(
        self,
        *,
        actor_id: str,
        request: PermissionGrantRequest,
    ) -> Grant:
        """Revoke a direct permission after verifying revoke authority."""
        self._require_permission(
            actor_id=actor_id,
            permission=PERMISSION_REVOKE_PERMISSION,
            target_user_id=request.target_user_id,
            scope=request.scope,
        )
        return self.grants.remove_permission_grant(
            user_id=request.target_user_id,
            permission=request.permission,
            scope=request.scope,
        )

    def require_permission(
        self,
        *,
        actor_id: str,
        permission: Permission,
        target_user_id: str,
        scope: Scope,
    ) -> None:
        """Require an actor permission for a host/domain lifecycle operation."""
        self._require_permission(
            actor_id=actor_id,
            permission=permission,
            target_user_id=target_user_id,
            scope=scope,
        )

    def _require_permission(
        self,
        *,
        actor_id: str,
        permission: Permission,
        target_user_id: str,
        scope: Scope,
    ) -> None:
        _ = validate_identifier(actor_id, field_name="actor_id")
        _ = validate_identifier(target_user_id, field_name="target_user_id")
        if self._has_permission(actor_id=actor_id, permission=permission, scope=scope):
            return
        raise AuthorizationError(actor_id, permission.name, target_user_id)

    def _has_permission(
        self,
        *,
        actor_id: str,
        permission: Permission,
        scope: Scope,
    ) -> bool:
        for grant in self.grants.list_grants_for_user(actor_id):
            if grant.scope.allows(scope) and self._grant_allows(grant, permission):
                return True
        return False

    def _grant_allows(self, grant: Grant, permission: Permission) -> bool:
        direct_permission = grant.permission
        if direct_permission is not None:
            return direct_permission in {permission, ADMIN_ACCESS_PERMISSION}
        role_name = grant.role_name
        if role_name is None:
            return False
        if role_name == ADMIN_ROLE_NAME:
            return True
        role = self.roles.get(role_name)
        if role is None:
            return False
        return (
            permission in role.permissions
            or ADMIN_ACCESS_PERMISSION in role.permissions
        )
