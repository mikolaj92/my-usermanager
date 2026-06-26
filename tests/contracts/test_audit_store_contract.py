from __future__ import annotations

from datetime import UTC, datetime

import pytest

from my_usermanager.memory import MemoryAuditStore
from my_usermanager.models import AuditEvent, Scope
from my_usermanager.stores import (
    AuditFilters,
    AuditStore,
    DuplicateAuditEventError,
    InvalidPageError,
)


def _audit_event(event_id: str, action: str, target_id: str) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="admin_123",
        action=action,
        target_type="user",
        target_id=target_id,
        scope=Scope.global_(),
        result="success",
    )


def test_audit_store_protocol_is_satisfied_by_memory_store() -> None:
    # Given: the Wave 2 in-memory audit store.
    stores: list[object] = []
    stores.append(MemoryAuditStore())

    # When: the store is checked against the sync protocol.
    conforms = isinstance(stores[0], AuditStore)

    # Then: callers can depend on the protocol surface only.
    assert conforms is True


def test_audit_store_appends_filters_and_paginates_deterministically() -> None:
    # Given: append-only audit events for different actions and targets.
    store = MemoryAuditStore()
    created = _audit_event("evt_1", "user.created", "user_1")
    updated = _audit_event("evt_2", "user.updated", "user_1")
    other = _audit_event("evt_3", "user.created", "user_2")
    _ = store.append(created)
    _ = store.append(updated)
    _ = store.append(other)

    # When: events are listed with pagination and filters.
    page = store.list(limit=2, offset=1, filters=AuditFilters())
    filtered = store.list(
        limit=10,
        offset=0,
        filters=AuditFilters(action="user.created", target_id="user_2"),
    )

    # Then: listing preserves append order and filter semantics.
    assert page == (updated, other)
    assert filtered == (other,)


def test_audit_store_reports_duplicate_events_and_invalid_pages() -> None:
    # Given: a store containing one event.
    store = MemoryAuditStore()
    event = _audit_event("evt_1", "user.created", "user_1")
    _ = store.append(event)

    # When / Then: duplicate append and invalid pagination are stable.
    with pytest.raises(DuplicateAuditEventError, match="evt_1"):
        _ = store.append(event)
    with pytest.raises(InvalidPageError, match="limit"):
        _ = store.list(limit=-1, offset=0, filters=AuditFilters())
