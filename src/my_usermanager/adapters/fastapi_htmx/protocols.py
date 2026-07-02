"""Host callback contracts for the FastAPI HTMX adapter."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import Request, Response

    from my_usermanager.adapters.fastapi_htmx.config import (
        CapabilityOption,
        CsrfContext,
        PermissionGrantRow,
        UserRow,
    )
    from my_usermanager.subjects import AuthenticatedSubject

type MaybeAwaitable[T] = T | Awaitable[T]


class UserManagerUiHooks(Protocol):
    """Host-owned policy and persistence callbacks for the UI adapter."""

    def get_current_user(
        self,
        request: Request,
    ) -> MaybeAwaitable[AuthenticatedSubject | None]:
        """Return the current authenticated subject or None."""
        ...

    def require_admin(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
    ) -> MaybeAwaitable[None]:
        """Raise on admin denial; return None on success."""
        ...

    def list_users(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
    ) -> MaybeAwaitable[Sequence[UserRow]]:
        """Return rows for the admin user list."""
        ...

    def role_options(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
    ) -> MaybeAwaitable[Sequence[str]]:
        """Return role names the host allows this panel to grant."""
        ...

    def capability_options(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
    ) -> MaybeAwaitable[Sequence[CapabilityOption]]:
        """Return app-defined capability options the panel can grant."""
        ...

    def set_user_disabled(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        disabled: bool,
    ) -> MaybeAwaitable[UserRow]:
        """Set disabled state for exactly one host-owned user."""
        ...

    def grant_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> MaybeAwaitable[UserRow]:
        """Grant a role and return the updated user row."""
        ...

    def revoke_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> MaybeAwaitable[UserRow]:
        """Revoke a role and return the updated user row."""
        ...

    def grant_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> MaybeAwaitable[UserRow]:
        """Grant an app-defined capability and return the updated user row."""
        ...

    def revoke_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> MaybeAwaitable[UserRow]:
        """Revoke an app-defined capability and return the updated user row."""
        ...

    def csrf_context(self, request: Request) -> MaybeAwaitable[CsrfContext]:
        """Return host-provided CSRF field pairs and metadata."""
        ...

    def after_user_disabled_changed(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        row: UserRow,
    ) -> MaybeAwaitable[None]:
        """Run host-owned side effects after a successful disabled-state change."""
        ...

    def render_passkey_panel(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
    ) -> MaybeAwaitable[Response | None]:
        """Return optional host-rendered passkey HTML for the account page."""
        ...
