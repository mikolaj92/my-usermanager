"""User-manager adapter hooks for the composition example."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi.responses import HTMLResponse

from examples.fastapi_htmx.demo_users import (
    DEMO_ADMIN_ID,
    DEMO_CSRF_HEADER,
    DEMO_CSRF_MARKER,
    _current_demo_subject,
    _demo_user_rows,
    _require_demo_admin,
    _set_demo_user_disabled,
)
from my_usermanager.adapters.fastapi_htmx import CsrfContext, UserRow

if TYPE_CHECKING:
    from fastapi import Request

    from my_usermanager.subjects import AuthenticatedSubject


class _UserManagerHooks:
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

    def set_user_disabled(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        user_id: str,
        disabled: bool,
    ) -> UserRow:
        return _set_demo_user_disabled(user_id, disabled=disabled)

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
    ) -> HTMLResponse:
        return HTMLResponse(_PASSKEY_PANEL_HTML)


def _usermanager_hooks() -> _UserManagerHooks:
    return _UserManagerHooks()


def _demo_csrf_token(_request: Request) -> str:
    return DEMO_CSRF_MARKER


_PASSKEY_PANEL_HTML: Final = """
<article class="card um-card um-stack" aria-labelledby="demo-passkey-panel-title">
  <div class="um-stack-tight">
    <p class="badge">Passkeys</p>
    <h2 id="demo-passkey-panel-title">Passkey UI composition</h2>
    <p class="um-muted">
      This account page receives passkey HTML through render_passkey_panel,
      while my-auth owns the reusable passkey forms and JSON endpoints.
    </p>
  </div>
  <div class="um-cluster">
    <a class="btn btn-secondary" href="/auth/login">Open passkey login</a>
    <a class="btn btn-ghost" href="/auth/register">Open passkey registration</a>
  </div>
</article>
"""
