"""Standard store-backed hooks remove mechanical host UI adapters."""

from __future__ import annotations

from fastapi import Request

from my_usermanager.adapters.fastapi_htmx import (
    PermissionGrantRow,
    StandardUserManagerUiHooks,
)
from my_usermanager.manager import UserManager, UserProfileUpdate
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore, MemoryUserStore
from my_usermanager.models import Permission, Scope, User
from my_usermanager.subjects import AuthenticatedSubject


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _subject(user_id: str) -> AuthenticatedSubject:
    return AuthenticatedSubject(provider="test", subject=user_id, user_id=user_id)


def _hooks() -> tuple[StandardUserManagerUiHooks, UserManager]:
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    _ = users.create(User("admin", "admin", display_name="Ada"))
    _ = users.create(User("member", "member", email="member@example.test"))
    _ = grants.add_role_grant("admin", "admin", Scope.global_())
    manager = UserManager(users=users, roles=roles, grants=grants)
    hooks = StandardUserManagerUiHooks(
        manager=manager,
        current_user=lambda _request: _subject("admin"),
        require_admin=lambda _request, _subject: None,
        role_names=("admin", "member"),
    )
    return hooks, manager


def test_standard_hooks_project_manager_users_and_grants_to_ui_rows() -> None:
    hooks, manager = _hooks()
    _ = manager.grants.add_permission_grant(
        "member", Permission("reports.read"), Scope.global_()
    )

    rows = hooks.list_users(_request(), _subject("admin"))

    member = next(row for row in rows if row.user_id == "member")
    assert member.display_name is None
    assert member.email == "member@example.test"
    assert member.account_status == "active"
    assert member.permissions == (
        PermissionGrantRow(permission="reports.read", label="reports.read"),
    )
    assert hooks.role_options(_request(), _subject("admin")) == ("admin", "member")
    assert hooks.capability_options(_request(), _subject("admin")) == ()


def test_standard_hooks_apply_account_role_permission_and_profile_mutations() -> None:
    hooks, _manager = _hooks()
    request = _request()
    admin = _subject("admin")

    disabled_row = hooks.set_user_disabled(request, admin, "member", disabled=True)
    assert disabled_row.disabled is True
    assert (
        hooks.set_user_disabled(request, admin, "member", disabled=False).disabled
        is False
    )
    assert "member" in hooks.grant_role(request, admin, "member", "member").roles
    assert "member" not in hooks.revoke_role(request, admin, "member", "member").roles
    permission = PermissionGrantRow("reports.read", "Reports")
    assert hooks.grant_permission(request, admin, "member", permission).permissions == (
        PermissionGrantRow("reports.read", "reports.read"),
    )
    assert (
        hooks.revoke_permission(request, admin, "member", permission).permissions == ()
    )
    updated = hooks.update_own_profile(
        request,
        admin,
        UserProfileUpdate(username="admin", display_name="Ada Updated"),
    )
    assert updated.display_name == "Ada Updated"


def test_standard_hooks_keep_policy_and_optional_surfaces_host_owned() -> None:
    calls: list[str] = []
    hooks, _manager = _hooks()
    hooks = StandardUserManagerUiHooks(
        manager=hooks.manager,
        current_user=lambda _request: None,
        require_admin=lambda _request, subject: calls.append(subject.user_id),
        role_names=("member",),
    )

    assert hooks.get_current_user(_request()) is None
    hooks.require_admin(_request(), _subject("admin"))
    assert calls == ["admin"]
    assert hooks.csrf_context(_request()).hidden_inputs == ()
    assert hooks.render_passkey_panel(_request(), _subject("admin")) is None
    hooks.after_user_disabled_changed(
        _request(),
        _subject("admin"),
        hooks.list_users(_request(), _subject("admin"))[0],
    )
