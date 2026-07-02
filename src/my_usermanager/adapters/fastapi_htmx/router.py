"""FastAPI routes for the reusable server-rendered user-manager UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from my_usermanager.adapters.fastapi_htmx.auth import (
    Authenticated,
    Denied,
    admin_user,
    current_user,
)
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import (
    CapabilityOption,
    CsrfContext,
    PermissionGrantRow,
    UserManagerUiConfig,
    UserManagerUiRouter,
    UserRow,
)
from my_usermanager.adapters.fastapi_htmx.forms import (
    FormError,
    GrantForm,
    MutationForm,
    read_grant_form,
    read_mutation_form,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response, response_text
from my_usermanager.adapters.fastapi_htmx.rows import safe_row
from my_usermanager.adapters.fastapi_htmx.static import usermanager_ui_static_files
from my_usermanager.adapters.fastapi_htmx.templates import create_template_environment

if TYPE_CHECKING:
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.protocols import UserManagerUiHooks
    from my_usermanager.subjects import AuthenticatedSubject


def create_usermanager_ui_router(  # noqa: C901
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> UserManagerUiRouter:
    """Create account/admin routes and packaged static files for host mounting."""
    templates = create_template_environment(config)
    router = APIRouter()

    async def account(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        match auth:
            case Denied(response=response):
                return response
            case Authenticated(current_user=authenticated_user):
                panel = await _passkey_panel_html(
                    templates,
                    request,
                    hooks,
                    authenticated_user,
                )
                html = templates.get_template("account/index.html").render(
                    request=request,
                    config=config,
                    current_user=authenticated_user,
                    passkey_panel_html=panel,
                    static_url_path=config.static_url_path,
                )
                return HTMLResponse(html)

    async def users(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        match auth:
            case Denied(response=response):
                return response
            case Authenticated(current_user=authenticated_user):
                csrf = await resolve(hooks.csrf_context(request))
                rows = tuple(
                    safe_row(row)
                    for row in await resolve(
                        hooks.list_users(request, authenticated_user),
                    )
                )
                html = templates.get_template("users/list.html").render(
                    request=request,
                    config=config,
                    current_user=authenticated_user,
                    users=rows,
                    role_options=await _role_options(
                        request,
                        hooks,
                        authenticated_user,
                    ),
                    capability_options=await _capability_options(
                        request,
                        hooks,
                        authenticated_user,
                    ),
                    csrf=csrf,
                    csrf_inputs=csrf.hidden_inputs,
                    static_url_path=config.static_url_path,
                )
                return HTMLResponse(html)

    async def disable(request: Request) -> Response:
        return await _change_disabled(templates, request, config, hooks, disabled=True)

    async def enable(request: Request) -> Response:
        return await _change_disabled(templates, request, config, hooks, disabled=False)

    async def grant_role(request: Request) -> Response:
        return await _change_role(templates, request, config, hooks, grant=True)

    async def revoke_role(request: Request) -> Response:
        return await _change_role(templates, request, config, hooks, grant=False)

    async def grant_permission(request: Request) -> Response:
        return await _change_permission(templates, request, config, hooks, grant=True)

    async def revoke_permission(request: Request) -> Response:
        return await _change_permission(templates, request, config, hooks, grant=False)

    router.add_api_route(config.account_path, account, methods=["GET"])
    router.add_api_route(config.users_path, users, methods=["GET"])
    router.add_api_route(config.disable_user_path, disable, methods=["POST"])
    router.add_api_route(config.enable_user_path, enable, methods=["POST"])
    router.add_api_route(config.grant_role_path, grant_role, methods=["POST"])
    router.add_api_route(config.revoke_role_path, revoke_role, methods=["POST"])
    router.add_api_route(
        config.grant_permission_path,
        grant_permission,
        methods=["POST"],
    )
    router.add_api_route(
        config.revoke_permission_path,
        revoke_permission,
        methods=["POST"],
    )
    return UserManagerUiRouter(
        router=router,
        static_mount_path=config.static_mount_path,
        static_files=usermanager_ui_static_files(),
    )


async def _change_disabled(
    templates: Environment,
    request: Request,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    *,
    disabled: bool,
) -> Response:
    auth = await admin_user(request, config, hooks)
    match auth:
        case Denied(response=response):
            return response
        case Authenticated(current_user=authenticated_user):
            form = await read_mutation_form(request)
            match form:
                case FormError(status_code=status_code, title=title, message=message):
                    return error_response(status_code, title, message)
                case MutationForm(user_id=user_id):
                    changed = await resolve(
                        hooks.set_user_disabled(
                            request,
                            authenticated_user,
                            user_id,
                            disabled,
                        ),
                    )
                    await resolve(
                        hooks.after_user_disabled_changed(
                            request,
                            authenticated_user,
                            changed,
                        ),
                    )
                    csrf = await resolve(hooks.csrf_context(request))
                    return await _row_response(
                        templates,
                        request,
                        config,
                        hooks,
                        authenticated_user,
                        changed,
                        csrf,
                    )


async def _change_role(
    templates: Environment,
    request: Request,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    *,
    grant: bool,
) -> Response:
    auth = await admin_user(request, config, hooks)
    match auth:
        case Denied(response=response):
            return response
        case Authenticated(current_user=authenticated_user):
            form = await read_grant_form(request, value_field="role_name")
            match form:
                case FormError(status_code=status_code, title=title, message=message):
                    return error_response(status_code, title, message)
                case GrantForm(user_id=user_id, value=role_name):
                    if grant:
                        changed = await resolve(
                            hooks.grant_role(
                                request,
                                authenticated_user,
                                user_id,
                                role_name,
                            ),
                        )
                    else:
                        changed = await resolve(
                            hooks.revoke_role(
                                request,
                                authenticated_user,
                                user_id,
                                role_name,
                            ),
                        )
                    csrf = await resolve(hooks.csrf_context(request))
                    return await _row_response(
                        templates,
                        request,
                        config,
                        hooks,
                        authenticated_user,
                        changed,
                        csrf,
                    )


async def _change_permission(
    templates: Environment,
    request: Request,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    *,
    grant: bool,
) -> Response:
    auth = await admin_user(request, config, hooks)
    match auth:
        case Denied(response=response):
            return response
        case Authenticated(current_user=authenticated_user):
            form = await read_grant_form(request, value_field="permission")
            match form:
                case FormError(status_code=status_code, title=title, message=message):
                    return error_response(status_code, title, message)
                case GrantForm(
                    user_id=user_id,
                    value=permission,
                    scope_type=scope_type,
                    scope_id=scope_id,
                ):
                    permission_row = PermissionGrantRow(
                        permission=permission,
                        label=permission,
                        scope_type=scope_type,
                        scope_id=scope_id,
                    )
                    if grant:
                        changed = await resolve(
                            hooks.grant_permission(
                                request,
                                authenticated_user,
                                user_id,
                                permission_row,
                            ),
                        )
                    else:
                        changed = await resolve(
                            hooks.revoke_permission(
                                request,
                                authenticated_user,
                                user_id,
                                permission_row,
                            ),
                        )
                    csrf = await resolve(hooks.csrf_context(request))
                    return await _row_response(
                        templates,
                        request,
                        config,
                        hooks,
                        authenticated_user,
                        changed,
                        csrf,
                    )


async def _row_response(  # noqa: PLR0913
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
        role_options=await _role_options(request, hooks, current_user),
        capability_options=await _capability_options(request, hooks, current_user),
        csrf=csrf,
        csrf_inputs=csrf.hidden_inputs,
    )
    return HTMLResponse(html)


async def _role_options(
    request: Request,
    hooks: UserManagerUiHooks,
    current_user: AuthenticatedSubject,
) -> tuple[str, ...]:
    return tuple(await resolve(hooks.role_options(request, current_user)))


async def _capability_options(
    request: Request,
    hooks: UserManagerUiHooks,
    current_user: AuthenticatedSubject,
) -> tuple[CapabilityOption, ...]:
    return tuple(await resolve(hooks.capability_options(request, current_user)))


async def _passkey_panel_html(
    templates: Environment,
    request: Request,
    hooks: UserManagerUiHooks,
    current_user: AuthenticatedSubject,
) -> str:
    panel = await resolve(hooks.render_passkey_panel(request, current_user))
    if panel is None:
        return templates.get_template("auth/_integration_panel.html").render(
            request=request,
            current_user=current_user,
        )
    return response_text(panel)
