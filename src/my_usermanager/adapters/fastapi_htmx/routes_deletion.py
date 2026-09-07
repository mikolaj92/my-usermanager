# ruff: noqa: TC002
"""Admin soft-delete and hard-delete routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from my_usermanager.adapters.fastapi_htmx.page import named_action

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import Response

    from my_usermanager.adapters.fastapi_htmx.config import (
        UserManagerUiConfig,
        UserManagerUiHooks,
    )


def add_deletion_routes(
    router: APIRouter,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    """Register soft-delete and hard-delete endpoints."""

    async def soft_delete(request: Request) -> Response:
        return await named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="soft_delete_user",
            required=("user_id",),
            redirect_url=config.users_path,
        )

    async def hard_delete(request: Request) -> Response:
        return await named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="hard_delete_user",
            required=("user_id", "confirmation"),
            redirect_url=config.users_path,
        )

    router.add_api_route(config.soft_delete_user_path, soft_delete, methods=["POST"])
    router.add_api_route(config.hard_delete_user_path, hard_delete, methods=["POST"])
