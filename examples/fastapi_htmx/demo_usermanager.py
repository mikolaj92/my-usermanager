"""User-manager adapter hooks for the composition example."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app_factory import PlatformPaths

from examples.fastapi_htmx.demo_users import (
    DEMO_ADMIN_ID,
    DEMO_CSRF_HEADER,
    DEMO_CSRF_MARKER,
    _current_demo_subject,
    _demo_capability_options,
    _demo_role_options,
    _demo_user_rows,
    _grant_demo_permission,
    _grant_demo_role,
    _invite_demo_user,
    _reissue_demo_invitation,
    _require_demo_admin,
    _revoke_demo_invitation,
    _revoke_demo_permission,
    _revoke_demo_role,
    _set_demo_user_disabled,
)
from my_usermanager.adapters.fastapi_htmx import (
    CapabilityOption,
    CsrfContext,
    CsrfProtection,
    InvitationResult,
    PasskeyPanel,
    PermissionGrantRow,
    UserRow,
)

if TYPE_CHECKING:
    from fastapi import Request

    from my_usermanager.subjects import AuthenticatedSubject


class _DemoCsrfProtection:
    def token(self, _request: Request) -> str:
        return DEMO_CSRF_MARKER

    def validate(self, _request: Request, submitted_token: str) -> None:
        if submitted_token != DEMO_CSRF_MARKER:
            error = "invalid demo CSRF token"
            raise ValueError(error)


def _demo_csrf_protection() -> CsrfProtection:
    return _DemoCsrfProtection()


class _UserManagerHooks:
    def page_context(self, _request: Request) -> dict[str, object]:
        return {"platform_paths": PlatformPaths()}

    def get_current_user(self, request: Request) -> AuthenticatedSubject | None:
        user_id = request.query_params.get("as", DEMO_ADMIN_ID)
        return _current_demo_subject(user_id)

    def require_admin(
        self,
        _request: Request,
        current_user: AuthenticatedSubject,
    ) -> None:
        _require_demo_admin(current_user.user_id)

    def list_users(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
    ) -> tuple[UserRow, ...]:
        return _demo_user_rows()

    def role_options(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
    ) -> tuple[str, ...]:
        return _demo_role_options()

    def capability_options(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
    ) -> tuple[CapabilityOption, ...]:
        return _demo_capability_options()

    def set_user_disabled(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        user_id: str,
        disabled: bool,
    ) -> UserRow:
        return _set_demo_user_disabled(user_id, disabled=disabled)

    def grant_role(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> UserRow:
        return _grant_demo_role(user_id, role_name)

    def revoke_role(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> UserRow:
        return _revoke_demo_role(user_id, role_name)

    def grant_permission(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> UserRow:
        return _grant_demo_permission(user_id, permission)

    def revoke_permission(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> UserRow:
        return _revoke_demo_permission(user_id, permission)

    def invite_user(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        username: str,
        email: str,
        role: str,
    ) -> InvitationResult:
        return _invite_demo_user(username, email, role)

    def reissue_invitation(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        invitation_id: str,
    ) -> InvitationResult:
        return _reissue_demo_invitation(invitation_id)

    def revoke_invitation(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        invitation_id: str,
    ) -> UserRow:
        return _revoke_demo_invitation(invitation_id)

    def csrf_context(self, _request: Request) -> CsrfContext:
        return CsrfContext(
            hidden_inputs=(("_demo_csrf", DEMO_CSRF_MARKER),),
            headers={DEMO_CSRF_HEADER: DEMO_CSRF_MARKER},
        )

    def after_user_disabled_changed(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        _row: UserRow,
    ) -> None:
        return None

    def render_passkey_panel(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
    ) -> PasskeyPanel:
        return PasskeyPanel(template_name="auth/_integration_panel.html", context={})


def _usermanager_hooks() -> _UserManagerHooks:
    return _UserManagerHooks()


def _demo_csrf_token(_request: Request) -> str:
    return DEMO_CSRF_MARKER


_PASSKEY_PANEL_HTML: Final = ""
