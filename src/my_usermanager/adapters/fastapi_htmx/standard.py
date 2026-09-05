"""Mechanical ``UserManager``-backed hooks for the packaged UI.

Hosts still provide session lookup, administrator policy, the role catalogue,
CSRF configuration, invitations, auditing, and product side effects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from my_usermanager.adapters.fastapi_htmx.config import (
    CapabilityOption,
    CsrfContext,
    ExternalIdentityRow,
    PasskeyPanel,
    PermissionGrantRow,
    UserRow,
)
from my_usermanager.adapters.fastapi_htmx.ids import row_key_from_user_id
from my_usermanager.manager import (
    PermissionGrantRequest,
    RoleGrantRequest,
    UserManager,
    UserProfileUpdate,
)
from my_usermanager.models import Permission, Scope, User
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import UserNotFoundError, UserQuery
from my_usermanager.subjects import AuthenticatedSubject

CurrentUser = Callable[[Request], AuthenticatedSubject | None]
RequireAdmin = Callable[[Request, AuthenticatedSubject], None]
AfterDisabled = Callable[[Request, AuthenticatedSubject, UserRow], None]
PasskeyPanelProvider = Callable[[Request, AuthenticatedSubject], PasskeyPanel | None]


@dataclass(slots=True)
class StandardUserManagerUiHooks:
    """Implement routine UI hooks using the public ``UserManager`` API.

    This adapter intentionally has no registration, invitation, session, audit,
    or role policy. Hosts add those optional hooks themselves or keep a focused
    subclass when those surfaces are needed.
    """

    manager: UserManager
    current_user: CurrentUser
    require_admin_policy: RequireAdmin
    role_names: tuple[str, ...] = ()
    capabilities: tuple[CapabilityOption, ...] = ()
    page_size: int = 200
    after_disabled: AfterDisabled | None = None
    passkey_panel: PasskeyPanelProvider | None = None

    def __init__(  # noqa: PLR0913
        self,
        *,
        manager: UserManager,
        current_user: CurrentUser,
        require_admin: RequireAdmin,
        role_names: tuple[str, ...] = (),
        capabilities: tuple[CapabilityOption, ...] = (),
        page_size: int = 200,
        after_disabled: AfterDisabled | None = None,
        passkey_panel: PasskeyPanelProvider | None = None,
    ) -> None:
        """Bind standard stores to the small set of host-owned policies."""
        if page_size < 1:
            msg = "page_size must be positive"
            raise ValueError(msg)
        self.manager = manager
        self.current_user = current_user
        self.require_admin_policy = require_admin
        self.role_names = role_names
        self.capabilities = capabilities
        self.page_size = page_size
        self.after_disabled = after_disabled
        self.passkey_panel = passkey_panel

    def get_current_user(self, request: Request) -> AuthenticatedSubject | None:
        """Delegate request/session interpretation to the host."""
        return self.current_user(request)

    def require_admin(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> None:
        """Delegate administrator policy to the host."""
        self.require_admin_policy(request, current_user)

    def list_users(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> tuple[UserRow, ...]:
        """List all manager users as packaged-UI rows."""
        del request, current_user
        users: list[User] = []
        offset = 0
        while True:
            page = self.manager.users.list(
                limit=self.page_size,
                offset=offset,
                query=UserQuery(),
            )
            users.extend(page)
            if len(page) < self.page_size:
                break
            offset += self.page_size
        return tuple(self._row(user) for user in users)

    def role_options(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> tuple[str, ...]:
        """Return the host-approved assignable role catalogue."""
        del request, current_user
        return self.role_names

    def capability_options(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> tuple[CapabilityOption, ...]:
        """Return the host-approved direct-permission catalogue."""
        del request, current_user
        return self.capabilities

    def set_user_disabled(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        disabled: bool,
    ) -> UserRow:
        """Apply the manager's account-transition and last-admin rules."""
        del request, current_user
        user = self.manager.users.get(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        if user.disabled == disabled:
            return self._row(user)
        target_status = "disabled" if disabled else "active"
        return self._row(
            self.manager.transition_account(user_id=user_id, status=target_status)
        )

    def grant_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> UserRow:
        """Grant one global role through ``UserManager`` authorization."""
        del request
        _ = self.manager.grant_role(
            actor_id=current_user.user_id,
            request=RoleGrantRequest(user_id, role_name, Scope.global_()),
        )
        return self._row_for(user_id)

    def revoke_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> UserRow:
        """Revoke one global role through ``UserManager`` authorization."""
        del request
        _ = self.manager.revoke_role(
            actor_id=current_user.user_id,
            request=RoleGrantRequest(user_id, role_name, Scope.global_()),
        )
        return self._row_for(user_id)

    def grant_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> UserRow:
        """Grant one direct permission through ``UserManager`` authorization."""
        del request
        _ = self.manager.grant_permission(
            actor_id=current_user.user_id,
            request=PermissionGrantRequest(
                user_id,
                Permission(permission.permission),
                Scope(permission.scope_type, permission.scope_id),
            ),
        )
        return self._row_for(user_id)

    def revoke_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> UserRow:
        """Revoke one direct permission through ``UserManager`` authorization."""
        del request
        _ = self.manager.revoke_permission(
            actor_id=current_user.user_id,
            request=PermissionGrantRequest(
                user_id,
                Permission(permission.permission),
                Scope(permission.scope_type, permission.scope_id),
            ),
        )
        return self._row_for(user_id)

    def update_own_profile(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        update: object,
    ) -> AuthenticatedSubject:
        """Update a profile through the manager and preserve auth identity."""
        del request
        if not isinstance(update, UserProfileUpdate):
            msg = "update must be UserProfileUpdate"
            raise TypeError(msg)
        user = self.manager.update_own_profile(
            actor_id=current_user.user_id,
            update=update,
        )
        return AuthenticatedSubject(
            provider=current_user.provider,
            subject=current_user.subject,
            user_id=user.user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            display_name=user.display_name,
            email=user.email,
            birth_date=user.birth_date,
            gender=user.gender,
        )

    def csrf_context(self, request: Request) -> CsrfContext:
        """Return no extra fields; ``UserManagerUiConfig`` owns its CSRF token."""
        del request
        return CsrfContext(hidden_inputs=(), headers={})

    def after_user_disabled_changed(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        row: UserRow,
    ) -> None:
        """Run an optional host side effect after the manager mutation."""
        if self.after_disabled is not None:
            self.after_disabled(request, current_user, row)

    def render_passkey_panel(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> PasskeyPanel | None:
        """Render an optional host-selected passkey panel."""
        if self.passkey_panel is None:
            return None
        return self.passkey_panel(request, current_user)

    def _row_for(self, user_id: str) -> UserRow:
        user = self.manager.users.get(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return self._row(user)

    def _row(self, user: User) -> UserRow:
        grants = self.manager.grants.list_grants_for_user(user.user_id)
        roles = tuple(
            sorted(
                grant.role_name
                for grant in grants
                if grant.role_name is not None and grant.scope.is_global()
            )
        )
        permissions = tuple(
            PermissionGrantRow(
                permission=grant.permission.name,
                label=grant.permission.name,
                scope_type=grant.scope.scope_type,
                scope_id=grant.scope.scope_id,
            )
            for grant in grants
            if grant.permission is not None
        )
        return UserRow(
            user_id=user.user_id,
            row_key=row_key_from_user_id(user.user_id),
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            disabled=user.disabled,
            is_admin=ADMIN_ROLE_NAME in roles,
            roles=roles,
            permissions=permissions,
            external_identities=tuple(
                ExternalIdentityRow(item.provider, item.subject)
                for item in sorted(
                    user.external_identities,
                    key=lambda item: (item.provider, item.subject),
                )
            ),
            deleted=user.status == "deleted",
            account_status=user.status,
        )
