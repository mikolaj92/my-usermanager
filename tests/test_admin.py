from __future__ import annotations

import pytest

from my_usermanager.admin import GrantAdminService, UnsafeGrantMutationError
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore, MemoryUserStore
from my_usermanager.models import Permission, Scope, User
from my_usermanager.stores import DuplicateGrantError, GrantNotFoundError, UserQuery


def _service() -> tuple[GrantAdminService, MemoryUserStore, MemoryGrantStore]:
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    return GrantAdminService(users=users, roles=roles, grants=grants), users, grants


def test_admin_service_lists_users_with_grants_and_projected_claims() -> None:
    # Given: two users with different grant state.
    service, users, _grants = _service()
    alice = users.create(User(user_id="alice", username="alice"))
    _ = users.create(User(user_id="bob", username="bob"))
    _ = service.grant_role(
        actor_id="admin",
        target_user_id="alice",
        role_name="admin",
    )

    # When: admin UI summaries are requested.
    summaries = service.list_users(limit=10, query=UserQuery())

    # Then: summaries include user profile, raw grants, and projected claims.
    alice_summary = next(summary for summary in summaries if summary.user == alice)
    assert alice_summary.user.username == "alice"
    assert len(alice_summary.grants) == 1
    assert alice_summary.projection.roles == frozenset({"admin"})
    assert alice_summary.projection.claims["is_admin"] is True


def test_admin_service_mutates_roles_permissions_and_scoped_permissions() -> None:
    # Given: an admin service and a target user.
    service, users, grants = _service()
    _ = users.create(User(user_id="target", username="target"))
    workflow_scope = Scope.scoped("workflow", "wf_1")

    # When: role, permission, and scoped permission grants are mutated.
    role_result = service.grant_role(
        actor_id="admin",
        target_user_id="target",
        role_name="member",
    )
    permission_result = service.grant_permission(
        actor_id="admin",
        target_user_id="target",
        permission=Permission("reports.read"),
    )
    scoped_result = service.grant_permission(
        actor_id="admin",
        target_user_id="target",
        permission=Permission("workflows.run"),
        scope=workflow_scope,
    )
    revoked_role = service.revoke_role(
        actor_id="admin",
        target_user_id="target",
        role_name="member",
    )
    revoked_permission = service.revoke_permission(
        actor_id="admin",
        target_user_id="target",
        permission=Permission("reports.read"),
    )
    revoked_scoped = service.revoke_permission(
        actor_id="admin",
        target_user_id="target",
        permission=Permission("workflows.run"),
        scope=workflow_scope,
    )

    # Then: results are explicit and stores end clean.
    assert role_result.action == "grant_role"
    assert permission_result.action == "grant_permission"
    assert scoped_result.grant.scope == workflow_scope
    assert revoked_role.action == "revoke_role"
    assert revoked_permission.action == "revoke_permission"
    assert revoked_scoped.grant.scope == workflow_scope
    assert grants.list_grants_for_user("target") == ()


def test_admin_service_preserves_duplicate_and_missing_grant_errors() -> None:
    # Given: an existing role grant.
    service, users, _grants = _service()
    _ = users.create(User(user_id="target", username="target"))
    _ = service.grant_role(
        actor_id="admin",
        target_user_id="target",
        role_name="member",
    )

    # When / Then: duplicate and missing operations keep store-level errors.
    with pytest.raises(DuplicateGrantError):
        _ = service.grant_role(
            actor_id="admin",
            target_user_id="target",
            role_name="member",
        )
    with pytest.raises(GrantNotFoundError):
        _ = service.revoke_permission(
            actor_id="admin",
            target_user_id="target",
            permission=Permission("reports.read"),
        )


def test_admin_service_rejects_self_demoting_last_admin_grant() -> None:
    # Given: an admin whose only global admin grant is the built-in admin role.
    service, users, _grants = _service()
    _ = users.create(User(user_id="admin", username="admin"))
    _ = service.grant_role(
        actor_id="admin",
        target_user_id="admin",
        role_name="admin",
    )

    # When / Then: revoking that own grant is rejected before mutation.
    with pytest.raises(UnsafeGrantMutationError, match="own last admin grant"):
        _ = service.revoke_role(
            actor_id="admin",
            target_user_id="admin",
            role_name="admin",
        )
    assert (
        service.summary_for_user(
            User(user_id="admin", username="admin")
        ).projection.claims["is_admin"]
        is True
    )


def test_admin_service_rejects_removing_last_active_admin_from_another_user() -> None:
    # Given: one active admin and another actor attempting the removal.
    service, users, _grants = _service()
    _ = users.create(User(user_id="admin", username="admin"))
    _ = users.create(User(user_id="operator", username="operator"))
    _ = service.grant_permission(
        actor_id="operator",
        target_user_id="admin",
        permission=Permission("admin.access"),
    )

    # When / Then: removing the final active admin grant is rejected.
    with pytest.raises(UnsafeGrantMutationError, match="last active admin"):
        _ = service.revoke_permission(
            actor_id="operator",
            target_user_id="admin",
            permission=Permission("admin.access"),
        )


def test_admin_service_allows_admin_revoke_when_target_keeps_admin_access() -> None:
    # Given: an admin with two independent global admin grants.
    service, users, _grants = _service()
    _ = users.create(User(user_id="admin", username="admin"))
    _ = service.grant_role(
        actor_id="admin",
        target_user_id="admin",
        role_name="admin",
    )
    _ = service.grant_permission(
        actor_id="admin",
        target_user_id="admin",
        permission=Permission("admin.access"),
    )

    # When: one admin grant is removed.
    result = service.revoke_role(
        actor_id="admin",
        target_user_id="admin",
        role_name="admin",
    )

    # Then: the operation succeeds because admin.access remains.
    assert result.action == "revoke_role"
    assert (
        service.summary_for_user(
            User(user_id="admin", username="admin")
        ).projection.claims["is_admin"]
        is True
    )
