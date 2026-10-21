# ruff: noqa: BLE001, PLR0913, TC002
"""Shared page-context, CSRF, and mutation helpers for HTMX route groups."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from inspect import Parameter, signature
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from my_usermanager.adapters.fastapi_htmx.auth import (
    Denied,
    admin_user,
    current_user,
)
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import (
    AuditPage,
    UserPage,
    resolve_ui_labels,
)
from my_usermanager.adapters.fastapi_htmx.forms import FormError, read_named_form
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.adapters.fastapi_htmx.rows import safe_row
from my_usermanager.models import ValidationError
from my_usermanager.stores import AuditFilters, InvalidPageError, UserQuery

if TYPE_CHECKING:
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import (
        AuditRow,
        CsrfContext,
        UserManagerUiConfig,
        UserManagerUiHooks,
        UserRow,
    )
    from my_usermanager.subjects import AuthenticatedSubject

_MAX_PAGE_LIMIT = 200
_ACCOUNT_STATUSES = frozenset({"pending", "active", "disabled", "deleted"})
_USER_FILTER_FIELDS = ("q", "status", "limit")
_AUDIT_FILTER_FIELDS = ("actor_id", "action", "since", "until", "limit")


class _PageContextHook(Protocol):
    def page_context(
        self, request: Request
    ) -> MaybeAwaitable[Mapping[str, object] | None]: ...


@dataclass(frozen=True, slots=True)
class PageQuery:
    """Parsed GET pagination plus preserved filter query."""

    limit: int | None
    offset: int
    page: int
    query_params: dict[str, str]


def is_htmx_request(request: Request) -> bool:
    """True when the client asked for an HTMX fragment."""
    return request.headers.get("HX-Request") == "true"


def parse_page_query(request: Request) -> PageQuery:
    """Parse page/limit from GET query; raise InvalidPageError on bad values."""
    raw_page = _optional_text(request.query_params.get("page"))
    raw_limit = _optional_text(request.query_params.get("limit"))
    page = 1 if raw_page is None else _positive_int(raw_page, field_name="page")
    limit = None if raw_limit is None else _positive_int(raw_limit, field_name="limit")
    if limit is not None and limit > _MAX_PAGE_LIMIT:
        field_name = "limit"
        reason = "must be between 1 and page_size"
        raise InvalidPageError(field_name, limit, reason)
    offset = (page - 1) * (1 if limit is None else limit)
    return PageQuery(
        limit=limit,
        offset=offset,
        page=page,
        query_params=_query_params(request),
    )


def parse_user_query(request: Request) -> UserQuery:
    """Map GET filters onto UserQuery fields the store already understands."""
    text = _optional_text(request.query_params.get("q"))
    status = _optional_text(request.query_params.get("status"))
    if status is not None and status not in _ACCOUNT_STATUSES:
        field_name = "status"
        reason = "must be a known account status"
        raise ValidationError(field_name, reason)
    return UserQuery(text=text, status=status)


def parse_audit_filters(request: Request) -> AuditFilters:
    """Map GET filters onto AuditFilters fields the store already understands."""
    actor_id = _optional_text(request.query_params.get("actor_id"))
    action = _optional_text(request.query_params.get("action"))
    since = _optional_timestamp(request.query_params.get("since"), field_name="since")
    until = _optional_timestamp(request.query_params.get("until"), field_name="until")
    return AuditFilters(actor_id=actor_id, action=action, since=since, until=until)


async def load_user_page(
    hooks: UserManagerUiHooks,
    request: Request,
    current_user: AuthenticatedSubject,
    *,
    page: PageQuery,
    query: UserQuery,
) -> UserPage:
    """Call list_users with optional kwargs; wrap a legacy sequence."""
    raw = await _call_list_hook(
        hooks.list_users,
        request,
        current_user,
        limit=page.limit,
        offset=page.offset,
        extra={"query": query},
    )
    if isinstance(raw, UserPage):
        return raw
    items = tuple(safe_row(cast("UserRow", row)) for row in _as_sequence(raw))
    return UserPage(
        items=items,
        limit=len(items) if page.limit is None else page.limit,
        offset=page.offset,
        has_previous=page.offset > 0,
        has_next=False,
        filtered=query != UserQuery(),
    )


async def load_audit_page(
    provider: Callable[..., object],
    request: Request,
    current_user: AuthenticatedSubject,
    *,
    page: PageQuery,
    filters: AuditFilters,
) -> AuditPage:
    """Call list_audit_events with optional kwargs; wrap a legacy sequence."""
    raw = await _call_list_hook(
        provider,
        request,
        current_user,
        limit=page.limit,
        offset=page.offset,
        extra={"filters": filters},
    )
    if isinstance(raw, AuditPage):
        return raw
    items = tuple(cast("Sequence[AuditRow]", _as_sequence(raw)))
    return AuditPage(
        items=items,
        limit=len(items) if page.limit is None else page.limit,
        offset=page.offset,
        has_previous=page.offset > 0,
        has_next=False,
        filtered=filters != AuditFilters(),
    )


def pager_state(page: UserPage | AuditPage, current_page: int) -> dict[str, object]:
    """Expose prev/next pages for the shared app-factory pagination macro."""
    total_pages = current_page
    if page.has_next:
        total_pages = current_page + 1
    elif page.has_previous:
        total_pages = max(current_page, 2)
    return {
        "current_page": current_page,
        "total_pages": total_pages,
        "has_previous": page.has_previous,
        "has_next": page.has_next,
        "filtered": page.filtered,
    }


def invalid_page_response(error: InvalidPageError | ValidationError) -> HTMLResponse:
    """Return HTTP 400 for malformed filter/page query."""
    return error_response(400, "Invalid page", str(error))


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _positive_int(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        parsed = 0
    if parsed < 1:
        raise InvalidPageError(field_name, parsed, "must be a positive integer")
    return parsed


def _optional_timestamp(value: str | None, *, field_name: str) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(field_name, "must be an ISO-8601 timestamp") from exc
    return parsed


def _query_params(request: Request) -> dict[str, str]:
    params: dict[str, str] = {}
    for key in (*_USER_FILTER_FIELDS, *_AUDIT_FILTER_FIELDS):
        value = _optional_text(request.query_params.get(key))
        if value is not None:
            params[key] = value
    return params


async def _call_list_hook(
    callback: Callable[..., object],
    request: Request,
    current_user: AuthenticatedSubject,
    *,
    limit: int | None,
    offset: int,
    extra: Mapping[str, object],
) -> object:
    kwargs: dict[str, object] = {}
    accepted = {
        Parameter.KEYWORD_ONLY,
        Parameter.POSITIONAL_OR_KEYWORD,
        Parameter.VAR_KEYWORD,
    }
    try:
        parameters = dict(signature(callback).parameters)
    except (TypeError, ValueError):
        parameters = {}
    if "limit" in parameters:
        kwargs["limit"] = limit
    if "offset" in parameters:
        kwargs["offset"] = offset
    kwargs.update(
        {
            name: value
            for name, value in extra.items()
            if name in parameters
            and getattr(parameters[name], "kind", None) in accepted
        }
    )
    try:
        result: object = callback(request, current_user, **kwargs)
        return await resolve(result)
    except TypeError:
        result = callback(request, current_user)
        return await resolve(result)


def _as_sequence(raw: object) -> Sequence[object]:
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return raw
    message = "list hook must return a sequence or page"
    raise TypeError(message)


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
        return _invitation_redirect(result, redirect_url)
    return RedirectResponse(url=redirect_url, status_code=303)


def _invitation_redirect(result: object, redirect_url: str) -> Response:
    """Redirect after invite/reissue without leaking tokens into query by default."""
    delivery_status = getattr(result, "delivery_status", "manual")
    if delivery_status not in {"manual", "delivered", "delivery_failed"}:
        delivery_status = "manual"
    reveal = getattr(result, "reveal_activation_url", None)
    if not isinstance(reveal, bool):
        reveal = delivery_status == "manual"
    activation_url = getattr(result, "activation_url", None)
    if reveal and (not isinstance(activation_url, str) or not activation_url):
        return error_response(
            500, "Invitation failed", "Invitation result has no activation URL."
        )
    params: dict[str, str] = {"invitation_delivery": str(delivery_status)}
    if reveal and isinstance(activation_url, str) and activation_url:
        params["invitation_url"] = activation_url
    return RedirectResponse(
        url=f"{redirect_url}?{urlencode(params)}",
        status_code=303,
    )


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
