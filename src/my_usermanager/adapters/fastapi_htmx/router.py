# ruff: noqa: TRY003, EM101, BLE001, PLR0913
"""FastAPI routes for the reusable server-rendered user-manager UI."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlencode

from app_factory.fastapi import AppFactoryUi, install_app_factory_ui
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from my_usermanager.adapters.fastapi_htmx.auth import (
    Denied,
    admin_user,
    current_user,
)
from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import (
    CsrfContext,
    PasskeyPanel,
    PermissionGrantRow,
    UserManagerUi,
    UserManagerUiConfig,
    UserManagerUiConflict,
    UserManagerUiRouter,
    UserRow,
    resolve_ui_labels,
)
from my_usermanager.adapters.fastapi_htmx.forms import (
    FormError,
    MutationForm,
    read_grant_form,
    read_mutation_form,
    read_named_form,
    read_profile_form,
)
from my_usermanager.adapters.fastapi_htmx.protocols import (  # noqa: TC001
    UserManagerUiHooks,
)
from my_usermanager.adapters.fastapi_htmx.responses import error_response
from my_usermanager.adapters.fastapi_htmx.rows import safe_row
from my_usermanager.adapters.fastapi_htmx.static import (
    ensure_static_mount_available,
    usermanager_ui_static_files,
)
from my_usermanager.adapters.fastapi_htmx.templates import (
    attach_package_templates,
    create_template_environment,
)
from my_usermanager.manager import UserProfileUpdate

if TYPE_CHECKING:
    from jinja2 import Environment

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.adapters.fastapi_htmx.config import AuditRow, SessionRow
    from my_usermanager.subjects import AuthenticatedSubject


class _PageContextHook(Protocol):
    def page_context(
        self, request: Request
    ) -> MaybeAwaitable[Mapping[str, object] | None]: ...


class _ListSessionsHook(Protocol):
    def list_sessions(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> MaybeAwaitable[tuple[SessionRow, ...]]: ...


class _ListAuditHook(Protocol):
    def list_audit_events(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> MaybeAwaitable[tuple[AuditRow, ...]]: ...


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


def _csrf_inputs(
    config: UserManagerUiConfig, request: Request, context: CsrfContext
) -> tuple[tuple[str, str], ...]:
    """Merge host fields while reserving the authoritative csrf field."""
    inputs = tuple(
        (name, value) for name, value in context.hidden_inputs if name != "csrf"
    )
    protection = config.csrf_protection
    if protection is None:
        return inputs
    return (*inputs, ("csrf", protection.token(request)))


async def _page_context(
    hooks: UserManagerUiHooks, request: Request
) -> dict[str, object]:
    """Optional host extras (shell chrome, i18n). Missing hook → empty dict."""
    provider = getattr(hooks, "page_context", None)
    if provider is None:
        return {}
    hook = cast("_PageContextHook", cast("object", hooks))
    raw = await resolve(hook.page_context(request))
    if raw is None:
        return {}
    return dict(raw)


def _merge_labels(
    config: UserManagerUiConfig, page_context: Mapping[str, object]
) -> dict[str, str]:
    raw_overrides = page_context.get("labels")
    overrides: Mapping[str, str] | None
    if isinstance(raw_overrides, Mapping):
        untyped_overrides = cast("Mapping[object, object]", raw_overrides)
        overrides = {
            key: value
            for key, value in untyped_overrides.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    else:
        overrides = None
    return resolve_ui_labels(config.labels, overrides=overrides)


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
        _add_account_routes(router, templates, config, hooks)
        _add_session_routes(router, templates, config, hooks)
    if config.admin_enabled:
        _add_admin_users_page(router, templates, config, hooks)
        _add_admin_mutation_routes(router, templates, config, hooks)
        _add_admin_invitation_routes(router, templates, config, hooks)
        _add_admin_deletion_routes(router, config, hooks)
        _add_audit_route(router, templates, config, hooks)
    return UserManagerUiRouter(
        router, config.static_mount_path, usermanager_ui_static_files()
    )


def _add_account_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    router.add_api_route(
        config.account_path,
        _account_endpoint(templates, config, hooks),
        methods=["GET"],
    )
    router.add_api_route(
        config.profile_path,
        _profile_endpoint(config, hooks),
        methods=["POST"],
    )


def _account_endpoint(
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> Callable[[Request], MaybeAwaitable[Response]]:
    async def account(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        host_context = await _page_context(hooks, request)
        if "platform_paths" not in host_context:
            return error_response(
                500,
                "Platform session unavailable",
                "Host page_context must provide platform_paths.",
            )
        labels = _merge_labels(config, host_context)
        panel = await resolve(hooks.render_passkey_panel(request, auth.current_user))
        panel_html = _render_panel(templates, request, auth.current_user, panel)
        profile_editable = callable(getattr(hooks, "update_own_profile", None))
        csrf_inputs: tuple[tuple[str, str], ...] = ()
        if profile_editable and config.csrf_protection is not None:
            csrf = await resolve(hooks.csrf_context(request))
            csrf_inputs = _csrf_inputs(config, request, csrf)
        html = templates.get_template("account/index.html").render(
            **{
                **host_context,
                "request": request,
                "config": config,
                "current_user": auth.current_user,
                "passkey_panel_html": panel_html,
                "static_url_path": config.static_url_path,
                "base_template": config.base_template,
                "labels": labels,
                "profile_editable": profile_editable,
                "csrf_inputs": csrf_inputs,
                "profile_message": request.query_params.get("saved")
                and labels["profile_saved"],
                "profile_error": None,
            }
        )
        return HTMLResponse(html)

    return account


def _profile_endpoint(
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> Callable[[Request], MaybeAwaitable[Response]]:
    async def update_profile(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        updater = getattr(hooks, "update_own_profile", None)
        if not callable(updater):
            return error_response(
                501,
                "Profile update unavailable",
                "Host did not provide update_own_profile.",
            )
        form = await read_profile_form(request)
        if isinstance(form, FormError):
            return error_response(form.status_code, form.title, form.message)
        if config.csrf_protection is not None:
            csrf_error = await _validate_csrf(request, config, form.csrf_token)
            if csrf_error is not None:
                return csrf_error
        try:
            update = UserProfileUpdate(
                username=form.username,
                first_name=form.first_name,
                last_name=form.last_name,
                display_name=form.display_name,
                email=form.email,
                birth_date=form.birth_date,
                gender=form.gender,
            )
            _ = await resolve(updater(request, auth.current_user, update))
        except Exception as exc:
            return error_response(400, "Profile update failed", str(exc))
        return RedirectResponse(
            url=f"{config.account_path}?saved=1",
            status_code=303,
        )

    return update_profile


def _add_session_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    async def sessions(request: Request) -> Response:
        auth = await current_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        provider = getattr(hooks, "list_sessions", None)
        if not callable(provider):
            return error_response(
                501, "Sessions unavailable", "Host did not provide list_sessions."
            )
        host_context = await _page_context(hooks, request)
        labels = _merge_labels(config, host_context)
        csrf = await resolve(hooks.csrf_context(request))
        html = templates.get_template("sessions/list.html").render(
            **host_context,
            request=request,
            config=config,
            current_user=auth.current_user,
            sessions=tuple(
                await resolve(
                    cast("_ListSessionsHook", cast("object", hooks)).list_sessions(
                        request, auth.current_user
                    )
                )
            ),
            csrf_inputs=_csrf_inputs(config, request, csrf),
            static_url_path=config.static_url_path,
            base_template=config.base_template,
            labels=labels,
        )
        return HTMLResponse(html)

    async def revoke_session(request: Request) -> Response:
        return await _named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="revoke_session",
            required=("session_id",),
            redirect_url=config.sessions_path,
        )

    router.add_api_route(config.sessions_path, sessions, methods=["GET"])
    router.add_api_route(config.revoke_session_path, revoke_session, methods=["POST"])


def _add_audit_route(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    async def audit(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        provider = getattr(hooks, "list_audit_events", None)
        if not callable(provider):
            return error_response(
                501, "Audit unavailable", "Host did not provide list_audit_events."
            )
        host_context = await _page_context(hooks, request)
        labels = _merge_labels(config, host_context)
        html = templates.get_template("audit/list.html").render(
            **host_context,
            request=request,
            config=config,
            current_user=auth.current_user,
            events=tuple(
                await resolve(
                    cast("_ListAuditHook", cast("object", hooks)).list_audit_events(
                        request, auth.current_user
                    )
                )
            ),
            static_url_path=config.static_url_path,
            base_template=config.base_template,
            labels=labels,
        )
        return HTMLResponse(html)

    router.add_api_route(config.audit_path, audit, methods=["GET"])


def _add_admin_users_page(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    async def users(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        host_context = await _page_context(hooks, request)
        labels = _merge_labels(config, host_context)
        csrf = await resolve(hooks.csrf_context(request))
        html = templates.get_template("users/list.html").render(
            **{
                **host_context,
                "request": request,
                "config": config,
                "current_user": auth.current_user,
                "users": tuple(
                    safe_row(row)
                    for row in await resolve(
                        hooks.list_users(request, auth.current_user)
                    )
                ),
                "role_options": tuple(
                    await resolve(hooks.role_options(request, auth.current_user))
                ),
                "capability_options": tuple(
                    await resolve(hooks.capability_options(request, auth.current_user))
                ),
                "csrf": csrf,
                "csrf_inputs": _csrf_inputs(config, request, csrf),
                "static_url_path": config.static_url_path,
                "base_template": config.base_template,
                "labels": labels,
                "invitation_url": request.query_params.get("invitation_url"),
                "invite_enabled": callable(getattr(hooks, "invite_user", None)),
                "reissue_invitation_enabled": callable(
                    getattr(hooks, "reissue_invitation", None)
                ),
                "revoke_invitation_enabled": callable(
                    getattr(hooks, "revoke_invitation", None)
                ),
                "soft_delete_enabled": callable(
                    getattr(hooks, "soft_delete_user", None)
                ),
                "hard_delete_enabled": callable(
                    getattr(hooks, "hard_delete_user", None)
                ),
            }
        )
        return HTMLResponse(html)

    router.add_api_route(config.users_path, users, methods=["GET"])


def _add_admin_mutation_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    for path, kind in (
        (config.disable_user_path, "disable"),
        (config.enable_user_path, "enable"),
        (config.grant_role_path, "grant-role"),
        (config.revoke_role_path, "revoke-role"),
        (config.grant_permission_path, "grant-permission"),
        (config.revoke_permission_path, "revoke-permission"),
    ):
        router.add_api_route(
            path,
            _mutation_endpoint(templates, config, hooks, kind),
            methods=["POST"],
        )


def _mutation_endpoint(
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    kind: str,
) -> Callable[[Request], MaybeAwaitable[Response]]:
    async def mutation(request: Request) -> Response:
        auth = await admin_user(request, config, hooks)
        if isinstance(auth, Denied):
            return auth.response
        if kind in {"disable", "enable"}:
            form = await read_mutation_form(request)
        else:
            form = await read_grant_form(
                request,
                value_field="role_name"
                if kind in {"grant-role", "revoke-role"}
                else "permission",
            )
        if isinstance(form, FormError):
            return error_response(form.status_code, form.title, form.message)
        csrf_error = await _validate_csrf(request, config, form.csrf_token)
        if csrf_error is not None:
            return csrf_error
        if isinstance(form, MutationForm):
            changed = await resolve(
                hooks.set_user_disabled(
                    request, auth.current_user, form.user_id, kind == "disable"
                )
            )
            await resolve(
                hooks.after_user_disabled_changed(request, auth.current_user, changed)
            )
        elif kind in {"grant-role", "revoke-role"}:
            callback = hooks.grant_role if kind == "grant-role" else hooks.revoke_role
            changed = await resolve(
                callback(request, auth.current_user, form.user_id, form.value)
            )
        else:
            permission = PermissionGrantRow(
                form.value, form.value, form.scope_type, form.scope_id
            )
            callback = (
                hooks.grant_permission
                if kind == "grant-permission"
                else hooks.revoke_permission
            )
            changed = await resolve(
                callback(request, auth.current_user, form.user_id, permission)
            )
        csrf = await resolve(hooks.csrf_context(request))
        return await _row_response(
            templates,
            request,
            config=config,
            hooks=hooks,
            current_user=auth.current_user,
            row=changed,
            csrf=csrf,
        )

    return mutation


def _add_admin_invitation_routes(
    router: APIRouter,
    templates: Environment,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    async def invite(request: Request) -> Response:
        return await _named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="invite_user",
            required=("username", "email", "role"),
            redirect_url=config.users_path,
        )

    async def reissue_invitation(request: Request) -> Response:
        return await _named_action(
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
        csrf_error = await _validate_csrf(request, config, form.get("csrf"))
        if csrf_error is not None:
            return csrf_error
        changed = cast(
            "UserRow",
            await resolve(callback(request, auth.current_user, form["invitation_id"])),
        )
        csrf = await resolve(hooks.csrf_context(request))
        return await _row_response(
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


def _add_admin_deletion_routes(
    router: APIRouter,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> None:
    async def soft_delete(request: Request) -> Response:
        return await _named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="soft_delete_user",
            required=("user_id",),
            redirect_url=config.users_path,
        )

    async def hard_delete(request: Request) -> Response:
        return await _named_action(
            request,
            config=config,
            hooks=hooks,
            hook_name="hard_delete_user",
            required=("user_id", "confirmation"),
            redirect_url=config.users_path,
        )

    router.add_api_route(config.soft_delete_user_path, soft_delete, methods=["POST"])
    router.add_api_route(config.hard_delete_user_path, hard_delete, methods=["POST"])


async def _named_action(  # noqa: PLR0911
    request: Request,
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    hook_name: str,
    required: tuple[str, ...],
    redirect_url: str,
) -> Response:
    auth = (
        await current_user(request, config, hooks)
        if hook_name == "revoke_session"
        else await admin_user(request, config, hooks)
    )
    if isinstance(auth, Denied):
        return auth.response
    callback = getattr(hooks, hook_name, None)
    if not callable(callback):
        return error_response(
            501, "Action unavailable", f"Host did not provide {hook_name}."
        )
    form = await read_named_form(request, required)
    if isinstance(form, FormError):
        return error_response(form.status_code, form.title, form.message)
    csrf_error = await _validate_csrf(request, config, form.get("csrf"))
    if csrf_error is not None:
        return csrf_error
    if hook_name == "hard_delete_user" and form["confirmation"] != form["user_id"]:
        return error_response(
            400,
            "Confirmation failed",
            "Confirmation must exactly match the user id.",
        )
    callback_values = (
        (form["user_id"],)
        if hook_name == "hard_delete_user"
        else tuple(form[name] for name in required)
    )
    result = await resolve(callback(request, auth.current_user, *callback_values))
    if hook_name in {"invite_user", "reissue_invitation"}:
        activation_url = getattr(result, "activation_url", None)
        if not isinstance(activation_url, str) or not activation_url:
            return error_response(
                500, "Invitation failed", "Invitation result has no activation URL."
            )
        return RedirectResponse(
            url=f"{redirect_url}?{urlencode({'invitation_url': activation_url})}",
            status_code=303,
        )
    return RedirectResponse(url=redirect_url, status_code=303)


async def _validate_csrf(
    request: Request, config: UserManagerUiConfig, submitted_token: str | None
) -> HTMLResponse | None:
    """Validate CSRF before invoking any mutation callback."""
    protection = config.csrf_protection
    if protection is None or submitted_token is None:
        return error_response(
            403, "CSRF validation failed", "A valid CSRF token is required."
        )
    try:
        _ = await resolve(protection.validate(request, submitted_token))
    except Exception:
        return error_response(
            403, "CSRF validation failed", "A valid CSRF token is required."
        )
    return None


async def _row_response(
    templates: Environment,
    request: Request,
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
    current_user: AuthenticatedSubject,
    row: UserRow,
    csrf: CsrfContext,
) -> HTMLResponse:
    host_context = await _page_context(hooks, request)
    labels = _merge_labels(config, host_context)
    html = templates.get_template("users/_row.html").render(
        **{
            **host_context,
            "request": request,
            "config": config,
            "user": safe_row(row),
            "role_options": tuple(
                await resolve(hooks.role_options(request, current_user))
            ),
            "capability_options": tuple(
                await resolve(hooks.capability_options(request, current_user))
            ),
            "csrf": csrf,
            "csrf_inputs": _csrf_inputs(config, request, csrf),
            "labels": labels,
            "reissue_invitation_enabled": callable(
                getattr(hooks, "reissue_invitation", None)
            ),
            "revoke_invitation_enabled": callable(
                getattr(hooks, "revoke_invitation", None)
            ),
            "soft_delete_enabled": callable(getattr(hooks, "soft_delete_user", None)),
            "hard_delete_enabled": callable(getattr(hooks, "hard_delete_user", None)),
        }
    )
    return HTMLResponse(html)


def _render_panel(
    templates: Environment,
    request: Request,
    current_user: AuthenticatedSubject,
    panel: PasskeyPanel | None,
) -> str:
    """Render a named host/package template, or omit the optional panel."""
    if panel is None:
        return ""
    context: dict[str, object] = {
        "request": request,
        "current_user": current_user,
        **dict(panel.context),
    }
    return templates.get_template(panel.template_name).render(**context)
