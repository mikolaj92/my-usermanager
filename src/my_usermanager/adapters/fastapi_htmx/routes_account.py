# ruff: noqa: BLE001, TC002
"""Account profile routes for the reusable user-manager UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from my_usermanager.adapters.fastapi_htmx.account_capabilities import account_providers
from my_usermanager.adapters.fastapi_htmx.auth import Denied, current_user
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.forms import FormError, read_profile_form
from my_usermanager.adapters.fastapi_htmx.page import (
    csrf_inputs,
    merge_labels,
    page_context,
    validate_csrf,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.manager import UserProfileUpdate

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import APIRouter
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import (
        PasskeyPanel,
        UserManagerUiConfig,
        UserManagerUiHooks,
    )
    from my_usermanager.subjects import AuthenticatedSubject


def add_account_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register account page and profile-update endpoints."""
    router.add_api_route(
        config.account_path,
        _account_endpoint(templates, config, hooks),
        methods=["GET"],
    )
    router.add_api_route(
        config.profile_path,
        _profile_endpoint(config, hooks),
        methods=["POST"],
    )


def _account_endpoint(
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> Callable[[Request], MaybeAwaitable[Response]]:
    async def account(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        host_context = await page_context(hooks, request)
        if "platform_paths" not in host_context:
            return error_response(
                500,
                "Platform session unavailable",
                "Host page_context must provide platform_paths.",
            )
        labels = merge_labels(config, host_context)
        providers = await account_providers(hooks, request, auth.current_user)
        local_credentials = providers is None or any(
            p.credentials.mode == "local" for p in providers
        )
        panel = (
            await resolve(hooks.render_passkey_panel(request, auth.current_user))
            if local_credentials
            else None
        )
        panel_html = _render_panel(templates, request, auth.current_user, panel)
        profile_editable = callable(getattr(hooks, "update_own_profile", None))
        csrf_fields: tuple[tuple[str, str], ...] = ()
        if profile_editable and config.csrf_protection is not None:
            csrf = await resolve(hooks.csrf_context(request))
            csrf_fields = csrf_inputs(config, request, csrf)
        html = templates.get_template("account/index.html").render(
            **{
                **host_context,
                "request": request,
                "config": config,
                "current_user": auth.current_user,
                "passkey_panel_html": panel_html,
                "identity_providers": providers or (),
                "static_url_path": config.static_url_path,
                "base_template": config.base_template,
                "labels": labels,
                "profile_editable": profile_editable,
                "csrf_inputs": csrf_fields,
                "profile_message": request.query_params.get("saved")
                and labels["profile_saved"],
                "profile_error": None,
            }
        )
        return HTMLResponse(html)

    return account


def _profile_endpoint(
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> Callable[[Request], MaybeAwaitable[Response]]:
    async def update_profile(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        updater = getattr(hooks, "update_own_profile", None)
        if not callable(updater):
            return error_response(
                501,
                "Profile update unavailable",
                "Host did not provide update_own_profile.",
            )
        form = await read_profile_form(request)
        if isinstance(form, FormError):
            return error_response(form.status_code, form.title, form.message)
        if config.csrf_protection is not None:
            csrf_error = await validate_csrf(request, config, form.csrf_token)
            if csrf_error is not None:
                return csrf_error
        try:
            update = UserProfileUpdate(
                username=form.username,
                first_name=form.first_name,
                last_name=form.last_name,
                display_name=form.display_name,
                email=form.email,
                birth_date=form.birth_date,
                gender=form.gender,
            )
            _ = await resolve(updater(request, auth.current_user, update))
        except Exception as exc:
            return error_response(400, "Profile update failed", str(exc))
        return RedirectResponse(
            url=f"{config.account_path}?saved=1",
            status_code=303,
        )

    return update_profile


def _render_panel(
    templates: Environment,
    request: Request,
    current_user: AuthenticatedSubject,
    panel: PasskeyPanel | None,
) -> str:
    """Render a named host/package template, or omit the optional panel."""
    if panel is None:
        return ""
    context: dict[str, object] = {
        "request": request,
        "current_user": current_user,
        **dict(panel.context),
    }
    return templates.get_template(panel.template_name).render(**context)
