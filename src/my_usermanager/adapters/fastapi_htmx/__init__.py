"""Explicit FastAPI, Jinja, HTMX adapter boundary for user management UI."""

from __future__ import annotations

from typing import Final

_OPTIONAL_IMPORT_ROOTS: Final = frozenset(
    {"fastapi", "jinja2", "starlette", "app_factory"}
)
try:
    from my_usermanager.adapters.fastapi_htmx.config import (
        DEFAULT_UI_LABELS,
        AuditRow,
        CapabilityOption,
        CsrfContext,
        CsrfProtection,
        ExternalIdentityRow,
        InvitationResult,
        PasskeyPanel,
        PermissionGrantRow,
        SessionRow,
        UserManagerUi,
        UserManagerUiConfig,
        UserManagerUiConflict,
        UserManagerUiRouter,
        UserRow,
        resolve_ui_labels,
    )
    from my_usermanager.adapters.fastapi_htmx.ids import row_key_from_user_id
    from my_usermanager.adapters.fastapi_htmx.protocols import UserManagerUiHooks
    from my_usermanager.adapters.fastapi_htmx.router import install_usermanager_ui
except (ModuleNotFoundError, ImportError) as exc:
    if (exc.name or "").split(".", 1)[
        0
    ] in _OPTIONAL_IMPORT_ROOTS or "app-factory" in str(exc):
        message = (
            "Optional dependencies for my_usermanager.adapters.fastapi_htmx are "
            "missing; install the fastapi-htmx extra and app-factory[fastapi]."
        )
        raise ImportError(message) from exc
    raise
__all__: Final = (
    "DEFAULT_UI_LABELS",
    "AuditRow",
    "CapabilityOption",
    "CsrfContext",
    "CsrfProtection",
    "ExternalIdentityRow",
    "InvitationResult",
    "PasskeyPanel",
    "PermissionGrantRow",
    "SessionRow",
    "UserManagerUi",
    "UserManagerUiConfig",
    "UserManagerUiConflict",
    "UserManagerUiHooks",
    "UserManagerUiRouter",
    "UserRow",
    "install_usermanager_ui",
    "resolve_ui_labels",
    "row_key_from_user_id",
)
