# ruff: noqa: BLE001, PLR0913
"""Shared page-context, CSRF, and mutation helpers for HTMX route groups."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse, RedirectResponse

from my_usermanager.adapters.fastapi_htmx.auth import (
    Denied,
    admin_user,
    current_user,
)
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import resolve_ui_labels
from my_usermanager.adapters.fastapi_htmx.forms import FormError, read_named_form
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.adapters.fastapi_htmx.rows import safe_row

if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import (
        CsrfContext,
        UserManagerUiConfig,
        UserManagerUiHooks,
        UserRow,
    )
    from my_usermanager.subjects import AuthenticatedSubject


class _PageContextHook(Protocol):
    def page_context(
        self, request: Request
    ) -> MaybeAwaitable[Mapping[str, object] | None]: ...


def csrf_inputs(
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


async def page_context(
    hooks: UserManagerUiHooks, request: Request
) -> dict[str, object]:
    """Optional host extras (shell chrome, i18n). Missing hook → empty dict."""
    provider = getattr(hooks, "page_context", None)
    if provider is None:
        return {}
    hook = cast("_PageContextHook", cast("object", hooks))
    raw = await resolve(hook.page_context(request))
    if raw is None:
        return {}
    return dict(raw)


def merge_labels(
    config: UserManagerUiConfig, page_context_values: Mapping[str, object]
) -> dict[str, str]:
    """Merge packaged labels with optional per-request host overrides."""
    raw_overrides = page_context_values.get("labels")
    overrides: Mapping[str, str] | None
    if isinstance(raw_overrides, Mapping):
        untyped_overrides = cast("Mapping[object, object]", raw_overrides)
        overrides = {
            key: value
            for key, value in untyped_overrides.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    else:
        overrides = None
    return resolve_ui_labels(config.labels, overrides=overrides)


async def named_action(  # noqa: PLR0911
    request: Request,
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    hook_name: str,
    required: tuple[str, ...],
    redirect_url: str,
) -> Response:
    """Run a named host mutation hook and redirect on success."""
    auth = (
        await current_user(request, config, hooks)
        if hook_name == "revoke_session"
        else await admin_user(request, config, hooks)
    )
    if isinstance(auth, Denied):
        return auth.response
    callback = getattr(hooks, hook_name, None)
    if not callable(callback):
        return error_response(
            501, "Action unavailable", f"Host did not provide {hook_name}."
        )
    form = await read_named_form(request, required)
    if isinstance(form, FormError):
        return error_response(form.status_code, form.title, form.message)
    csrf_error = await validate_csrf(request, config, form.get("csrf"))
    if csrf_error is not None:
        return csrf_error
    if hook_name == "hard_delete_user" and form["confirmation"] != form["user_id"]:
        return error_response(
            400,
            "Confirmation failed",
            "Confirmation must exactly match the user id.",
        )
    callback_values = (
        (form["user_id"],)
        if hook_name == "hard_delete_user"
        else tuple(form[name] for name in required)
    )
    result = await resolve(callback(request, auth.current_user, *callback_values))
    if hook_name in {"invite_user", "reissue_invitation"}:
        activation_url = getattr(result, "activation_url", None)
        if not isinstance(activation_url, str) or not activation_url:
            return error_response(
                500, "Invitation failed", "Invitation result has no activation URL."
            )
        return RedirectResponse(
            url=f"{redirect_url}?{urlencode({'invitation_url': activation_url})}",
            status_code=303,
        )
    return RedirectResponse(url=redirect_url, status_code=303)


async def validate_csrf(
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


async def row_response(
    templates: Environment,
    request: Request,
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    current_user: AuthenticatedSubject,
    row: UserRow,
    csrf: CsrfContext,
) -> HTMLResponse:
    """Render the swapped admin user row after a mutation."""
    host_context = await page_context(hooks, request)
    labels = merge_labels(config, host_context)
    html = templates.get_template("users/_row.html").render(
        **{
            **host_context,
            "request": request,
            "config": config,
            "user": safe_row(row),
            "role_options": tuple(
                await resolve(hooks.role_options(request, current_user))
            ),
            "capability_options": tuple(
                await resolve(hooks.capability_options(request, current_user))
            ),
            "csrf": csrf,
            "csrf_inputs": csrf_inputs(config, request, csrf),
            "labels": labels,
            "reissue_invitation_enabled": callable(
                getattr(hooks, "reissue_invitation", None)
            ),
            "revoke_invitation_enabled": callable(
                getattr(hooks, "revoke_invitation", None)
            ),
            "soft_delete_enabled": callable(getattr(hooks, "soft_delete_user", None)),
            "hard_delete_enabled": callable(getattr(hooks, "hard_delete_user", None)),
        }
    )
    return HTMLResponse(html)
