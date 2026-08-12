"""In-memory demo users for the FastAPI HTMX composition example."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from fastapi import HTTPException, status
from my_auth import PasskeyUser

from my_usermanager.adapters.fastapi_htmx import (
    CapabilityOption,
    ExternalIdentityRow,
    PermissionGrantRow,
    UserRow,
)
from my_usermanager.subjects import AuthenticatedSubject

DEMO_ADMIN_ID: Final = "demo-user"
DEMO_UNSAFE_USER_ID: Final = "unsafe/id space\"quote'<tag>&tail"
DEMO_CSRF_HEADER: Final = "X-Demo-CSRF"
DEMO_CSRF_MARKER: Final = "demo-noop-csrf"
_DEMO_CAPABILITY: Final = CapabilityOption(
    permission="workflow.run",
    label="Run demo workflow",
    description="Demo-only scoped capability.",
    scope_type="workflow",
    scope_id="demo-workflow",
)


@dataclass(frozen=True, slots=True)
class _DemoUser:
    user_id: str
    username: str
    display_name: str
    email: str
    admin: bool = False
    disabled: bool = False
    roles: tuple[str, ...] = ()
    permissions: tuple[PermissionGrantRow, ...] = ()


def _all_demo_users() -> tuple[_DemoUser, ...]:
    return tuple(_DEMO_USERS.values())


def _current_demo_subject(user_id: str) -> AuthenticatedSubject | None:
    user = _DEMO_USERS.get(user_id)
    if user is None:
        return None
    return _authenticated_subject(user)


def _require_demo_admin(user_id: str) -> None:
    user = _DEMO_USERS.get(user_id)
    if user is not None and user.admin:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access is required for this demo action.",
    )


def _demo_user_rows() -> tuple[UserRow, ...]:
    return tuple(_user_row(user) for user in _DEMO_USERS.values())


def _demo_role_options() -> tuple[str, ...]:
    return ("member", "admin")


def _demo_capability_options() -> tuple[CapabilityOption, ...]:
    return (_DEMO_CAPABILITY,)


def _set_demo_user_disabled(user_id: str, *, disabled: bool) -> UserRow:
    user = _require_demo_user(user_id)
    if disabled:
        _reject_last_demo_admin_mutation(user, action="disable")
    updated = replace(user, disabled=disabled)
    _DEMO_USERS[user.user_id] = updated
    return _user_row(updated)


def _grant_demo_role(user_id: str, role_name: str) -> UserRow:
    user = _require_demo_user(user_id)
    roles = tuple(sorted({*user.roles, role_name}))
    updated = replace(user, roles=roles, admin=user.admin or role_name == "admin")
    _DEMO_USERS[user.user_id] = updated
    return _user_row(updated)


def _revoke_demo_role(user_id: str, role_name: str) -> UserRow:
    user = _require_demo_user(user_id)
    if role_name == "admin":
        _reject_last_demo_admin_mutation(user, action="revoke")
    roles = tuple(role for role in user.roles if role != role_name)
    updated = replace(user, roles=roles, admin=user.admin and role_name != "admin")
    _DEMO_USERS[user.user_id] = updated
    return _user_row(updated)


def _reject_last_demo_admin_mutation(user: _DemoUser, *, action: str) -> None:
    if not user.admin or user.disabled:
        return
    active_admins = sum(
        1
        for candidate in _DEMO_USERS.values()
        if candidate.admin and not candidate.disabled
    )
    if active_admins > 1:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"cannot {action} the last active admin",
    )


def _grant_demo_permission(
    user_id: str,
    permission: PermissionGrantRow,
) -> UserRow:
    user = _require_demo_user(user_id)
    permissions = tuple(sorted({*user.permissions, permission}, key=_permission_key))
    updated = replace(user, permissions=permissions)
    _DEMO_USERS[user.user_id] = updated
    return _user_row(updated)


def _revoke_demo_permission(
    user_id: str,
    permission: PermissionGrantRow,
) -> UserRow:
    user = _require_demo_user(user_id)
    permissions = tuple(
        grant
        for grant in user.permissions
        if _permission_key(grant) != _permission_key(permission)
    )
    updated = replace(user, permissions=permissions)
    _DEMO_USERS[user.user_id] = updated
    return _user_row(updated)


def _passkey_user_from_demo(user: _DemoUser) -> PasskeyUser:
    return PasskeyUser(
        user_id=user.user_id,
        user_handle=f"demo-handle:{user.user_id}".encode(),
        name=user.username,
        display_name=user.display_name,
    )


def _ensure_demo_user(display_name: str) -> PasskeyUser:
    user_id = _user_id_from_display_name(display_name)
    user = _DEMO_USERS.get(user_id)
    if user is None:
        user = _DemoUser(
            user_id=user_id,
            username=user_id,
            display_name=display_name,
            email=f"{user_id}@example.invalid",
        )
        _DEMO_USERS[user_id] = user
    return _passkey_user_from_demo(user)


def _initial_users() -> dict[str, _DemoUser]:
    return {
        DEMO_ADMIN_ID: _DemoUser(
            user_id=DEMO_ADMIN_ID,
            username="admin",
            display_name="Demo Administrator",
            email="admin@example.invalid",
            admin=True,
            roles=("admin",),
        ),
        "auditor-user": _DemoUser(
            user_id="auditor-user",
            username="auditor",
            display_name="Audit Reviewer",
            email="auditor@example.invalid",
            roles=("member",),
            permissions=(
                PermissionGrantRow(
                    permission=_DEMO_CAPABILITY.permission,
                    label=_DEMO_CAPABILITY.label,
                    scope_type=_DEMO_CAPABILITY.scope_type,
                    scope_id=_DEMO_CAPABILITY.scope_id,
                ),
            ),
        ),
        DEMO_UNSAFE_USER_ID: _DemoUser(
            user_id=DEMO_UNSAFE_USER_ID,
            username="unsafe-user",
            display_name="Unsafe User",
            email="unsafe@example.invalid",
        ),
    }


_DEMO_USERS: Final = _initial_users()


def _authenticated_subject(user: _DemoUser) -> AuthenticatedSubject:
    return AuthenticatedSubject(
        provider="demo-passkey",
        subject=f"demo:{user.user_id}",
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
    )


def _user_row(user: _DemoUser) -> UserRow:
    return UserRow(
        user_id=user.user_id,
        row_key=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        disabled=user.disabled,
        is_admin=user.admin,
        roles=user.roles,
        permissions=user.permissions,
        external_identities=(
            ExternalIdentityRow(
                provider="demo-passkey",
                subject=f"demo:{user.user_id}",
            ),
        ),
    )


def _require_demo_user(user_id: str) -> _DemoUser:
    user = _DEMO_USERS.get(user_id)
    if user is not None:
        return user
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Demo user was not found.",
    )


def _user_id_from_display_name(display_name: str) -> str:
    parts = [character if character.isalnum() else "-" for character in display_name]
    return "".join(parts).strip("-").casefold() or "registered-user"


def _permission_key(permission: PermissionGrantRow) -> tuple[str, str, str]:
    return (
        permission.permission,
        permission.scope_type or "",
        permission.scope_id or "",
    )
