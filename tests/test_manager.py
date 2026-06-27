from __future__ import annotations

import pytest

from my_usermanager import ADMIN_ROLE_NAME
from my_usermanager.manager import (
    AuthorizationError,
    PermissionGrantRequest,
    RoleGrantRequest,
    UserManager,
    UserProfileUpdate,
)
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore, MemoryUserStore
from my_usermanager.models import Permission, Scope, User


def test_admin_can_grant_and_revoke_user_access() -> None:
    # Given: an admin actor and a target user in memory-backed stores.
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    manager = UserManager(users=users, roles=roles, grants=grants)
    _ = users.create(User(user_id="admin_123"))
    _ = users.create(User(user_id="user_123"))
    _ = grants.add_role_grant(
        user_id="admin_123",
        role_name=ADMIN_ROLE_NAME,
        scope=Scope.global_(),
    )

    # When: the admin grants and then revokes access for the target user.
    request = RoleGrantRequest(
        target_user_id="user_123",
        role_name=ADMIN_ROLE_NAME,
        scope=Scope.global_(),
    )
    added = manager.grant_role(actor_id="admin_123", request=request)
    removed = manager.revoke_role(actor_id="admin_123", request=request)

    # Then: access management succeeds and leaves no target grants behind.
    assert added.role_name == ADMIN_ROLE_NAME
    assert removed.role_name == ADMIN_ROLE_NAME
    assert grants.list_grants_for_user("user_123") == ()


def test_regular_user_cannot_manage_access() -> None:
    # Given: a non-admin actor and a target user.
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    manager = UserManager(users=users, roles=roles, grants=grants)
    _ = users.create(User(user_id="actor_123"))
    _ = users.create(User(user_id="user_123"))

    # When / Then: attempting to grant access is rejected and mutates nothing.
    with pytest.raises(AuthorizationError, match=r"permissions\.grant"):
        _ = manager.grant_permission(
            actor_id="actor_123",
            request=PermissionGrantRequest(
                target_user_id="user_123",
                permission=Permission("reports.read"),
                scope=Scope.global_(),
            ),
        )

    assert grants.list_grants_for_user("user_123") == ()


def test_user_can_update_own_basic_profile_only() -> None:
    # Given: a user with protected administrative fields already set.
    users = MemoryUserStore()
    manager = UserManager(
        users=users,
        roles=MemoryRoleStore(),
        grants=MemoryGrantStore(),
    )
    _ = users.create(
        User(
            user_id="user_123",
            username="old_username",
            first_name="Old",
            last_name="Name",
            disabled=True,
            system=True,
            scope=Scope.scoped("tenant", "tenant_123"),
        )
    )

    # When: the same user updates only their profile value object.
    updated = manager.update_own_profile(
        actor_id="user_123",
        update=UserProfileUpdate(
            username="new_username",
            first_name="Alice",
            last_name="Example",
            display_name="Alice Example",
            email="alice@example.com",
        ),
    )

    # Then: profile fields changed but access/protection fields did not.
    assert updated.username == "new_username"
    assert updated.first_name == "Alice"
    assert updated.last_name == "Example"
    assert updated.display_name == "Alice Example"
    assert updated.email == "alice@example.com"
    assert updated.disabled is True
    assert updated.system is True
    assert updated.scope == Scope.scoped("tenant", "tenant_123")


def test_user_cannot_update_another_users_profile() -> None:
    # Given: two ordinary users.
    users = MemoryUserStore()
    manager = UserManager(
        users=users,
        roles=MemoryRoleStore(),
        grants=MemoryGrantStore(),
    )
    _ = users.create(User(user_id="actor_123"))
    _ = users.create(User(user_id="user_123", username="old_username"))

    # When / Then: cross-user profile updates are rejected and leave data intact.
    with pytest.raises(AuthorizationError, match=r"profile\.update"):
        _ = manager.update_profile(
            actor_id="actor_123",
            target_user_id="user_123",
            update=UserProfileUpdate(
                username="new_username",
                first_name="Alice",
                last_name="Example",
            ),
        )

    assert users.get("user_123") == User(
        user_id="user_123",
        username="old_username",
    )
