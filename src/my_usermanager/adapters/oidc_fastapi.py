"""Optional FastAPI callback for a generic OpenID Connect relying party.

The host remains a relying party: this router consumes a one-time authorization
flow, verifies an RS256 ID token, and maps ``(issuer, sub)`` onto an existing
local user. It does not mint tokens or implement an OpenID Provider.
"""

from __future__ import annotations

from time import time
from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from my_usermanager.adapters.fastapi import write_current_user
from my_usermanager.adapters.oidc import complete_authorization_code

if TYPE_CHECKING:
    from collections.abc import Callable

    from my_usermanager.adapters.oidc import (
        OidcCodeRedeemer,
        OidcFlowStore,
        OidcRelyingParty,
    )

__all__: Final[tuple[str, ...]] = ("build_oidc_callback_router",)

_UNAVAILABLE: Final = "authentication unavailable"


def build_oidc_callback_router(
    *,
    relying_party: OidcRelyingParty,
    flows: OidcFlowStore,
    redeem_code: OidcCodeRedeemer,
    now: Callable[[], float] | None = None,
    success_url: str = "/",
) -> APIRouter:
    """Return a same-origin `/oidc/callback` router for a configured issuer."""
    clock = now or time
    router = APIRouter()

    @router.get("/oidc/callback")
    def oidc_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
    ) -> RedirectResponse:
        if not code or not state:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_UNAVAILABLE,
            )
        try:
            principal = complete_authorization_code(
                code=code,
                state=state,
                flows=flows,
                redeem_code=redeem_code,
                relying_party=relying_party,
                now=clock(),
            )
        except PermissionError as extra:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_UNAVAILABLE,
            ) from extra
        write_current_user(request, principal)
        return RedirectResponse(success_url, status_code=302)

    return router
