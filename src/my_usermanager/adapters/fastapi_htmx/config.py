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
def _empty_labels() -> dict[str, str]:
    return {}


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
    "status_pending": "Pending",
    "status_deleted": "Deleted",
    "invitation_status_pending": "Invitation pending",
    "invitation_status_used": "Invitation used",
    "invitation_status_revoked": "Invitation revoked",
    "invitation_status_expired": "Invitation expired",
    "invitation_expires": "Expires",
    "action_enable": "Enable",
    "action_disable": "Disable",
    "action_grant_role": "Grant role",
    "action_revoke": "Revoke",
    "action_grant": "Grant",
    "action_reissue_invitation": "Reissue invitation",
    "action_revoke_invitation": "Revoke invitation",
    "action_soft_delete": "Delete account",
    "action_hard_delete": "Permanently delete",
    "confirm_hard_delete": "Type the user id to permanently delete this account.",
    "invite_title": "Invite user",
    "invite_description": (
        "Create a pending account and send the activation link through your "
        "trusted delivery channel."
    ),
    "invite_username": "Username",
    "invite_email": "Email",
    "invite_role": "Initial role",
    "invite_submit": "Create invitation",
    "invite_activation_link": "Activation link",
    "invite_activation_once": (
        "Copy this activation link now. It is shown once and is not stored "
        "in this admin UI."
    ),
    "sessions_title": "Sessions",
    "sessions_description": "Review and revoke active application sessions.",
    "sessions_empty": "No active sessions.",
    "session_current": "Current",
    "audit_title": "Audit log",
    "audit_description": "Append-only account and authorization activity.",
    "audit_empty": "No audit events.",
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
class InvitationRow:
    """Safe invitation metadata for admin rows; raw tokens are never included."""

    invitation_id: str
    status: str
    expires_at: str | None = None


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
    deleted: bool = False
    account_status: str | None = None
    invitation: InvitationRow | None = None


@dataclass(frozen=True, slots=True)
class SessionRow:
    """Safe host session metadata; opaque tokens are never exposed."""

    session_id: str
    created_at: str
    last_seen_at: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    current: bool = False


@dataclass(frozen=True, slots=True)
class AuditRow:
    """Safe append-only audit row for the administrator reader."""

    timestamp: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    result: str


@dataclass(frozen=True, slots=True)
class InvitationResult:
    """Invitation delivery result linked to my-auth's activation page."""

    activation_url: str


@dataclass(frozen=True, slots=True)
class CsrfContext:
    """Host-provided CSRF form fields and response metadata."""

    hidden_inputs: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class UserManagerUiConfig:
    """Route and static settings for the adapter.

    Host shell integration (optional, backward compatible):

    * ``base_template`` — Jinja name that account/users pages extend. The
      packaged default is a thin extension of app-factory's canonical
      authenticated identity shell. Hosts
      that pass their own Jinja ``environment`` to ``install_usermanager_ui``
      may point this at a host template that provides a ``content`` block
      without copying platform chrome.
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
    invite_path: str = "/admin/users/invite"
    reissue_invitation_path: str = "/admin/users/invitations/reissue"
    revoke_invitation_path: str = "/admin/users/invitations/revoke"
    soft_delete_user_path: str = "/admin/users/delete"
    hard_delete_user_path: str = "/admin/users/delete-permanently"
    sessions_path: str = "/account/sessions"
    revoke_session_path: str = "/account/sessions/revoke"
    audit_path: str = "/admin/audit"
    static_mount_path: str = "/usermanager/ui/static"
    static_url_path: str = "/usermanager/ui/static"
    login_url: str = "/auth/login"
    logout_path: str = "/logout"
    account_enabled: bool = True
    admin_enabled: bool = True
    csrf_protection: CsrfProtection | None = None
    base_template: str = "base.html"
    labels: Mapping[str, str] = field(default_factory=_empty_labels)

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

    # Optional hooks are discovered with getattr so existing hosts remain valid:
    # invite_user(request, current_user, username, email, role) -> InvitationResult
    # reissue_invitation(request, current_user, invitation_id) -> InvitationResult
    # revoke_invitation(request, current_user, invitation_id) -> UserRow
    # list_sessions(request, current_user) -> Sequence[SessionRow]
    # revoke_session(request, current_user, session_id) -> None
    # list_audit_events(request, current_user) -> Sequence[AuditRow]
    # soft_delete_user(request, current_user, user_id) -> UserRow
    # hard_delete_user and update_own_profile follow the same host-owned pattern.


@dataclass(frozen=True, slots=True)
class UserManagerUi:
    """Installed usermanager UI router, CSS mount, platform, and callbacks."""

    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles
    platform: AppFactoryUi
    config: UserManagerUiConfig
    hooks: UserManagerUiHooks
