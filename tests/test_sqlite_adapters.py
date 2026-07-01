"""Tests for SQLite-backed store implementations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from my_usermanager.adapters.sqlite import (
    SQLiteAuditStore,
    SQLiteGrantStore,
    SQLiteRoleStore,
    SQLiteUserStore,
    create_tables,
)
from my_usermanager.models import (
    AuditEvent,
    ExternalIdentity,
    Grant,
    Permission,
    Scope,
    User,
)
from my_usermanager.stores import (
    AuditFilters,
    AuditStore,
    DuplicateAuditEventError,
    DuplicateGrantError,
    DuplicateUserError,
    GrantNotFoundError,
    GrantStore,
    InvalidPageError,
    UserNotFoundError,
    UserQuery,
    UserStore,
)
from my_usermanager.subjects import (
    ExternalIdentityConflictError,
    ExternalIdentityUserStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> Generator[sqlite3.Connection, None, None]:
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    create_tables(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def user_store(conn: sqlite3.Connection) -> SQLiteUserStore:
    return SQLiteUserStore(conn)


@pytest.fixture
def grant_store(conn: sqlite3.Connection) -> SQLiteGrantStore:
    return SQLiteGrantStore(conn)


@pytest.fixture
def audit_store(conn: sqlite3.Connection) -> SQLiteAuditStore:
    return SQLiteAuditStore(conn)


def _event(
    event_id: str,
    action: str = "user.created",
    target_id: str = "user_1",
) -> AuditEvent:
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


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_sqlite_user_store_satisfies_user_store_protocol(
    user_store: SQLiteUserStore,
) -> None:
    assert isinstance(user_store, UserStore)


def test_sqlite_user_store_satisfies_external_identity_user_store_protocol(
    user_store: SQLiteUserStore,
) -> None:
    assert isinstance(user_store, ExternalIdentityUserStore)


def test_sqlite_grant_store_satisfies_grant_store_protocol(
    grant_store: SQLiteGrantStore,
) -> None:
    assert isinstance(grant_store, GrantStore)


def test_sqlite_audit_store_satisfies_audit_store_protocol(
    audit_store: SQLiteAuditStore,
) -> None:
    assert isinstance(audit_store, AuditStore)


# ---------------------------------------------------------------------------
# SQLiteUserStore
# ---------------------------------------------------------------------------


def test_user_store_create_and_get(user_store: SQLiteUserStore) -> None:
    user = User(user_id="user_a", display_name="Alice")
    created = user_store.create(user)
    assert created == user
    assert user_store.get("user_a") == user


def test_user_store_get_missing_returns_none(user_store: SQLiteUserStore) -> None:
    assert user_store.get("missing") is None


def test_user_store_create_duplicate_raises(user_store: SQLiteUserStore) -> None:
    user = User(user_id="user_a")
    user_store.create(user)
    with pytest.raises(DuplicateUserError, match="user_a"):
        user_store.create(user)


def test_user_store_update_replaces_user(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_b", display_name="Alice"))
    updated = User(user_id="user_b", display_name="Alice Renamed")
    result = user_store.update(updated)
    assert result == updated
    assert user_store.get("user_b") == updated


def test_user_store_update_missing_raises(user_store: SQLiteUserStore) -> None:
    with pytest.raises(UserNotFoundError, match="missing"):
        user_store.update(User(user_id="missing"))


def test_user_store_list_sorted_by_user_id(user_store: SQLiteUserStore) -> None:
    alice = User(user_id="user_b", display_name="Alice")
    bob = User(user_id="user_a", display_name="Bob", disabled=True)
    user_store.create(alice)
    user_store.create(bob)
    page = user_store.list(limit=1, offset=0, query=UserQuery())
    assert page == (bob,)


def test_user_store_list_text_filter(user_store: SQLiteUserStore) -> None:
    user_store.create(
        User(user_id="user_b", display_name="Alice Example", email="a@example.com"),
    )
    user_store.create(User(user_id="user_a", display_name="Bob"))
    results = user_store.list(limit=10, offset=0, query=UserQuery(text="renamed"))
    assert results == ()
    results = user_store.list(limit=10, offset=0, query=UserQuery(text="alice"))
    assert len(results) == 1
    assert results[0].user_id == "user_b"


def test_user_store_list_disabled_filter(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="active"))
    user_store.create(User(user_id="disabled", disabled=True))
    active = user_store.list(limit=10, offset=0, query=UserQuery(disabled=False))
    disabled = user_store.list(limit=10, offset=0, query=UserQuery(disabled=True))
    assert active == (User(user_id="active"),)
    assert disabled == (User(user_id="disabled", disabled=True),)


def test_user_store_list_invalid_page(user_store: SQLiteUserStore) -> None:
    with pytest.raises(InvalidPageError, match="offset"):
        user_store.list(limit=10, offset=-1, query=UserQuery())
    with pytest.raises(InvalidPageError, match="limit"):
        user_store.list(limit=-1, offset=0, query=UserQuery())


def test_user_store_count_active(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_a"))
    user_store.create(User(user_id="user_b", disabled=True))
    assert user_store.count_active() == 1


def test_user_store_persists_external_identities(user_store: SQLiteUserStore) -> None:
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    user = User(
        user_id="user_a",
        external_identities=frozenset({identity}),
    )
    user_store.create(user)
    loaded = user_store.get("user_a")
    assert loaded is not None
    assert identity in loaded.external_identities


def test_user_store_update_replaces_external_identities(
    user_store: SQLiteUserStore,
) -> None:
    old_id = ExternalIdentity(provider="passkey", subject="cred_old")
    new_id = ExternalIdentity(provider="passkey", subject="cred_new")
    user_store.create(
        User(user_id="user_a", external_identities=frozenset({old_id})),
    )
    user_store.update(
        User(user_id="user_a", external_identities=frozenset({new_id})),
    )
    loaded = user_store.get("user_a")
    assert loaded is not None
    assert new_id in loaded.external_identities
    assert old_id not in loaded.external_identities


# ---------------------------------------------------------------------------
# ExternalIdentityUserStore methods on SQLiteUserStore
# ---------------------------------------------------------------------------


def test_resolve_external_identity_returns_user(user_store: SQLiteUserStore) -> None:
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    user = User(user_id="user_a", external_identities=frozenset({identity}))
    user_store.create(user)
    resolved = user_store.resolve_external_identity(identity)
    assert resolved is not None
    assert resolved.user_id == "user_a"


def test_resolve_external_identity_unknown_returns_none(
    user_store: SQLiteUserStore,
) -> None:
    identity = ExternalIdentity(provider="passkey", subject="cred_unknown")
    assert user_store.resolve_external_identity(identity) is None


def test_link_external_identity_adds_identity(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_a"))
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    result = user_store.link_external_identity(user_id="user_a", identity=identity)
    assert result.user_id == "user_a"
    resolved = user_store.resolve_external_identity(identity)
    assert resolved is not None
    assert resolved.user_id == "user_a"


def test_link_external_identity_conflict_raises(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_a"))
    user_store.create(User(user_id="user_b"))
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    user_store.link_external_identity(user_id="user_a", identity=identity)
    with pytest.raises(ExternalIdentityConflictError):
        user_store.link_external_identity(user_id="user_b", identity=identity)


def test_link_external_identity_idempotent_for_same_user(
    user_store: SQLiteUserStore,
) -> None:
    user_store.create(User(user_id="user_a"))
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    user_store.link_external_identity(user_id="user_a", identity=identity)
    result = user_store.link_external_identity(user_id="user_a", identity=identity)
    assert result.user_id == "user_a"


# ---------------------------------------------------------------------------
# SQLiteRoleStore
# ---------------------------------------------------------------------------


def test_role_store_lists_builtin_roles() -> None:
    store = SQLiteRoleStore()
    roles = store.list()
    assert len(roles) > 0
    assert all(r.name for r in roles)


def test_role_store_get_known_role() -> None:
    store = SQLiteRoleStore()
    roles = store.list()
    first_role = roles[0]
    assert store.get(first_role.name) == first_role


def test_role_store_get_unknown_returns_none() -> None:
    store = SQLiteRoleStore()
    assert store.get("nonexistent_role") is None


# ---------------------------------------------------------------------------
# SQLiteGrantStore
# ---------------------------------------------------------------------------


def test_grant_store_add_and_list_role_grant(grant_store: SQLiteGrantStore) -> None:
    scope = Scope.global_()
    grant = grant_store.add_role_grant("user_123", "admin", scope)
    assert grant == Grant.for_role("user_123", "admin", scope)
    listed = grant_store.list_grants_for_user("user_123")
    assert listed == (grant,)


def test_grant_store_add_and_list_permission_grant(
    grant_store: SQLiteGrantStore,
) -> None:
    scope = Scope.global_()
    perm = Permission("reports.read")
    grant = grant_store.add_permission_grant("user_123", perm, scope)
    assert grant == Grant.for_permission("user_123", perm, scope)
    listed = grant_store.list_grants_for_user("user_123")
    assert listed == (grant,)


def test_grant_store_deterministic_ordering(grant_store: SQLiteGrantStore) -> None:
    tenant_scope = Scope.scoped("tenant", "tenant_123")
    permission_grant = Grant.for_permission(
        "user_123",
        Permission("reports.read"),
        Scope.global_(),
    )
    role_grant = Grant.for_role("user_123", "admin", tenant_scope)

    grant_store.add_permission_grant(
        "user_123",
        Permission("reports.read"),
        Scope.global_(),
    )
    grant_store.add_role_grant("user_123", "admin", tenant_scope)

    listed = grant_store.list_grants_for_user("user_123")
    assert listed == (permission_grant, role_grant)


def test_grant_store_remove_role_grant(grant_store: SQLiteGrantStore) -> None:
    scope = Scope.global_()
    grant_store.add_role_grant("user_123", "admin", scope)
    removed = grant_store.remove_role_grant("user_123", "admin", scope)
    assert removed == Grant.for_role("user_123", "admin", scope)
    assert grant_store.list_grants_for_user("user_123") == ()


def test_grant_store_remove_permission_grant(grant_store: SQLiteGrantStore) -> None:
    scope = Scope.global_()
    perm = Permission("reports.read")
    grant_store.add_permission_grant("user_123", perm, scope)
    removed = grant_store.remove_permission_grant("user_123", perm, scope)
    assert removed == Grant.for_permission("user_123", perm, scope)
    assert grant_store.list_grants_for_user("user_123") == ()


def test_grant_store_duplicate_role_grant_raises(grant_store: SQLiteGrantStore) -> None:
    scope = Scope.global_()
    grant_store.add_role_grant("user_123", "admin", scope)
    with pytest.raises(DuplicateGrantError, match="user_123"):
        grant_store.add_role_grant("user_123", "admin", scope)


def test_grant_store_remove_missing_role_grant_raises(
    grant_store: SQLiteGrantStore,
) -> None:
    with pytest.raises(GrantNotFoundError):
        grant_store.remove_role_grant("user_123", "admin", Scope.global_())


def test_grant_store_remove_missing_permission_grant_raises(
    grant_store: SQLiteGrantStore,
) -> None:
    with pytest.raises(GrantNotFoundError, match=r"reports\.read"):
        grant_store.remove_permission_grant(
            "user_123", Permission("reports.read"), Scope.global_()
        )


def test_grant_store_list_missing_user_returns_empty(
    grant_store: SQLiteGrantStore,
) -> None:
    assert grant_store.list_grants_for_user("missing") == ()


# ---------------------------------------------------------------------------
# SQLiteAuditStore
# ---------------------------------------------------------------------------


def test_audit_store_append_and_list(audit_store: SQLiteAuditStore) -> None:
    event = _event("evt_1")
    stored = audit_store.append(event)
    assert stored == event
    listed = audit_store.list(limit=10, offset=0, filters=AuditFilters())
    assert listed == (event,)


def test_audit_store_preserves_append_order(audit_store: SQLiteAuditStore) -> None:
    created = _event("evt_1", action="user.created", target_id="user_1")
    updated = _event("evt_2", action="user.updated", target_id="user_1")
    other = _event("evt_3", action="user.created", target_id="user_2")
    audit_store.append(created)
    audit_store.append(updated)
    audit_store.append(other)

    page = audit_store.list(limit=2, offset=1, filters=AuditFilters())
    assert page == (updated, other)


def test_audit_store_filters_by_action_and_target(
    audit_store: SQLiteAuditStore,
) -> None:
    audit_store.append(_event("evt_1", action="user.created", target_id="user_1"))
    audit_store.append(_event("evt_2", action="user.updated", target_id="user_1"))
    audit_store.append(_event("evt_3", action="user.created", target_id="user_2"))

    filtered = audit_store.list(
        limit=10,
        offset=0,
        filters=AuditFilters(action="user.created", target_id="user_2"),
    )
    assert len(filtered) == 1
    assert filtered[0].event_id == "evt_3"


def test_audit_store_duplicate_event_raises(audit_store: SQLiteAuditStore) -> None:
    event = _event("evt_1")
    audit_store.append(event)
    with pytest.raises(DuplicateAuditEventError, match="evt_1"):
        audit_store.append(event)


def test_audit_store_invalid_page_raises(audit_store: SQLiteAuditStore) -> None:
    with pytest.raises(InvalidPageError, match="limit"):
        audit_store.list(limit=-1, offset=0, filters=AuditFilters())
    with pytest.raises(InvalidPageError, match="offset"):
        audit_store.list(limit=10, offset=-1, filters=AuditFilters())


def test_audit_store_filters_by_actor(audit_store: SQLiteAuditStore) -> None:
    audit_store.append(_event("evt_1"))
    other = AuditEvent(
        event_id="evt_2",
        timestamp=datetime(2025, 1, 2, tzinfo=UTC),
        actor_id="other_actor",
        action="user.created",
        target_type="user",
        target_id="user_2",
        scope=Scope.global_(),
        result="success",
    )
    audit_store.append(other)

    filtered = audit_store.list(
        limit=10,
        offset=0,
        filters=AuditFilters(actor_id="other_actor"),
    )
    assert filtered == (other,)
