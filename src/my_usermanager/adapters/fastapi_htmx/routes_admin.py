# ruff: noqa: TC002
"""Admin user list and grant/disable mutation routes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse

from my_usermanager.adapters.fastapi_htmx.auth import Denied, admin_user
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import PermissionGrantRow
from my_usermanager.adapters.fastapi_htmx.forms import (
    FormError,
    MutationForm,
    read_grant_form,
    read_mutation_form,
)
from my_usermanager.adapters.fastapi_htmx.page import (
    csrf_inputs,
    invalid_page_response,
    is_htmx_request,
    load_user_page,
    merge_labels,
    page_context,
    pager_state,
    parse_page_query,
    parse_user_query,
    row_response,
    validate_csrf,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.adapters.fastapi_htmx.rows import safe_row
from my_usermanager.models import ValidationError
from my_usermanager.stores import InvalidPageError

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import APIRouter
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import (
        UserManagerUiConfig,
        UserManagerUiHooks,
    )


def add_admin_users_page(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register the administrative users list page."""

    async def users(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        try:
            page_query = parse_page_query(request)
            user_query = parse_user_query(request)
            users_page = await load_user_page(
                hooks,
                request,
                auth.current_user,
                page=page_query,
                query=user_query,
            )
        except (InvalidPageError, ValidationError) as error:
            return invalid_page_response(error)
        host_context = await page_context(hooks, request)
        labels = merge_labels(config, host_context)
        csrf = await resolve(hooks.csrf_context(request))
        context = {
            **host_context,
            "request": request,
            "config": config,
            "current_user": auth.current_user,
            "users": tuple(safe_row(row) for row in users_page.items),
            "role_options": tuple(
                await resolve(hooks.role_options(request, auth.current_user))
            ),
            "capability_options": tuple(
                await resolve(hooks.capability_options(request, auth.current_user))
            ),
            "csrf": csrf,
            "csrf_inputs": csrf_inputs(config, request, csrf),
            "static_url_path": config.static_url_path,
            "base_template": config.base_template,
            "labels": labels,
            "invitation_url": request.query_params.get("invitation_url"),
            "invitation_delivery": request.query_params.get("invitation_delivery"),
            "invite_enabled": callable(getattr(hooks, "invite_user", None)),
            "reissue_invitation_enabled": callable(
                getattr(hooks, "reissue_invitation", None)
            ),
            "revoke_invitation_enabled": callable(
                getattr(hooks, "revoke_invitation", None)
            ),
            "soft_delete_enabled": callable(getattr(hooks, "soft_delete_user", None)),
            "hard_delete_enabled": callable(getattr(hooks, "hard_delete_user", None)),
            "query_params": page_query.query_params,
            "filter_query": urlencode(page_query.query_params),
            "pager": pager_state(users_page, page_query.page),
            "results_url": config.users_path,
        }
        template_name = (
            "users/_results.html" if is_htmx_request(request) else "users/list.html"
        )
        return HTMLResponse(templates.get_template(template_name).render(**context))

    router.add_api_route(config.users_path, users, methods=["GET"])


def add_admin_mutation_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register enable/disable and grant/revoke mutation endpoints."""
    for path, kind in (
        (config.disable_user_path, "disable"),
        (config.enable_user_path, "enable"),
        (config.grant_role_path, "grant-role"),
        (config.revoke_role_path, "revoke-role"),
        (config.grant_permission_path, "grant-permission"),
        (config.revoke_permission_path, "revoke-permission"),
    ):
        router.add_api_route(
            path,
            _mutation_endpoint(templates, config, hooks, kind),
            methods=["POST"],
        )


def _mutation_endpoint(
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    kind: str,
) -> Callable[[Request], MaybeAwaitable[Response]]:
    async def mutation(request: Request) -> Response:
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
        csrf_error = await validate_csrf(request, config, form.csrf_token)
        if csrf_error is not None:
            return csrf_error
        if isinstance(form, MutationForm):
            changed = await resolve(
                hooks.set_user_disabled(
                    request, auth.current_user, form.user_id, kind == "disable"
                )
            )
            await resolve(
                hooks.after_user_disabled_changed(request, auth.current_user, changed)
            )
        elif kind in {"grant-role", "revoke-role"}:
            callback = hooks.grant_role if kind == "grant-role" else hooks.revoke_role
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
        return await row_response(
            templates,
            request,
            config=config,
            hooks=hooks,
            current_user=auth.current_user,
            row=changed,
            csrf=csrf,
        )

    return mutation
