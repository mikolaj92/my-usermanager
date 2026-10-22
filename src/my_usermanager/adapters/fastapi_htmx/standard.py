"""Mechanical ``UserManager``-backed hooks for the packaged UI.

Hosts still provide session lookup, administrator policy, the role catalogue,
CSRF configuration, invitations, auditing, and product side effects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from inspect import isawaitable
from typing import TYPE_CHECKING, Concatenate, Generic, final, overload

from fastapi import Request
from typing_extensions import TypeVar as DefaultTypeVar

from my_usermanager.adapters.fastapi_htmx.awaitables import execution_loop
from my_usermanager.adapters.fastapi_htmx.config import (
    CapabilityOption,
    CsrfContext,
    ExternalIdentityRow,
    PasskeyPanel,
    PermissionGrantRow,
    UserPage,
    UserRow,
)
from my_usermanager.adapters.fastapi_htmx.ids import row_key_from_user_id
from my_usermanager.adapters.my_auth_fastapi import RegistrationExecutor
from my_usermanager.manager import (
    PermissionGrantRequest,
    RoleGrantRequest,
    UserManager,
    UserProfileUpdate,
)
from my_usermanager.models import Permission, Scope, User
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.stores import InvalidPageError, UserNotFoundError, UserQuery
from my_usermanager.subjects import AuthenticatedSubject

if TYPE_CHECKING:
    from my_usermanager.identity_capabilities import IdentityProviderCapabilities

CurrentUser = Callable[[Request], AuthenticatedSubject | None]
RequireAdmin = Callable[[Request, AuthenticatedSubject], None]
AfterDisabled = Callable[
    [Request, AuthenticatedSubject, UserRow], Awaitable[None] | None
]
PasskeyPanelProvider = Callable[[Request, AuthenticatedSubject], PasskeyPanel | None]


_Executor_co = DefaultTypeVar(
    "_Executor_co", bound=RegistrationExecutor | None, default=None, covariant=True
)


@final
class _ExecutedHook[**Params, Result]:
    def __init__(
        self,
        method: Callable[
            Concatenate[
                StandardUserManagerUiHooks[RegistrationExecutor | None], Params
            ],
            Result,
        ],
    ) -> None:
        self.method = method

    @overload
    def __get__(
        self, instance: StandardUserManagerUiHooks[None], owner: type[object]
    ) -> Callable[Params, Result]: ...

    @overload
    def __get__(
        self,
        instance: StandardUserManagerUiHooks[RegistrationExecutor],
        owner: type[object],
    ) -> Callable[Params, Awaitable[Result]]: ...

    def __get__(
        self,
        instance: StandardUserManagerUiHooks[RegistrationExecutor | None],
        owner: type[object],
    ) -> Callable[Params, Result] | Callable[Params, Awaitable[Result]]:
        del owner
        if instance.executor is None:
            return partial(self.method, instance)
        executor = instance.executor

        async def execute(*args: Params.args, **kwargs: Params.kwargs) -> Result:
            loop = asyncio.get_running_loop()

            def invoke() -> Result:
                token = execution_loop.set(loop)
                try:
                    return self.method(instance, *args, **kwargs)
                finally:
                    execution_loop.reset(token)

            return await executor(invoke)

        return execute


@dataclass(slots=True)
class StandardUserManagerUiHooks(Generic[_Executor_co]):
    """Implement routine UI hooks using the public ``UserManager`` API.

    This adapter intentionally has no registration, invitation, session, audit,
    or role policy. Hosts add those optional hooks themselves or keep a focused
    subclass when those surfaces are needed.
    """

    executor: _Executor_co | None
    manager: UserManager
    current_user: CurrentUser
    require_admin_policy: RequireAdmin
    role_names: tuple[str, ...] = ()
    capabilities: tuple[CapabilityOption, ...] = ()
    page_size: int = 200
    after_disabled: AfterDisabled | None = None
    passkey_panel: PasskeyPanelProvider | None = None
    identity_providers: tuple[IdentityProviderCapabilities, ...] | None = None

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
        identity_providers: tuple[IdentityProviderCapabilities, ...] | None = None,
        executor: _Executor_co | None = None,
    ) -> None:
        """Bind standard stores to the small set of host-owned policies."""
        if page_size < 1:
            msg = "page_size must be positive"
            raise ValueError(msg)
        self.executor = executor
        self.manager = manager
        self.current_user = current_user
        self.require_admin_policy = require_admin
        self.role_names = role_names
        self.capabilities = capabilities
        self.page_size = page_size
        self.after_disabled = after_disabled
        self.passkey_panel = passkey_panel
        self.identity_providers = identity_providers

    @_ExecutedHook
    def get_current_user(self, request: Request) -> AuthenticatedSubject | None:
        """Delegate request/session interpretation to the host."""
        return self.current_user(request)

    @_ExecutedHook
    def require_admin(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> None:
        """Delegate administrator policy to the host."""
        return self.require_admin_policy(request, current_user)

    @_ExecutedHook
    def list_users(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        *,
        limit: int | None = None,
        offset: int = 0,
        query: UserQuery | None = None,
    ) -> UserPage:
        """Return one deterministic page of manager users as packaged-UI rows."""
        del request, current_user
        selected_limit = self.page_size if limit is None else limit
        if selected_limit < 1 or selected_limit > self.page_size:
            field_name = "limit"
            reason = "must be between 1 and page_size"
            raise InvalidPageError(field_name, selected_limit, reason)
        if offset < 0:
            field_name = "offset"
            reason = "must be greater than or equal to zero"
            raise InvalidPageError(field_name, offset, reason)
        user_query = UserQuery() if query is None else query
        page = self.manager.users.list(
            limit=selected_limit + 1,
            offset=offset,
            query=user_query,
        )
        return UserPage(
            items=tuple(self._row(user) for user in page[:selected_limit]),
            limit=selected_limit,
            offset=offset,
            has_previous=offset > 0,
            has_next=len(page) > selected_limit,
            filtered=user_query != UserQuery(),
        )

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

    @_ExecutedHook
    def set_user_disabled(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        disabled: bool,
    ) -> UserRow:
        """Apply the manager's account-transition and last-admin rules."""
        user = self.manager.users.get(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        if user.disabled != disabled:
            target_status = "disabled" if disabled else "active"
            user = self.manager.transition_account(
                user_id=user_id, status=target_status
            )
        row = self._row(user)
        if self.executor is not None and self.after_disabled is not None:
            self._after_disabled(request, current_user, row)
        return row

    @_ExecutedHook
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

    @_ExecutedHook
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

    @_ExecutedHook
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

    @_ExecutedHook
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

    @_ExecutedHook
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

    @_ExecutedHook
    def after_user_disabled_changed(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        row: UserRow,
    ) -> None:
        """Run an optional host side effect after the manager mutation."""
        if self.executor is None and self.after_disabled is not None:
            self._after_disabled(request, current_user, row)

    def _after_disabled(
        self, request: Request, current_user: AuthenticatedSubject, row: UserRow
    ) -> None:
        if self.after_disabled is None:
            return
        result = self.after_disabled(request, current_user, row)
        if isawaitable(result):

            async def complete() -> None:
                await result

            asyncio.run_coroutine_threadsafe(complete(), execution_loop.get()).result()

    @_ExecutedHook
    def account_identity_providers(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> tuple[IdentityProviderCapabilities, ...] | None:
        """Select configured capabilities from current local identity links.

        None retains legacy panel hooks; an explicit empty tuple opts out.
        """
        del request
        if self.identity_providers is None:
            return None
        user = self.manager.users.get(current_user.user_id)
        if user is None or user.status != "active":
            return ()
        linked = {identity.provider for identity in user.external_identities}
        return tuple(p for p in self.identity_providers if p.provider in linked)

    @_ExecutedHook
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
        grants = tuple(self.manager.grants.list_grants_for_user(user.user_id))
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
