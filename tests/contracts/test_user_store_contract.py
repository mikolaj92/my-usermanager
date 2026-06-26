from __future__ import annotations

import pytest

from my_usermanager.memory import MemoryUserStore
from my_usermanager.models import User
from my_usermanager.stores import (
    DuplicateUserError,
    InvalidPageError,
    UserNotFoundError,
    UserQuery,
    UserStore,
)


def test_user_store_protocol_is_satisfied_by_memory_store() -> None:
    # Given: the Wave 2 in-memory user store.
    stores: list[object] = []
    stores.append(MemoryUserStore())

    # When: the store is checked against the sync protocol.
    conforms = isinstance(stores[0], UserStore)

    # Then: callers can depend on the protocol surface only.
    assert conforms is True


def test_user_store_persists_updates_and_lists_deterministically() -> None:
    # Given: a memory store with out-of-order users.
    store = MemoryUserStore()
    alice = User(user_id="user_b", display_name="Alice Example", email="a@example.com")
    bob = User(user_id="user_a", display_name="Bob Example", disabled=True)
    _ = store.create(alice)
    _ = store.create(bob)

    # When: one user is updated and the collection is queried.
    updated = User(
        user_id="user_b", display_name="Alice Renamed", email="a@example.com"
    )
    stored_update = store.update(updated)
    page = store.list(limit=1, offset=0, query=UserQuery())
    search_results = store.list(limit=10, offset=0, query=UserQuery(text="renamed"))
    active_count = store.count_active()

    # Then: retrieval, pagination, query, and active counts are deterministic.
    assert stored_update == updated
    assert store.get("user_b") == updated
    assert page == (bob,)
    assert search_results == (updated,)
    assert active_count == 1


def test_user_store_reports_duplicate_missing_and_invalid_pages() -> None:
    # Given: a store containing one user.
    store = MemoryUserStore()
    user = User(user_id="user_123")
    _ = store.create(user)

    # When / Then: create, update, get, and pagination failures are stable.
    with pytest.raises(DuplicateUserError, match="user_123"):
        _ = store.create(user)
    with pytest.raises(UserNotFoundError, match="missing"):
        _ = store.update(User(user_id="missing"))
    with pytest.raises(InvalidPageError, match="offset"):
        _ = store.list(limit=10, offset=-1, query=UserQuery())
    assert store.get("missing") is None


def test_user_store_filters_disabled_users_when_requested() -> None:
    # Given: active and disabled users.
    store = MemoryUserStore()
    active = User(user_id="active", display_name="Same Name")
    disabled = User(user_id="disabled", display_name="Same Name", disabled=True)
    _ = store.create(disabled)
    _ = store.create(active)

    # When: list queries request each disabled state.
    active_users = store.list(limit=10, offset=0, query=UserQuery(disabled=False))
    disabled_users = store.list(limit=10, offset=0, query=UserQuery(disabled=True))

    # Then: state filters do not depend on insertion order.
    assert active_users == (active,)
    assert disabled_users == (disabled,)
