"""Configuration and typed contracts for the FastAPI HTMX user-manager UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, final, override

from my_usermanager.adapters.fastapi_htmx.awaitables import (  # noqa: TC001
    MaybeAwaitable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app_factory.fastapi import AppFactoryUi
    from fastapi import APIRouter, Request
    from fastapi.staticfiles import StaticFiles

    from my_usermanager.subjects import AuthenticatedSubject


# English defaults for packaged chrome. Hosts override via config.labels and/or
# an optional hooks.page_context mapping (merged last for per-request i18n).
DEFAULT_UI_LABELS: Final[dict[str, str]] = {
    "nav_account": "Account",
    "nav_users": "Users",
    "skip_to_content": "Skip to content",
    "users_document_title": "Users - User management",
    "users_badge": "Admin",
    "users_title": "Users",
    "users_description": (
        "User status and access changes call host-owned callbacks and swap one row."
    ),
    "current_user": "Current user",
    "col_user": "User",
    "col_email": "Email",
    "col_access": "Access",
    "col_identities": "Identities",
    "col_status": "Status",
    "col_action": "Action",
    "empty_users": "No users are available.",
    "badge_admin": "Admin",
    "badge_user": "User",
    "status_disabled": "Disabled",
    "status_active": "Active",
    "action_enable": "Enable",
    "action_disable": "Disable",
    "action_grant_role": "Grant role",
    "action_revoke": "Revoke",
    "action_grant": "Grant",
    "updating_row": "Updating row.",
    "account_document_title": "Account - User management",
    "account_badge": "Account",
    "account_title": "Account",
    "account_description": (
        "This reusable page delegates authentication and passkey behavior to "
        "host callbacks."
    ),
    "local_user_id": "Local user id",
    "external_subject": "External subject",
    "profile_title": "Profile",
    "profile_description": (
        "Username is required. Birth date and gender are optional."
    ),
    "profile_username": "Username",
    "profile_first_name": "First name",
    "profile_last_name": "Last name",
    "profile_display_name": "Display name",
    "profile_email": "Email",
    "profile_birth_date": "Birth date",
    "profile_gender": "Gender",
    "profile_gender_unspecified": "Prefer not to say",
    "profile_gender_female": "Female",
    "profile_gender_male": "Male",
    "profile_gender_other": "Other",
    "profile_save": "Save profile",
    "profile_saved": "Profile saved.",
    "session_title": "Session",
    "session_description": "Sign out of this app on this device.",
    "log_out": "Log out",
}


def resolve_ui_labels(
    config_labels: Mapping[str, str] | None = None,
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge default chrome labels with host config and per-request overrides."""
    merged = dict(DEFAULT_UI_LABELS)
    if config_labels:
        merged.update(dict(config_labels))
    if overrides:
        merged.update(dict(overrides))
    return merged


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
    """Route and static settings for the adapter.

    Host shell integration (optional, backward compatible):

    * ``base_template`` — Jinja name that account/users pages extend. Default
      ``base.html`` is the packaged shell (``app_factory/shell.html``). Hosts
      that pass their own Jinja ``environment`` to ``install_usermanager_ui``
      may point this at a host template that provides a ``content`` block
      (and any chrome the host owns).
    * ``labels`` — optional chrome string overrides merged over
      :data:`DEFAULT_UI_LABELS`. Per-request i18n can further override via an
      optional hooks ``page_context`` mapping key ``labels``.
    """

    account_path: str = "/account"
    profile_path: str = "/account/profile"
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
    base_template: str = "base.html"
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Require CSRF protection whenever admin mutations are enabled."""
        if self.admin_enabled and self.csrf_protection is None:
            message = "csrf_protection is required when admin_enabled is true"
            raise ValueError(message)
        if not self.base_template or not str(self.base_template).strip():
            message = "base_template must be a non-empty Jinja template name"
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

    # Optional (checked via getattr): update_own_profile(request, current_user, update)
    # -> AuthenticatedSubject. When absent, the account profile form is read-only.


@dataclass(frozen=True, slots=True)
class UserManagerUi:
    """Installed usermanager UI router, CSS mount, platform, and callbacks."""

    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles
    platform: AppFactoryUi
    config: UserManagerUiConfig
    hooks: UserManagerUiHooks
