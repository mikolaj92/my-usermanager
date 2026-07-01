"""Authentication and admin callback handling for adapter routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.responses import (
    error_response,
    forbidden_response,
)

if TYPE_CHECKING:
    from fastapi.responses import Response

    from my_usermanager.adapters.fastapi_htmx.config import UserManagerUiConfig
    from my_usermanager.adapters.fastapi_htmx.protocols import UserManagerUiHooks
    from my_usermanager.subjects import AuthenticatedSubject


@dataclass(frozen=True, slots=True)
class Authenticated:
    """Authenticated route state."""

    current_user: AuthenticatedSubject


@dataclass(frozen=True, slots=True)
class Denied:
    """Denied route state with a prepared response."""

    response: Response


type AuthResult = Authenticated | Denied


async def current_user(
    request: Request,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> AuthResult:
    """Return the current user or the configured unauthenticated response."""
    user = await resolve(hooks.get_current_user(request))
    if user is not None:
        return Authenticated(user)
    if request.method != "GET" or request.headers.get("HX-Request") == "true":
        return Denied(
            error_response(
                status.HTTP_401_UNAUTHORIZED,
                "Authentication required",
                "Sign in before using this user-management view.",
            ),
        )
    return Denied(RedirectResponse(config.login_url, status.HTTP_303_SEE_OTHER))


async def admin_user(
    request: Request,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> AuthResult:
    """Return the current admin user or an auth/admin denial response."""
    auth = await current_user(request, config, hooks)
    match auth:
        case Denied():
            return auth
        case Authenticated(current_user=user):
            try:
                await resolve(hooks.require_admin(request, user))
            except HTTPException as exc:
                if exc.status_code != status.HTTP_403_FORBIDDEN:
                    raise
                return Denied(forbidden_response())
            except PermissionError:
                return Denied(forbidden_response())
            return auth
