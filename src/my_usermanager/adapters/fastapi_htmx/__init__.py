"""Explicit FastAPI, Jinja, HTMX adapter boundary for user management UI."""

from __future__ import annotations

from typing import Final

_OPTIONAL_IMPORT_ROOTS: Final = frozenset({"fastapi", "jinja2", "starlette"})

try:
    from my_usermanager.adapters.fastapi_htmx.config import (
        CsrfContext,
        UserManagerUiConfig,
        UserManagerUiRouter,
        UserRow,
    )
    from my_usermanager.adapters.fastapi_htmx.ids import row_key_from_user_id
    from my_usermanager.adapters.fastapi_htmx.protocols import UserManagerUiHooks
    from my_usermanager.adapters.fastapi_htmx.router import create_usermanager_ui_router
    from my_usermanager.adapters.fastapi_htmx.static import usermanager_ui_static_files
except ModuleNotFoundError as exc:
    missing_root = (exc.name or "").split(".", maxsplit=1)[0]
    if missing_root in _OPTIONAL_IMPORT_ROOTS:
        message = (
            "Optional dependencies for my_usermanager.adapters.fastapi_htmx "
            "are missing. Install the `my-usermanager[fastapi-htmx]` extra "
            "or add `fastapi>=0.115` and `jinja2>=3.1`."
        )
        raise ImportError(message) from exc
    raise

__all__: Final = (
    "CsrfContext",
    "UserManagerUiConfig",
    "UserManagerUiHooks",
    "UserManagerUiRouter",
    "UserRow",
    "create_usermanager_ui_router",
    "row_key_from_user_id",
    "usermanager_ui_static_files",
)
