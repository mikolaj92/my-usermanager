# ruff: noqa: TC002
"""Admin invitation create/reissue/revoke routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request

from my_usermanager.adapters.fastapi_htmx.auth import Denied, admin_user
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.forms import FormError, read_named_form
from my_usermanager.adapters.fastapi_htmx.page import (
    named_action,
    row_response,
    validate_csrf,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import Response
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.config import (
        UserManagerUiConfig,
        UserManagerUiHooks,
        UserRow,
    )


def add_invitation_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register invite, reissue, and revoke invitation endpoints."""

    async def invite(request: Request) -> Response:
        return await named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="invite_user",
            required=("username", "email", "role"),
            redirect_url=config.users_path,
        )

    async def reissue_invitation(request: Request) -> Response:
        return await named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="reissue_invitation",
            required=("invitation_id",),
            redirect_url=config.users_path,
        )

    async def revoke_invitation(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        callback = getattr(hooks, "revoke_invitation", None)
        if not callable(callback):
            return error_response(
                501,
                "Action unavailable",
                "Host did not provide revoke_invitation.",
            )
        form = await read_named_form(request, ("invitation_id",))
        if isinstance(form, FormError):
            return error_response(form.status_code, form.title, form.message)
        csrf_error = await validate_csrf(request, config, form.get("csrf"))
        if csrf_error is not None:
            return csrf_error
        changed = cast(
            "UserRow",
            await resolve(callback(request, auth.current_user, form["invitation_id"])),
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

    router.add_api_route(config.invite_path, invite, methods=["POST"])
    router.add_api_route(
        config.reissue_invitation_path, reissue_invitation, methods=["POST"]
    )
    router.add_api_route(
        config.revoke_invitation_path, revoke_invitation, methods=["POST"]
    )
