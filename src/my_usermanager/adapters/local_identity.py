"""One FastAPI installer for local passkey login and packaged user management.

Hosts that want later issuer swap still log in through ``/oidc/login``. This
plugin is the in-process passkey + UM default: one SQLite file, signed session,
CSRF, chrome, ``/login``, ``/account``, and ``/admin/users``.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from app_factory.adapters import (
    PasskeyBinding,
    UserManagerBinding,
    install_identity_adapters,
)
from app_factory.csrf import SessionCsrfProtection
from app_factory.platform import PlatformConfig, PlatformUser
from fastapi import FastAPI, Request, Response
from my_auth import (  # pyright: ignore[reportMissingTypeStubs]
    PasskeyConfig,
    PasskeyService,
    PasskeyUser,
    SQLiteChallengeStore,
)
from my_auth.fastapi import (  # pyright: ignore[reportMissingTypeStubs]
    PasskeyCookies,
    PasskeyRouteHooks,
)
from my_auth.passkeys import (  # pyright: ignore[reportMissingTypeStubs]
    SQLiteCredentialStore,
)
from starlette.middleware.sessions import SessionMiddleware

from my_usermanager.adapters.fastapi import (
    clear_current_user,
    current_user,
    write_current_user,
)
from my_usermanager.adapters.fastapi_htmx import StandardUserManagerUiHooks
from my_usermanager.adapters.my_auth import MY_AUTH_PROVIDER
from my_usermanager.adapters.my_auth_fastapi import (
    PasskeyUserProfile,
    build_after_login_identity_linker,
    build_after_register_identity_linker,
    build_complete_registration,
    build_login_session_principal_writer,
    user_to_session_principal,
)
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.manager import UserManager
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.subjects import AuthenticatedSubject

if TYPE_CHECKING:
    from pathlib import Path

    from my_usermanager.adapters.sqlite import SQLiteUserStore
    from my_usermanager.sessions import SessionPrincipal

__all__: Final[tuple[str, ...]] = (
    "LocalIdentity",
    "install_local_identity",
)

_ADMIN_REQUIRED: Final = "admin required"
_SESSION_KEY_REQUIRED: Final = "session_secret is required"
_MISSING_PASSKEY_USER: Final = "verified registration is missing a passkey user"


@dataclass(frozen=True, slots=True)
class LocalIdentity:
    """Installed local identity stack returned to the host."""

    manager: UserManager
    database: SQLiteAuthDatabase
    passkey: PasskeyService


@dataclass(frozen=True, slots=True)
class _Stack:
    auth_db: SQLiteAuthDatabase
    manager: UserManager
    users: SQLiteUserStore
    credentials: SQLiteCredentialStore
    service: PasskeyService


def install_local_identity(  # noqa: PLR0913
    app: FastAPI,
    *,
    database: str | Path | sqlite3.Connection,
    rp_id: str,
    origin: str,
    session_secret: str,
    app_name: str = "App",
) -> LocalIdentity:
    """Mount passkey login, account, and admin UI on one FastAPI app.

    Requires ``my-usermanager[fastapi-htmx,myauth]`` and ``my-auth[fastapi-htmx]``.
    Product routes should keep using ``current_user`` / ``SessionPrincipal``.
    """
    existing = getattr(app.state, "local_identity", None)
    if isinstance(existing, LocalIdentity):
        return existing
    if not session_secret:
        raise ValueError(_SESSION_KEY_REQUIRED)

    stack = _open_stack(database, rp_id=rp_id, origin=origin, app_name=app_name)
    csrf = SessionCsrfProtection()
    _ = install_identity_adapters(
        app,
        environments=(),
        config=PlatformConfig(
            app_name=app_name,
            enable_account=True,
            enable_credentials=True,
            enable_admin_users=True,
        ),
        passkey=PasskeyBinding(
            service=stack.service,
            hooks=_passkey_hooks(stack),
            cookies=PasskeyCookies(secure=urlsplit(origin).scheme == "https"),
            csrf_token=csrf.token,
            login_success_url="/",
            register_success_url="/account",
        ),
        usermanager=UserManagerBinding(
            hooks=_um_hooks(stack.manager),
            csrf_protection=csrf,
        ),
        current_user=_platform_user,
    )
    # Last-added middleware runs first: session must wrap platform context.
    app.add_middleware(SessionMiddleware, secret_key=session_secret)
    installed = LocalIdentity(
        manager=stack.manager,
        database=stack.auth_db,
        passkey=stack.service,
    )
    app.state.local_identity = installed
    return installed


def _open_stack(
    database: str | Path | sqlite3.Connection,
    *,
    rp_id: str,
    origin: str,
    app_name: str,
) -> _Stack:
    conn = _sqlite_connection(database)
    auth_db = SQLiteAuthDatabase(conn)
    auth_db.initialize()
    stores = auth_db.stores()
    credentials = SQLiteCredentialStore(conn)
    return _Stack(
        auth_db=auth_db,
        manager=UserManager(
            users=stores.users,
            roles=stores.roles,
            grants=stores.grants,
        ),
        users=stores.users,
        credentials=credentials,
        service=PasskeyService(
            config=PasskeyConfig(rp_id=rp_id, rp_name=app_name, origin=origin),
            challenges=SQLiteChallengeStore(conn),
            credentials=credentials,
        ),
    )


def _sqlite_connection(
    database: str | Path | sqlite3.Connection,
) -> sqlite3.Connection:
    if isinstance(database, sqlite3.Connection):
        return database
    conn = sqlite3.connect(database, timeout=30, check_same_thread=False)
    _ = conn.execute("PRAGMA busy_timeout=30000")
    _ = conn.execute("PRAGMA foreign_keys=ON")
    _ = conn.execute("PRAGMA journal_mode=WAL")
    _ = conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _passkey_hooks(stack: _Stack) -> PasskeyRouteHooks:
    users = stack.users
    return PasskeyRouteHooks(
        get_session_user=lambda request: _session_passkey_user(stack, request),
        prepare_registration=_prepare_registration,
        complete_registration=build_complete_registration(
            lambda request, result: _complete_registration(stack, request, result)
        ),
        get_auth_user=lambda user_id: _get_auth_user(stack, user_id),
        login=build_login_session_principal_writer(
            users,
            _write_principal,
            lambda user: _principal_for(stack, user),
        ),
        logout=_logout,
        render_login=_unused_render,
        render_register=_unused_render,
        after_register=build_after_register_identity_linker(users),
        after_login=build_after_login_identity_linker(users),
    )


def _prepare_registration(_request: Request, username: str) -> PasskeyUser:
    profile = PasskeyUserProfile(
        user_id=username,
        user_handle=secrets.token_bytes(32),
        name=username,
        display_name=username,
    )
    return PasskeyUser(
        profile.user_id,
        profile.user_handle,
        profile.name,
        profile.display_name,
    )


def _get_auth_user(stack: _Stack, user_id: str) -> PasskeyUser | None:
    identity = ExternalIdentity(provider=MY_AUTH_PROVIDER, subject=user_id)
    user = stack.users.resolve_external_identity(identity)
    if user is None or not user.is_active:
        return None
    return stack.credentials.get_user(user_id)


def _complete_registration(
    stack: _Stack,
    request: Request,
    result: object,
) -> PasskeyUser:
    passkey_user = getattr(result, "user", None)
    if not isinstance(passkey_user, PasskeyUser):
        raise TypeError(_MISSING_PASSKEY_USER)
    _ = stack.auth_db.complete_registration(
        request,
        result,
        user=User(
            user_id=passkey_user.user_id,
            username=passkey_user.name,
            display_name=passkey_user.display_name,
        ),
        identity=ExternalIdentity(
            provider=MY_AUTH_PROVIDER,
            subject=passkey_user.user_id,
        ),
    )
    return passkey_user


def _principal_for(stack: _Stack, user: User) -> SessionPrincipal:
    grants = stack.manager.grants.list_grants_for_user(user.user_id)
    roles = frozenset(
        grant.role_name for grant in grants if grant.role_name is not None
    )
    permissions = frozenset(
        grant.permission for grant in grants if grant.permission is not None
    )
    return user_to_session_principal(
        user,
        roles=roles,
        permissions=permissions,
        claims={"is_admin": ADMIN_ROLE_NAME in roles},
    )


def _write_principal(
    _response: Response,
    request: Request,
    principal: SessionPrincipal,
) -> None:
    _ = write_current_user(request, principal)


def _session_passkey_user(stack: _Stack, request: Request) -> PasskeyUser | None:
    principal = current_user(request)
    if principal is None:
        return None
    for identity in principal.external_identities:
        if identity.provider == MY_AUTH_PROVIDER:
            return stack.credentials.get_user(identity.subject)
    return stack.credentials.get_user(principal.user_id)


def _logout(_response: Response, request: Request) -> None:
    clear_current_user(request)


def _um_hooks(manager: UserManager) -> StandardUserManagerUiHooks:
    def subject_from_request(request: Request) -> AuthenticatedSubject | None:
        principal = current_user(request)
        if principal is None:
            return None
        identity = next(iter(principal.external_identities), None)
        return AuthenticatedSubject(
            provider=identity.provider if identity is not None else MY_AUTH_PROVIDER,
            subject=identity.subject if identity is not None else principal.user_id,
            user_id=principal.user_id,
            username=principal.username,
            display_name=principal.display_name,
        )

    def require_admin(_request: Request, subject: AuthenticatedSubject) -> None:
        grants = manager.grants.list_grants_for_user(subject.user_id)
        if not any(grant.role_name == ADMIN_ROLE_NAME for grant in grants):
            raise PermissionError(_ADMIN_REQUIRED)

    return StandardUserManagerUiHooks(
        manager=manager,
        current_user=subject_from_request,
        require_admin=require_admin,
        role_names=(ADMIN_ROLE_NAME,),
    )


def _platform_user(request: Request) -> PlatformUser | None:
    principal = current_user(request)
    if principal is None:
        return None
    return PlatformUser(
        display_name=principal.display_name or principal.username or principal.user_id,
        is_admin=principal.has_role(ADMIN_ROLE_NAME)
        or principal.claims.get("is_admin") is True,
        user_id=principal.user_id,
    )


def _unused_render(request: Request) -> Response:
    del request
    return Response("replaced by my-auth.fastapi_htmx", media_type="text/plain")
