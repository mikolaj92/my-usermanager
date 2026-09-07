# ruff: noqa: TC002
"""Account session list and revoke routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from fastapi import Request
from fastapi.responses import HTMLResponse

from my_usermanager.adapters.fastapi_htmx.auth import Denied, current_user
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.page import (
    csrf_inputs,
    merge_labels,
    named_action,
    page_context,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import (
        SessionRow,
        UserManagerUiConfig,
        UserManagerUiHooks,
    )
    from my_usermanager.subjects import AuthenticatedSubject


class _ListSessionsHook(Protocol):
    def list_sessions(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> MaybeAwaitable[tuple[SessionRow, ...]]: ...


def add_session_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register session list and revoke endpoints."""

    async def sessions(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        provider = getattr(hooks, "list_sessions", None)
        if not callable(provider):
            return error_response(
                501, "Sessions unavailable", "Host did not provide list_sessions."
            )
        host_context = await page_context(hooks, request)
        labels = merge_labels(config, host_context)
        csrf = await resolve(hooks.csrf_context(request))
        html = templates.get_template("sessions/list.html").render(
            **host_context,
            request=request,
            config=config,
            current_user=auth.current_user,
            sessions=tuple(
                await resolve(
                    cast("_ListSessionsHook", cast("object", hooks)).list_sessions(
                        request, auth.current_user
                    )
                )
            ),
            csrf_inputs=csrf_inputs(config, request, csrf),
            static_url_path=config.static_url_path,
            base_template=config.base_template,
            labels=labels,
        )
        return HTMLResponse(html)

    async def revoke_session(request: Request) -> Response:
        return await named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="revoke_session",
            required=("session_id",),
            redirect_url=config.sessions_path,
        )

    router.add_api_route(config.sessions_path, sessions, methods=["GET"])
    router.add_api_route(config.revoke_session_path, revoke_session, methods=["POST"])
