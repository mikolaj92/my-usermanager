from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from ny_usermanager.memory import (
    MemoryAuditStore,
    MemoryGrantStore,
    MemoryRoleStore,
    MemoryUserStore,
)
from ny_usermanager.models import AuditEvent, Permission, Scope, User
from ny_usermanager.permissions import ADMIN_ROLE_NAME
from ny_usermanager.stores import (
    AuditFilters,
    DuplicateUserError,
    GrantNotFoundError,
    UserQuery,
)


def test_memory_stores_are_independent_process_local_instances() -> None:
    # Given: two independently constructed memory user stores.
    first = MemoryUserStore()
    second = MemoryUserStore()
    user = User(user_id="user_123")

    # When: one process-local instance is mutated.
    _ = first.create(user)

    # Then: the other instance has no shared state.
    assert first.get("user_123") == user
    assert second.get("user_123") is None


def test_memory_stores_cover_dev_data_surface_without_optional_imports() -> None:
    # Given: fresh memory stores for users, roles, grants, and audit.
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    audit = MemoryAuditStore()
    user = User(user_id="user_123", display_name="Alice")
    event = AuditEvent(
        event_id="evt_123",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="admin_123",
        action="user.created",
        target_type="user",
        target_id="user_123",
        scope=Scope.global_(),
        result="success",
    )

    # When: each store is exercised through its sync data surface.
    created = users.create(user)
    listed_users = users.list(limit=10, offset=0, query=UserQuery(text="ali"))
    admin_role = roles.get(ADMIN_ROLE_NAME)
    role_grant = grants.add_role_grant("user_123", ADMIN_ROLE_NAME, Scope.global_())
    permission_grant = grants.add_permission_grant(
        "user_123",
        Permission("reports.read"),
        Scope.scoped("tenant", "tenant_123"),
    )
    _ = audit.append(event)
    listed_events = audit.list(
        limit=10,
        offset=0,
        filters=AuditFilters(actor_id="admin_123"),
    )

    # Then: memory stores behave without loading optional frameworks.
    assert created == user
    assert listed_users == (user,)
    assert admin_role is not None
    assert role_grant.role_name == "admin"
    assert permission_grant.permission == Permission("reports.read")
    assert grants.list_grants_for_user("user_123") == (role_grant, permission_grant)
    assert listed_events == (event,)
    assert "fastapi" not in sys.modules
    assert "pydantic" not in sys.modules


def test_memory_stores_use_deterministic_error_contracts() -> None:
    # Given: memory user and grant stores with one existing record.
    users = MemoryUserStore()
    grants = MemoryGrantStore()
    user = User(user_id="user_123")
    _ = users.create(user)

    # When / Then: duplicate and missing operations expose typed outcomes.
    with pytest.raises(DuplicateUserError, match="user_123"):
        _ = users.create(user)
    with pytest.raises(GrantNotFoundError, match="admin"):
        _ = grants.remove_role_grant("missing", "admin", Scope.global_())
