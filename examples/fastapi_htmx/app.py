"""Optional FastAPI composition host for the reusable HTMX UI adapters."""

from __future__ import annotations

from typing import Final
from warnings import filterwarnings

from app_factory import PlatformConfig, PlatformPaths
from app_factory.adapters import (
    PasskeyBinding,
    UserManagerBinding,
    install_identity_adapters,
)
from fastapi import APIRouter, FastAPI, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from my_auth.fastapi_htmx import PasskeyUiConfig

from examples.fastapi_htmx.demo_passkeys import (
    PASSKEY_PATHS,
    _demo_passkey_service,
    _passkey_hooks,
)
from examples.fastapi_htmx.demo_usermanager import (
    _demo_csrf_protection,
    _usermanager_hooks,
)
from examples.fastapi_htmx.demo_users import DEMO_CSRF_HEADER
from examples.fastapi_htmx.demo_users import DEMO_UNSAFE_USER_ID as _DEMO_UNSAFE_USER_ID
from my_usermanager.adapters.fastapi_htmx import UserManagerUiConfig

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
    paths = PlatformPaths(
        login=PASSKEY_PATHS.login_page,
        logout=PASSKEY_PATHS.logout,
        register=PASSKEY_PATHS.register_page,
        recovery=PASSKEY_PATHS.recovery_page,
        activation=PASSKEY_PATHS.activation_page,
        credentials=PASSKEY_PATHS.credentials_page,
        account="/account",
        admin_users="/admin/users",
        invite="/admin/users",
    )
    _ = install_identity_adapters(
        demo_app,
        environments=(),
        config=PlatformConfig(
            app_name="my-usermanager demo",
            paths=paths,
            enable_account=True,
            enable_credentials=True,
            enable_admin_users=True,
            enable_invite=True,
        ),
        passkey=PasskeyBinding(
            service=_demo_passkey_service(),
            hooks=_passkey_hooks(),
            ui_config=PasskeyUiConfig(
                paths=PASSKEY_PATHS,
                csrf_header_name=DEMO_CSRF_HEADER,
                csrf_token=lambda _request: "demo-noop-csrf",
            ),
        ),
        usermanager=UserManagerBinding(
            hooks=_usermanager_hooks(),
            ui_config=UserManagerUiConfig(
                login_url=PASSKEY_PATHS.login_page,
                csrf_protection=_demo_csrf_protection(),
            ),
        ),
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
