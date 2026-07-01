"""Demo passkey service and hooks for the composition example."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastapi.responses import HTMLResponse
from my_auth import AuthenticationResult, PasskeyCredential, PasskeyUser
from my_auth.fastapi import PasskeyPaths, PasskeyRouteHooks

from examples.fastapi_htmx.demo_users import (
    DEMO_ADMIN_ID,
    _all_demo_users,
    _ensure_demo_user,
    _passkey_user_from_demo,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import Request, Response

PASSKEY_PATHS: Final = PasskeyPaths(
    login_page="/auth/login",
    register_page="/auth/register",
)

type _CredentialValue = str | bytes | dict[str, str] | list[str] | None


@dataclass(frozen=True, slots=True)
class _DemoPasskeyServiceConfig:
    challenge_ttl_seconds: int = 300


class _DemoPasskeyService:
    def __init__(self) -> None:
        self.config = _DemoPasskeyServiceConfig()
        self._registration_users: dict[str, PasskeyUser] = {}

    def begin_authentication(self, *, flow_id: str) -> dict[str, str | list[str]]:
        return {
            "challenge": "demo-login-challenge",
            "rpId": "example.invalid",
            "allowCredentials": [],
            "flowId": flow_id,
        }

    def finish_authentication(
        self,
        *,
        flow_id: str,
        credential: Mapping[str, _CredentialValue],
        require_user_handle: bool = False,
    ) -> AuthenticationResult:
        user = _DEMO_PASSKEY_USERS[DEMO_ADMIN_ID]
        return AuthenticationResult(
            user=user,
            credential=PasskeyCredential(
                credential_id=_credential_id(flow_id, credential, require_user_handle),
                user_id=user.user_id,
                public_key=b"demo-public-key",
            ),
        )

    def begin_registration(
        self,
        *,
        flow_id: str,
        user: PasskeyUser,
    ) -> dict[str, str | dict[str, str]]:
        self._registration_users[flow_id] = user
        return {
            "challenge": "demo-register-challenge",
            "rp": {"id": "example.invalid", "name": "my-usermanager demo"},
            "user": {"id": user.user_handle_b64url, "name": user.name},
        }

    def finish_registration(
        self,
        *,
        flow_id: str,
        credential: Mapping[str, _CredentialValue],
    ) -> PasskeyCredential:
        user = self._registration_users.get(flow_id, _DEMO_PASSKEY_USERS[DEMO_ADMIN_ID])
        passkey = PasskeyCredential(
            credential_id=_credential_id(
                flow_id,
                credential,
                user.user_id != DEMO_ADMIN_ID,
            ),
            user_id=user.user_id,
            public_key=b"demo-public-key",
        )
        _DEMO_PASSKEY_USERS[user.user_id] = user
        return passkey


def _demo_passkey_service() -> _DemoPasskeyService:
    return _DemoPasskeyService()


def _passkey_hooks() -> PasskeyRouteHooks:
    return PasskeyRouteHooks(
        get_session_user=_get_session_user,
        make_registration_user=_make_registration_user,
        get_auth_user=_get_auth_user,
        login=_login,
        logout=_logout,
        registration_allowed=_registration_allowed,
        render_login=_unused_render_login,
        render_register=_unused_render_register,
        after_register=_after_register,
        after_login=_after_login,
    )


_DEMO_PASSKEY_USERS: Final = {
    user.user_id: _passkey_user_from_demo(user) for user in _all_demo_users()
}


def _get_session_user(request: Request) -> PasskeyUser | None:
    registration_paths = {PASSKEY_PATHS.register_page, PASSKEY_PATHS.register_options}
    if request.url.path in registration_paths:
        return None
    return _DEMO_PASSKEY_USERS[DEMO_ADMIN_ID]


def _make_registration_user(_request: Request, display_name: str) -> PasskeyUser:
    user = _ensure_demo_user(display_name)
    _DEMO_PASSKEY_USERS[user.user_id] = user
    return user


def _get_auth_user(user_id: str) -> PasskeyUser | None:
    return _DEMO_PASSKEY_USERS.get(user_id)


def _login(_response: Response, _request: Request, _user: PasskeyUser) -> None:
    return None


def _logout(_response: Response, _request: Request) -> None:
    return None


def _registration_allowed(request: Request) -> bool:
    return request.query_params.get("registration") != "closed"


def _unused_render_login(_request: Request) -> HTMLResponse:
    return HTMLResponse("my-auth fastapi_htmx replaces this host placeholder")


def _unused_render_register(_request: Request, *, bootstrap: bool) -> HTMLResponse:
    body = "bootstrap" if bootstrap else "current account"
    return HTMLResponse(f"my-auth fastapi_htmx replaces this {body} placeholder")


def _after_register(
    _request: Request,
    user: PasskeyUser,
    _credential: PasskeyCredential,
) -> None:
    _DEMO_PASSKEY_USERS[user.user_id] = user


def _after_login(
    _request: Request,
    _user: PasskeyUser,
    _credential: PasskeyCredential,
) -> None:
    return None


def _credential_id(
    flow_id: str,
    credential: Mapping[str, _CredentialValue],
    require_user_handle: bool,
) -> bytes:
    raw_id = credential.get("id")
    if isinstance(raw_id, str) and raw_id != "":
        return raw_id.encode()
    return f"demo-credential:{flow_id}:{require_user_handle}".encode()
