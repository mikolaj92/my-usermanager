# ruff: noqa: TC002
"""Admin audit-log route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from fastapi import Request
from fastapi.responses import HTMLResponse

from my_usermanager.adapters.fastapi_htmx.auth import Denied, admin_user
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.page import merge_labels, page_context
from my_usermanager.adapters.fastapi_htmx.responses import error_response

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import (
        AuditRow,
        UserManagerUiConfig,
        UserManagerUiHooks,
    )
    from my_usermanager.subjects import AuthenticatedSubject


class _ListAuditHook(Protocol):
    def list_audit_events(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> MaybeAwaitable[tuple[AuditRow, ...]]: ...


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
        host_context = await page_context(hooks, request)
        labels = merge_labels(config, host_context)
        html = templates.get_template("audit/list.html").render(
            **host_context,
            request=request,
            config=config,
            current_user=auth.current_user,
            events=tuple(
                await resolve(
                    cast("_ListAuditHook", cast("object", hooks)).list_audit_events(
                        request, auth.current_user
                    )
                )
            ),
            static_url_path=config.static_url_path,
            base_template=config.base_template,
            labels=labels,
        )
        return HTMLResponse(html)

    router.add_api_route(config.audit_path, audit, methods=["GET"])
