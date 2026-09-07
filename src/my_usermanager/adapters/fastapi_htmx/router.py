# ruff: noqa: TRY003, EM101
"""FastAPI routes for the reusable server-rendered user-manager UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_factory.fastapi import AppFactoryUi, install_app_factory_ui
from fastapi import APIRouter, FastAPI

from my_usermanager.adapters.fastapi_htmx.config import (
    UserManagerUi,
    UserManagerUiConfig,
    UserManagerUiConflict,
    UserManagerUiHooks,
    UserManagerUiRouter,
)
from my_usermanager.adapters.fastapi_htmx.routes_account import add_account_routes
from my_usermanager.adapters.fastapi_htmx.routes_admin import (
    add_admin_mutation_routes,
    add_admin_users_page,
)
from my_usermanager.adapters.fastapi_htmx.routes_audit import add_audit_route
from my_usermanager.adapters.fastapi_htmx.routes_deletion import add_deletion_routes
from my_usermanager.adapters.fastapi_htmx.routes_invitations import (
    add_invitation_routes,
)
from my_usermanager.adapters.fastapi_htmx.routes_sessions import add_session_routes
from my_usermanager.adapters.fastapi_htmx.static import (
    ensure_static_mount_available,
    usermanager_ui_static_files,
)
from my_usermanager.adapters.fastapi_htmx.templates import (
    attach_package_templates,
    create_template_environment,
)

if TYPE_CHECKING:
    from jinja2 import Environment


def install_usermanager_ui(
    app: FastAPI,
    *,
    platform: AppFactoryUi,
    hooks: UserManagerUiHooks,
    config: UserManagerUiConfig | None = None,
    environment: Environment | None = None,
) -> UserManagerUi:
    """Install the user-manager routes and shared platform UI assets.

    When ``environment`` is omitted the adapter builds a package-only Jinja
    environment (default hosts / demos). Hosts that own product chrome may pass
    their Jinja environment; packaged templates are attached with host loaders
    first so a host ``base.html`` (or ``config.base_template``) can wrap pages.
    """
    selected = config or UserManagerUiConfig()
    installed_platform = getattr(app.state, "app_factory_ui", None)
    if installed_platform != platform:
        raise UserManagerUiConflict("app-factory UI platform is not installed")
    existing = getattr(app.state, "usermanager_ui", None)
    if isinstance(existing, UserManagerUi):
        if (
            existing.platform is not platform
            or existing.config != selected
            or existing.hooks is not hooks
        ):
            raise UserManagerUiConflict(
                "a different usermanager UI is already installed"
            )
        return existing
    if environment is None:
        templates = create_template_environment()
    else:
        templates = attach_package_templates(environment)
    _ = install_app_factory_ui(
        app,
        environments=[templates],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    result = create_usermanager_ui_router(
        config=selected, hooks=hooks, environment=templates
    )
    ensure_static_mount_available(app, result.static_mount_path)
    app.include_router(result.router)
    app.mount(result.static_mount_path, result.static_files, name="my-usermanager-ui")
    installed = UserManagerUi(
        result.router,
        result.static_mount_path,
        result.static_files,
        platform,
        selected,
        hooks,
    )
    app.state.usermanager_ui = installed
    return installed


def create_usermanager_ui_router(
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    environment: Environment | None = None,
) -> UserManagerUiRouter:
    """Compose enabled account and administrative route groups."""
    templates = environment or create_template_environment()
    router = APIRouter()
    if config.account_enabled:
        add_account_routes(router, templates, config, hooks)
        add_session_routes(router, templates, config, hooks)
    if config.admin_enabled:
        add_admin_users_page(router, templates, config, hooks)
        add_admin_mutation_routes(router, templates, config, hooks)
        add_invitation_routes(router, templates, config, hooks)
        add_deletion_routes(router, config, hooks)
        add_audit_route(router, templates, config, hooks)
    return UserManagerUiRouter(
        router, config.static_mount_path, usermanager_ui_static_files()
    )
