"""Safe framework-neutral user management facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from my_usermanager.models import Grant, Permission, Scope, User, validate_identifier
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import GrantStore, RoleStore, UserNotFoundError, UserStore

__all__ = [
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
    """Replacement values for the basic user-editable profile fields."""

    username: str
    first_name: str
    last_name: str
    display_name: str | None = None
    email: str | None = None

    def __post_init__(self) -> None:
        """Validate profile values that have public validators."""
        _ = validate_identifier(self.username, field_name="username")


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
        updated = User(
            user_id=user.user_id,
            external_identities=user.external_identities,
            username=update.username,
            first_name=update.first_name,
            last_name=update.last_name,
            display_name=update.display_name,
            email=update.email,
            disabled=user.disabled,
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
