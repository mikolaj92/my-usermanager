# ruff: noqa: TRY003, EM101, ANN202, BLE001, PLR0913
"""FastAPI routes for the reusable server-rendered user-manager UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_factory.fastapi import AppFactoryUi, install_app_factory_ui
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse

from my_usermanager.adapters.fastapi_htmx.auth import (
    Denied,
    admin_user,
    current_user,
)
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import (
    CsrfContext,
    PasskeyPanel,
    PermissionGrantRow,
    UserManagerUi,
    UserManagerUiConfig,
    UserManagerUiConflict,
    UserManagerUiRouter,
    UserRow,
)
from my_usermanager.adapters.fastapi_htmx.forms import (
    FormError,
    MutationForm,
    read_grant_form,
    read_mutation_form,
)
from my_usermanager.adapters.fastapi_htmx.protocols import (  # noqa: TC001
    UserManagerUiHooks,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.adapters.fastapi_htmx.rows import safe_row
from my_usermanager.adapters.fastapi_htmx.static import (
    ensure_static_mount_available,
    usermanager_ui_static_files,
)
from my_usermanager.adapters.fastapi_htmx.templates import create_template_environment

if TYPE_CHECKING:
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.subjects import AuthenticatedSubject


def install_usermanager_ui(
    app: FastAPI,
    *,
    platform: AppFactoryUi,
    hooks: UserManagerUiHooks,
    config: UserManagerUiConfig | None = None,
) -> UserManagerUi:
    """Install the user-manager routes and shared platform UI assets."""
    selected = config or UserManagerUiConfig()
    installed_platform = getattr(app.state, "app_factory_ui", None)
    if installed_platform != platform:
        raise UserManagerUiConflict("app-factory UI platform is not installed")
    existing = getattr(app.state, "usermanager_ui", None)
    if isinstance(existing, UserManagerUi):
        if (
            existing.platform is not platform
            or existing.config != selected
            or existing.hooks is not hooks
        ):
            raise UserManagerUiConflict(
                "a different usermanager UI is already installed"
            )
        return existing
    environment = create_template_environment()
    _ = install_app_factory_ui(
        app,
        environments=[environment],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    result = create_usermanager_ui_router(
        config=selected, hooks=hooks, environment=environment
    )
    ensure_static_mount_available(app, result.static_mount_path)
    app.include_router(result.router)
    app.mount(result.static_mount_path, result.static_files, name="my-usermanager-ui")
    installed = UserManagerUi(
        result.router,
        result.static_mount_path,
        result.static_files,
        platform,
        selected,
        hooks,
    )
    app.state.usermanager_ui = installed
    return installed


def _csrf_inputs(
    config: UserManagerUiConfig, request: Request, context: CsrfContext
) -> tuple[tuple[str, str], ...]:
    """Merge host fields while reserving the authoritative csrf field."""
    inputs = tuple(
        (name, value) for name, value in context.hidden_inputs if name != "csrf"
    )
    protection = config.csrf_protection
    if protection is None:
        return inputs
    return (*inputs, ("csrf", protection.token(request)))


def create_usermanager_ui_router(  # noqa: C901
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    environment: Environment | None = None,
) -> UserManagerUiRouter:
    """Create enabled account/admin routes and packaged static files."""
    templates = environment or create_template_environment()
    router = APIRouter()

    async def account(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        panel = await resolve(hooks.render_passkey_panel(request, auth.current_user))
        panel_html = _render_panel(templates, request, auth.current_user, panel)
        html = templates.get_template("account/index.html").render(
            request=request,
            config=config,
            current_user=auth.current_user,
            passkey_panel_html=panel_html,
            static_url_path=config.static_url_path,
            logout_path=config.logout_path,
        )
        return HTMLResponse(html)

    async def users(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        csrf = await resolve(hooks.csrf_context(request))
        html = templates.get_template("users/list.html").render(
            request=request,
            config=config,
            current_user=auth.current_user,
            users=tuple(
                safe_row(row)
                for row in await resolve(hooks.list_users(request, auth.current_user))
            ),
            role_options=tuple(
                await resolve(hooks.role_options(request, auth.current_user))
            ),
            capability_options=tuple(
                await resolve(hooks.capability_options(request, auth.current_user))
            ),
            csrf=csrf,
            csrf_inputs=_csrf_inputs(config, request, csrf),
            static_url_path=config.static_url_path,
        )
        return HTMLResponse(html)

    if config.account_enabled:
        router.add_api_route(config.account_path, account, methods=["GET"])
    if config.admin_enabled:

        async def mutation(request: Request, kind: str) -> Response:
            auth = await admin_user(request, config, hooks)
            if isinstance(auth, Denied):
                return auth.response
            if kind in {"disable", "enable"}:
                form = await read_mutation_form(request)
            else:
                form = await read_grant_form(
                    request,
                    value_field="role_name"
                    if kind in {"grant-role", "revoke-role"}
                    else "permission",
                )
            if isinstance(form, FormError):
                return error_response(form.status_code, form.title, form.message)
            csrf_error = await _validate_csrf(request, config, form.csrf_token)
            if csrf_error is not None:
                return csrf_error
            if isinstance(form, MutationForm):
                changed = await resolve(
                    hooks.set_user_disabled(
                        request, auth.current_user, form.user_id, kind == "disable"
                    )
                )
                await resolve(
                    hooks.after_user_disabled_changed(
                        request, auth.current_user, changed
                    )
                )
            elif kind in {"grant-role", "revoke-role"}:
                callback = (
                    hooks.grant_role if kind == "grant-role" else hooks.revoke_role
                )
                changed = await resolve(
                    callback(request, auth.current_user, form.user_id, form.value)
                )
            else:
                permission = PermissionGrantRow(
                    form.value, form.value, form.scope_type, form.scope_id
                )
                callback = (
                    hooks.grant_permission
                    if kind == "grant-permission"
                    else hooks.revoke_permission
                )
                changed = await resolve(
                    callback(request, auth.current_user, form.user_id, permission)
                )
            csrf = await resolve(hooks.csrf_context(request))
            return await _row_response(
                templates, request, config, hooks, auth.current_user, changed, csrf
            )

        def make_mutation_endpoint(kind: str):
            async def endpoint(request: Request) -> Response:
                return await mutation(request, kind)

            return endpoint

        for path, kind in (
            (config.users_path, "users"),
            (config.disable_user_path, "disable"),
            (config.enable_user_path, "enable"),
            (config.grant_role_path, "grant-role"),
            (config.revoke_role_path, "revoke-role"),
            (config.grant_permission_path, "grant-permission"),
            (config.revoke_permission_path, "revoke-permission"),
        ):
            if kind == "users":
                router.add_api_route(path, users, methods=["GET"])
            else:
                router.add_api_route(
                    path, make_mutation_endpoint(kind), methods=["POST"]
                )
    return UserManagerUiRouter(
        router, config.static_mount_path, usermanager_ui_static_files()
    )


async def _validate_csrf(
    request: Request, config: UserManagerUiConfig, submitted_token: str | None
) -> HTMLResponse | None:
    """Validate CSRF before invoking any mutation callback."""
    protection = config.csrf_protection
    if protection is None or submitted_token is None:
        return error_response(
            403, "CSRF validation failed", "A valid CSRF token is required."
        )
    try:
        _ = await resolve(protection.validate(request, submitted_token))
    except Exception:
        return error_response(
            403, "CSRF validation failed", "A valid CSRF token is required."
        )
    return None


async def _row_response(
    templates: Environment,
    request: Request,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    current_user: AuthenticatedSubject,
    row: UserRow,
    csrf: CsrfContext,
) -> HTMLResponse:
    html = templates.get_template("users/_row.html").render(
        request=request,
        config=config,
        user=safe_row(row),
        role_options=tuple(await resolve(hooks.role_options(request, current_user))),
        capability_options=tuple(
            await resolve(hooks.capability_options(request, current_user))
        ),
        csrf=csrf,
        csrf_inputs=_csrf_inputs(config, request, csrf),
    )
    return HTMLResponse(html)


def _render_panel(
    templates: Environment,
    request: Request,
    current_user: AuthenticatedSubject,
    panel: PasskeyPanel | None,
) -> str:
    """Render a named packaged template with merged safe context."""
    if panel is None:
        panel = PasskeyPanel("auth/_integration_panel.html", {})
    context = {"request": request, "current_user": current_user, **dict(panel.context)}
    return templates.get_template(panel.template_name).render(**context)
