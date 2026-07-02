"""Optional FastAPI dependencies for session principals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from fastapi import HTTPException, Request, status

from my_usermanager.sessions import (
    SESSION_PRINCIPAL_KEY,
    SessionPrincipal,
    clear_session_principal,
    read_session_principal,
    write_session_principal,
)

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

__all__: Final[tuple[str, ...]] = (
    "clear_current_user",
    "current_user",
    "current_user_dependency",
    "require_user",
    "require_user_dependency",
    "write_current_user",
)


def current_user(request: Request) -> SessionPrincipal | None:
    """FastAPI dependency returning the current session principal if present."""
    return read_session_principal(_session(request))


def require_user(request: Request) -> SessionPrincipal:
    """FastAPI dependency requiring an authenticated session principal."""
    principal = current_user(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return principal


def current_user_dependency(
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> Callable[[Request], SessionPrincipal | None]:
    """Return a current-user dependency for a custom session key."""

    def dependency(request: Request) -> SessionPrincipal | None:
        return read_session_principal(_session(request), key=key)

    return dependency


def require_user_dependency(
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> Callable[[Request], SessionPrincipal]:
    """Return a require-user dependency for a custom session key."""

    def dependency(request: Request) -> SessionPrincipal:
        principal = read_session_principal(_session(request), key=key)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return principal

    return dependency


def write_current_user(
    request: Request,
    principal: SessionPrincipal,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> SessionPrincipal:
    """Write a principal into request.session."""
    return write_session_principal(_session(request), principal, key=key)


def clear_current_user(
    request: Request,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> None:
    """Clear the principal from request.session."""
    clear_session_principal(_session(request), key=key)


def _session(request: Request) -> MutableMapping[str, object]:
    try:
        session = request.session
    except AssertionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SessionMiddleware is required",
        ) from exc
    return cast("MutableMapping[str, object]", session)
