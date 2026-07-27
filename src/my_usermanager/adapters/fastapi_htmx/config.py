"""Configuration and typed contracts for the FastAPI HTMX user-manager UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, final, override

from my_usermanager.adapters.fastapi_htmx.awaitables import (  # noqa: TC001
    MaybeAwaitable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app_factory.fastapi import AppFactoryUi
    from fastapi import APIRouter, Request
    from fastapi.staticfiles import StaticFiles

    from my_usermanager.subjects import AuthenticatedSubject


class CsrfProtection(Protocol):
    """Host-owned CSRF validation required for enabled mutation routes."""

    def token(self, request: Request) -> str:
        """Return a token to include in rendered mutation forms."""
        ...

    def validate(self, request: Request, submitted_token: str) -> object:
        """Validate a submitted mutation token, raising when it is invalid."""
        ...


@final
class UserManagerUiConflict(ValueError):  # noqa: N818
    """Raised when a different usermanager UI is already installed."""

    def __init__(self, message: str) -> None:
        """Initialize the conflict with its message."""
        super().__init__(message)
        self.message: str = message

    @override
    def __str__(self) -> str:
        """Return the conflict message."""
        return self.message


@dataclass(frozen=True, slots=True)
class PasskeyPanel:
    """Typed host-selected packaged template and safe context."""

    template_name: str
    context: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CapabilityOption:
    """Host-defined capability that an administrator may grant."""

    permission: str
    label: str
    description: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalIdentityRow:
    """External identity displayed for a managed user."""

    provider: str
    subject: str


@dataclass(frozen=True, slots=True)
class PermissionGrantRow:
    """Permission grant displayed for a managed user."""

    permission: str
    label: str
    scope_type: str | None = None
    scope_id: str | None = None


@dataclass(frozen=True, slots=True)
class UserRow:
    """Host-provided managed-user row rendered by the administrator UI."""

    user_id: str
    row_key: str
    username: str
    display_name: str | None
    email: str | None
    disabled: bool
    is_admin: bool
    roles: tuple[str, ...] = ()
    permissions: tuple[PermissionGrantRow, ...] = ()
    external_identities: tuple[ExternalIdentityRow, ...] = ()


@dataclass(frozen=True, slots=True)
class CsrfContext:
    """Host-provided CSRF form fields and response metadata."""

    hidden_inputs: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class UserManagerUiConfig:
    """Route and static settings for the adapter."""

    account_path: str = "/account"
    users_path: str = "/admin/users"
    disable_user_path: str = "/admin/users/disable"
    enable_user_path: str = "/admin/users/enable"
    grant_role_path: str = "/admin/users/grant-role"
    revoke_role_path: str = "/admin/users/revoke-role"
    grant_permission_path: str = "/admin/users/grant-permission"
    revoke_permission_path: str = "/admin/users/revoke-permission"
    static_mount_path: str = "/usermanager/ui/static"
    static_url_path: str = "/usermanager/ui/static"
    login_url: str = "/auth/login"
    logout_path: str = "/logout"
    account_enabled: bool = True
    admin_enabled: bool = True
    csrf_protection: CsrfProtection | None = None

    def __post_init__(self) -> None:
        """Require CSRF protection whenever admin mutations are enabled."""
        if self.admin_enabled and self.csrf_protection is None:
            message = "csrf_protection is required when admin_enabled is true"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class UserManagerUiRouter:
    """Router plus static mount values returned to host applications."""

    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles


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
    ) -> MaybeAwaitable[PasskeyPanel | None]:
        """Return an optional packaged-template passkey panel descriptor."""
        ...


@dataclass(frozen=True, slots=True)
class UserManagerUi:
    """Installed usermanager UI router, CSS mount, platform, and callbacks."""

    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles
    platform: AppFactoryUi
    config: UserManagerUiConfig
    hooks: UserManagerUiHooks
