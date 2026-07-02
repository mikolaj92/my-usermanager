from __future__ import annotations

import pytest

from my_usermanager import ADMIN_ROLE_NAME
from my_usermanager.admin import (
    AdminGrantService,
    GrantChange,
    SelfDemotionError,
)
from my_usermanager.manager import (
    AuthorizationError,
    PermissionGrantRequest,
    RoleGrantRequest,
    UserManager,
)
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore, MemoryUserStore
from my_usermanager.models import Permission, Scope, User, ValidationError
from my_usermanager.stores import UserNotFoundError, UserQuery

SVG_LEVELS = (
    Permission("svg.l1"),
    Permission("svg.l2"),
    Permission("svg.l3"),
)


def make_service() -> tuple[AdminGrantService, MemoryUserStore, MemoryGrantStore]:
    users = MemoryUserStore()
    grants = MemoryGrantStore()
    manager = UserManager(users=users, roles=MemoryRoleStore(), grants=grants)
    service = AdminGrantService(manager=manager)
    _ = users.create(User(user_id="admin_123"))
    _ = grants.add_role_grant(
        user_id="admin_123",
        role_name=ADMIN_ROLE_NAME,
        scope=Scope.global_(),
    )
    return service, users, grants


def test_grant_and_revoke_role_reports_changes() -> None:
    # Given: an admin actor and a plain target user.
    service, users, grants = make_service()
    _ = users.create(User(user_id="user_123"))
    request = RoleGrantRequest(
        target_user_id="user_123",
        role_name=ADMIN_ROLE_NAME,
        scope=Scope.global_(),
    )

    # When: the role is granted twice and then revoked twice.
    first_grant = service.grant_role(actor_id="admin_123", request=request)
    second_grant = service.grant_role(actor_id="admin_123", request=request)
    first_revoke = service.revoke_role(actor_id="admin_123", request=request)
    second_revoke = service.revoke_role(actor_id="admin_123", request=request)

    # Then: duplicates and missing grants are reported without raising.
    assert first_grant == GrantChange(
        action="grant",
        grant=first_grant.grant,
        changed=True,
    )
    assert second_grant.changed is False
    assert second_grant.reason == "duplicate-grant"
    assert first_revoke.changed is True
    assert second_revoke.changed is False
    assert second_revoke.reason == "grant-not-found"
    assert grants.list_grants_for_user("user_123") == ()


def test_grant_and_revoke_permission_handles_duplicates() -> None:
    # Given: an admin actor and a target user.
    service, users, grants = make_service()
    _ = users.create(User(user_id="user_123"))
    request = PermissionGrantRequest(
        target_user_id="user_123",
        permission=Permission("report.access"),
        scope=Scope.global_(),
    )

    # When: the permission is granted twice and revoked twice.
    changes = (
        service.grant_permission(actor_id="admin_123", request=request),
        service.grant_permission(actor_id="admin_123", request=request),
        service.revoke_permission(actor_id="admin_123", request=request),
        service.revoke_permission(actor_id="admin_123", request=request),
    )

    # Then: outcomes alternate changed / no-op with stable reasons.
    assert [change.changed for change in changes] == [True, False, True, False]
    assert changes[1].reason == "duplicate-grant"
    assert changes[3].reason == "grant-not-found"
    assert grants.list_grants_for_user("user_123") == ()


def test_scoped_grants_are_isolated_per_scope() -> None:
    # Given: an admin and a target user with a tenant-scoped grant.
    service, users, grants = make_service()
    _ = users.create(User(user_id="user_123"))
    tenant_scope = Scope.scoped("tenant", "tenant_1")
    scoped_request = PermissionGrantRequest(
        target_user_id="user_123",
        permission=Permission("report.access"),
        scope=tenant_scope,
    )
    _ = service.grant_permission(actor_id="admin_123", request=scoped_request)

    # When: the same permission is revoked in a different scope.
    other_scope_revoke = service.revoke_permission(
        actor_id="admin_123",
        request=PermissionGrantRequest(
            target_user_id="user_123",
            permission=Permission("report.access"),
            scope=Scope.scoped("tenant", "tenant_2"),
        ),
    )

    # Then: the tenant_1 grant is untouched and the other scope is a no-op.
    assert other_scope_revoke.changed is False
    remaining = grants.list_grants_for_user("user_123")
    assert len(remaining) == 1
    assert remaining[0].scope == tenant_scope


def test_self_demotion_of_admin_role_is_rejected() -> None:
    # Given: an admin actor holding the admin role.
    service, _, grants = make_service()
    request = RoleGrantRequest(
        target_user_id="admin_123",
        role_name=ADMIN_ROLE_NAME,
        scope=Scope.global_(),
    )

    # When / Then: revoking their own admin role fails and changes nothing.
    with pytest.raises(SelfDemotionError, match="admin_123"):
        _ = service.revoke_role(actor_id="admin_123", request=request)

    assert len(grants.list_grants_for_user("admin_123")) == 1


def test_admin_can_revoke_admin_role_from_another_admin() -> None:
    # Given: two admins.
    service, users, grants = make_service()
    _ = users.create(User(user_id="admin_456"))
    _ = grants.add_role_grant(
        user_id="admin_456",
        role_name=ADMIN_ROLE_NAME,
        scope=Scope.global_(),
    )

    # When: one admin revokes the other's admin role.
    change = service.revoke_role(
        actor_id="admin_123",
        request=RoleGrantRequest(
            target_user_id="admin_456",
            role_name=ADMIN_ROLE_NAME,
            scope=Scope.global_(),
        ),
    )

    # Then: the revoke succeeds because it is not a self-demotion.
    assert change.changed is True
    assert grants.list_grants_for_user("admin_456") == ()


def test_set_cumulative_permissions_grants_prefix_and_revokes_rest() -> None:
    # Given: a user already holding the highest level only.
    service, users, _ = make_service()
    _ = users.create(User(user_id="user_123"))
    _ = service.grant_permission(
        actor_id="admin_123",
        request=PermissionGrantRequest(
            target_user_id="user_123",
            permission=SVG_LEVELS[2],
            scope=Scope.global_(),
        ),
    )

    # When: the cumulative level is set to two.
    changes = service.set_cumulative_permissions(
        actor_id="admin_123",
        target_user_id="user_123",
        ordered_permissions=SVG_LEVELS,
        count=2,
        scope=Scope.global_(),
    )

    # Then: levels one and two are held and level three was revoked.
    assert [change.action for change in changes] == ["grant", "grant", "revoke"]
    assert [change.changed for change in changes] == [True, True, True]
    summary = service.user_access("user_123")
    assert summary.direct_permissions == (SVG_LEVELS[0], SVG_LEVELS[1])


def test_set_cumulative_permissions_rejects_out_of_range_count() -> None:
    # Given: an admin actor.
    service, users, _ = make_service()
    _ = users.create(User(user_id="user_123"))

    # When / Then: counts outside the permission list are rejected.
    with pytest.raises(ValidationError, match="count"):
        _ = service.set_cumulative_permissions(
            actor_id="admin_123",
            target_user_id="user_123",
            ordered_permissions=SVG_LEVELS,
            count=4,
            scope=Scope.global_(),
        )


def test_non_admin_actor_cannot_use_service_mutations() -> None:
    # Given: an ordinary actor without grant authority.
    service, users, grants = make_service()
    _ = users.create(User(user_id="actor_123"))
    _ = users.create(User(user_id="user_123"))

    # When / Then: mutations are rejected by the underlying manager rules.
    with pytest.raises(AuthorizationError):
        _ = service.grant_role(
            actor_id="actor_123",
            request=RoleGrantRequest(
                target_user_id="user_123",
                role_name=ADMIN_ROLE_NAME,
                scope=Scope.global_(),
            ),
        )

    assert grants.list_grants_for_user("user_123") == ()


def test_list_user_access_projects_claims() -> None:
    # Given: an admin and a user with one direct permission.
    service, users, _ = make_service()
    _ = users.create(User(user_id="user_123"))
    _ = service.grant_permission(
        actor_id="admin_123",
        request=PermissionGrantRequest(
            target_user_id="user_123",
            permission=Permission("report.access"),
            scope=Scope.global_(),
        ),
    )

    # When: the access list page is fetched.
    summaries = service.list_user_access(limit=10, offset=0, query=UserQuery())

    # Then: both users appear with roles, permissions, and projected claims.
    assert [summary.user.user_id for summary in summaries] == [
        "admin_123",
        "user_123",
    ]
    admin_summary, user_summary = summaries
    assert admin_summary.role_names == (ADMIN_ROLE_NAME,)
    assert "users.list" in admin_summary.claims
    assert user_summary.role_names == ()
    assert user_summary.direct_permissions == (Permission("report.access"),)
    assert user_summary.claims == ("report.access",)


def test_user_access_raises_for_missing_user() -> None:
    # Given: a service with no such user stored.
    service, _, _ = make_service()

    # When / Then: asking for a missing user's access summary fails.
    with pytest.raises(UserNotFoundError, match="ghost_123"):
        _ = service.user_access("ghost_123")
