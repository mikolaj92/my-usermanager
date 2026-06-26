from __future__ import annotations

import pytest

from ny_usermanager.memory import MemoryGrantStore
from ny_usermanager.models import Grant, Permission, Scope
from ny_usermanager.stores import (
    DuplicateGrantError,
    GrantNotFoundError,
    GrantStore,
)


def test_grant_store_protocol_is_satisfied_by_memory_store() -> None:
    # Given: the Wave 2 in-memory grant store.
    stores: list[object] = []
    stores.append(MemoryGrantStore())

    # When: the store is checked against the sync protocol.
    conforms = isinstance(stores[0], GrantStore)

    # Then: callers can depend on the protocol surface only.
    assert conforms is True


def test_grant_store_adds_lists_and_removes_grants_deterministically() -> None:
    # Given: role and permission grants for the same user.
    store = MemoryGrantStore()
    tenant_scope = Scope.scoped("tenant", "tenant_123")
    role_grant = Grant.for_role("user_123", "admin", tenant_scope)
    permission_grant = Grant.for_permission(
        "user_123",
        Permission("reports.read"),
        Scope.global_(),
    )

    # When: grants are added out of sorted order and then one is removed.
    stored_permission = store.add_permission_grant(
        "user_123",
        Permission("reports.read"),
        Scope.global_(),
    )
    stored_role = store.add_role_grant("user_123", "admin", tenant_scope)
    listed_before_remove = store.list_grants_for_user("user_123")
    removed = store.remove_permission_grant(
        "user_123",
        Permission("reports.read"),
        Scope.global_(),
    )
    listed_after_remove = store.list_grants_for_user("user_123")

    # Then: grant values and ordering are deterministic.
    assert stored_permission == permission_grant
    assert stored_role == role_grant
    assert listed_before_remove == (permission_grant, role_grant)
    assert removed == permission_grant
    assert listed_after_remove == (role_grant,)


def test_grant_store_reports_duplicate_and_missing_grants() -> None:
    # Given: a store containing one role grant.
    store = MemoryGrantStore()
    scope = Scope.global_()
    _ = store.add_role_grant("user_123", "admin", scope)

    # When / Then: duplicate add and missing remove outcomes are stable.
    with pytest.raises(DuplicateGrantError, match="user_123"):
        _ = store.add_role_grant("user_123", "admin", scope)
    with pytest.raises(GrantNotFoundError, match=r"reports\.read"):
        _ = store.remove_permission_grant("user_123", Permission("reports.read"), scope)
    assert store.list_grants_for_user("missing") == ()
