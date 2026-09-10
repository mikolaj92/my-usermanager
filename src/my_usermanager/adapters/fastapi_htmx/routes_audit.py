# ruff: noqa: TC002
"""Admin audit-log route."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse

from my_usermanager.adapters.fastapi_htmx.auth import Denied, admin_user
from my_usermanager.adapters.fastapi_htmx.page import (
    invalid_page_response,
    is_htmx_request,
    load_audit_page,
    merge_labels,
    page_context,
    pager_state,
    parse_audit_filters,
    parse_page_query,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.models import ValidationError
from my_usermanager.stores import InvalidPageError

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.config import (
        UserManagerUiConfig,
        UserManagerUiHooks,
    )


def add_audit_route(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register the administrative audit log page."""

    async def audit(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        provider = getattr(hooks, "list_audit_events", None)
        if not callable(provider):
            return error_response(
                501, "Audit unavailable", "Host did not provide list_audit_events."
            )
        try:
            page_query = parse_page_query(request)
            filters = parse_audit_filters(request)
            events_page = await load_audit_page(
                provider,
                request,
                auth.current_user,
                page=page_query,
                filters=filters,
            )
        except (InvalidPageError, ValidationError) as error:
            return invalid_page_response(error)
        host_context = await page_context(hooks, request)
        labels = merge_labels(config, host_context)
        context = {
            **host_context,
            "request": request,
            "config": config,
            "current_user": auth.current_user,
            "events": events_page.items,
            "static_url_path": config.static_url_path,
            "base_template": config.base_template,
            "labels": labels,
            "query_params": page_query.query_params,
            "filter_query": urlencode(page_query.query_params),
            "pager": pager_state(events_page, page_query.page),
            "results_url": config.audit_path,
        }
        template_name = (
            "audit/_results.html" if is_htmx_request(request) else "audit/list.html"
        )
        return HTMLResponse(templates.get_template(template_name).render(**context))

    router.add_api_route(config.audit_path, audit, methods=["GET"])
