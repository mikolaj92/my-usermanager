"""Optional FastAPI composition host for the reusable HTMX UI adapters."""

from __future__ import annotations

from typing import Final
from warnings import filterwarnings

from fastapi import APIRouter, FastAPI, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from my_auth.fastapi_htmx import PasskeyUiConfig, create_passkey_ui_router

from examples.fastapi_htmx.demo_passkeys import (
    PASSKEY_PATHS,
    _demo_passkey_service,
    _passkey_hooks,
)
from examples.fastapi_htmx.demo_usermanager import _demo_csrf_token, _usermanager_hooks
from examples.fastapi_htmx.demo_users import (
    DEMO_CSRF_HEADER,
)
from examples.fastapi_htmx.demo_users import (
    DEMO_UNSAFE_USER_ID as _DEMO_UNSAFE_USER_ID,
)
from my_usermanager.adapters.fastapi_htmx import (
    UserManagerUiConfig,
    create_usermanager_ui_router,
)

_HOST_ROUTER: Final = APIRouter()
DEMO_UNSAFE_USER_ID: Final = _DEMO_UNSAFE_USER_ID
_FAVICON_SVG: Final = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path d="M3 4h10v2H3zm0 3h10v2H3zm0 3h7v2H3z" '
    'fill="currentColor"/>'
    "</svg>"
)

filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated*",
    module="fastapi.testclient",
)


def create_app() -> FastAPI:
    """Create the optional no-build adapter composition example."""
    demo_app = FastAPI(title="my-usermanager FastAPI HTMX adapter composition example")
    passkey_ui = create_passkey_ui_router(
        service=_demo_passkey_service(),
        hooks=_passkey_hooks(),
        config=PasskeyUiConfig(
            paths=PASSKEY_PATHS,
            csrf_header_name=DEMO_CSRF_HEADER,
            csrf_token=_demo_csrf_token,
        ),
    )
    usermanager_ui = create_usermanager_ui_router(
        config=UserManagerUiConfig(login_url=PASSKEY_PATHS.login_page),
        hooks=_usermanager_hooks(),
    )
    demo_app.include_router(passkey_ui.router)
    demo_app.mount(
        passkey_ui.static_mount_path,
        passkey_ui.static_files,
        name="my_auth_fastapi_htmx_static",
    )
    demo_app.include_router(usermanager_ui.router)
    demo_app.mount(
        usermanager_ui.static_mount_path,
        usermanager_ui.static_files,
        name="my_usermanager_fastapi_htmx_static",
    )
    demo_app.include_router(_HOST_ROUTER)
    return demo_app


@_HOST_ROUTER.get("/", include_in_schema=False)
def _root() -> RedirectResponse:
    return RedirectResponse(
        url=PASSKEY_PATHS.login_page,
        status_code=status.HTTP_303_SEE_OTHER,
    )


@_HOST_ROUTER.get("/health", response_class=PlainTextResponse)
def _health() -> str:
    return "ok"


@_HOST_ROUTER.get("/favicon.ico", include_in_schema=False)
def _favicon() -> Response:
    return Response(
        content=_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


app: Final = create_app()
