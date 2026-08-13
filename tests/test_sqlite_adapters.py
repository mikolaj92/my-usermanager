# pyright: reportUnusedCallResult=false
"""Tests for SQLite-backed store implementations."""

import importlib
import sqlite3
import threading
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

from my_usermanager.adapters import sqlite as sqlite_adapter
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import (
    SQLiteAuditStore,
    SQLiteGrantStore,
    SQLiteRoleStore,
    SQLiteUserStore,
    create_tables,
    migrate_sqlite_schema,
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

_INJECTED_MY_AUTH_FAILURE = "injected my-auth migration failure"
_INJECTED_COMMIT_FAILURE = "injected commit failure"


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
    SQLiteUserStore(conn).create(User(user_id="user_123", username="user_123"))
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
    user = User(user_id="user_a", username="user_a", display_name="Alice")
    created = user_store.create(user)
    assert created == user
    assert user_store.get("user_a") == user


def test_user_store_get_missing_returns_none(user_store: SQLiteUserStore) -> None:
    assert user_store.get("missing") is None


def test_user_store_create_duplicate_raises(user_store: SQLiteUserStore) -> None:
    user = User(user_id="user_a", username="user_a")
    user_store.create(user)
    with pytest.raises(DuplicateUserError, match="user_a"):
        user_store.create(user)


def test_user_store_update_replaces_user(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_b", username="user_b", display_name="Alice"))
    updated = User(user_id="user_b", username="user_b", display_name="Alice Renamed")
    result = user_store.update(updated)
    assert result == updated
    assert user_store.get("user_b") == updated


def test_user_store_update_missing_raises(user_store: SQLiteUserStore) -> None:
    with pytest.raises(UserNotFoundError, match="missing"):
        user_store.update(User(user_id="missing", username="missing"))


def test_user_store_list_sorted_by_user_id(user_store: SQLiteUserStore) -> None:
    alice = User(user_id="user_b", username="user_b", display_name="Alice")
    bob = User(user_id="user_a", username="user_a", display_name="Bob", disabled=True)
    user_store.create(alice)
    user_store.create(bob)
    page = user_store.list(limit=1, offset=0, query=UserQuery())
    assert page == (bob,)


def test_user_store_list_text_filter(user_store: SQLiteUserStore) -> None:
    user_store.create(
        User(
            user_id="user_b",
            username="user_b",
            display_name="Alice Example",
            email="a@example.com",
        ),
    )
    user_store.create(User(user_id="user_a", username="user_a", display_name="Bob"))
    results = user_store.list(limit=10, offset=0, query=UserQuery(text="renamed"))
    assert results == ()
    results = user_store.list(limit=10, offset=0, query=UserQuery(text="alice"))
    assert len(results) == 1
    assert results[0].user_id == "user_b"


def test_user_store_list_disabled_filter(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="active", username="active"))
    user_store.create(User(user_id="disabled", username="disabled", disabled=True))
    active = user_store.list(limit=10, offset=0, query=UserQuery(disabled=False))
    disabled = user_store.list(limit=10, offset=0, query=UserQuery(disabled=True))
    assert active == (User(user_id="active", username="active"),)
    assert disabled == (User(user_id="disabled", username="disabled", disabled=True),)


def test_user_store_list_invalid_page(user_store: SQLiteUserStore) -> None:
    with pytest.raises(InvalidPageError, match="offset"):
        user_store.list(limit=10, offset=-1, query=UserQuery())
    with pytest.raises(InvalidPageError, match="limit"):
        user_store.list(limit=-1, offset=0, query=UserQuery())


def test_user_store_count_active(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_a", username="user_a"))
    user_store.create(User(user_id="user_b", username="user_b", disabled=True))
    assert user_store.count_active() == 1


def test_user_store_persists_external_identities(user_store: SQLiteUserStore) -> None:
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    user = User(
        user_id="user_a",
        username="user_a",
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
        User(
            user_id="user_a", username="user_a", external_identities=frozenset({old_id})
        ),
    )
    user_store.update(
        User(
            user_id="user_a", username="user_a", external_identities=frozenset({new_id})
        ),
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
    user = User(
        user_id="user_a", username="user_a", external_identities=frozenset({identity})
    )
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
    user_store.create(User(user_id="user_a", username="user_a"))
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    result = user_store.link_external_identity(user_id="user_a", identity=identity)
    assert result.user_id == "user_a"
    resolved = user_store.resolve_external_identity(identity)
    assert resolved is not None
    assert resolved.user_id == "user_a"


def test_link_external_identity_conflict_raises(user_store: SQLiteUserStore) -> None:
    user_store.create(User(user_id="user_a", username="user_a"))
    user_store.create(User(user_id="user_b", username="user_b"))
    identity = ExternalIdentity(provider="passkey", subject="cred_abc123")
    user_store.link_external_identity(user_id="user_a", identity=identity)
    with pytest.raises(ExternalIdentityConflictError):
        user_store.link_external_identity(user_id="user_b", identity=identity)


def test_link_external_identity_idempotent_for_same_user(
    user_store: SQLiteUserStore,
) -> None:
    user_store.create(User(user_id="user_a", username="user_a"))
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


def test_auth_database_operation_stores_can_cross_request_threads(
    tmp_path: Path,
) -> None:
    """Path-owned operation stores remain usable from TestClient threads."""
    database = SQLiteAuthDatabase(tmp_path / "auth.db")
    conn = sqlite3.connect(tmp_path / "auth.db")
    conn.execute("PRAGMA foreign_keys = ON")
    create_tables(conn)
    conn.close()

    stores = database.stores()
    stores.users.create(User(user_id="thread-user", username="thread-user"))
    errors: list[BaseException] = []

    def read_user() -> None:
        try:
            assert stores.users.get("thread-user") is not None
        except AssertionError as exc:
            errors.append(exc)

    thread = threading.Thread(target=read_user)
    thread.start()
    thread.join()
    stores.close()
    assert errors == []


def test_auth_database_caller_stores_commit_mutations_and_preserve_connection(
    conn: sqlite3.Connection,
) -> None:
    """Caller-owned stores commit each mutation without closing the connection."""
    database = SQLiteAuthDatabase(conn)
    stores = database.stores()

    stores.users.create(User(user_id="store-user", username="store-user"))
    grant = stores.grants.add_role_grant("store-user", "admin", Scope.global_())
    assert conn.in_transaction is False
    assert conn.execute(
        "SELECT user_id FROM um_users WHERE user_id = 'store-user'"
    ).fetchone() == ("store-user",)
    assert conn.execute("SELECT user_id, role_name FROM um_grants").fetchone() == (
        "store-user",
        "admin",
    )

    assert (
        stores.grants.remove_role_grant("store-user", "admin", Scope.global_()) == grant
    )
    assert conn.in_transaction is False
    stores.close()
    assert conn.execute("SELECT COUNT(*) FROM um_users").fetchone() == (1,)


def test_auth_database_caller_stores_refuse_pending_transaction(
    conn: sqlite3.Connection,
) -> None:
    """Stores refuse creation and mutation rather than owning caller work."""
    conn.execute("CREATE TABLE caller_pending (value TEXT NOT NULL)")
    conn.execute("INSERT INTO caller_pending (value) VALUES ('pending')")
    database = SQLiteAuthDatabase(conn)

    with pytest.raises(RuntimeError, match="transaction"):
        database.stores()
    assert conn.in_transaction
    assert conn.execute("SELECT value FROM caller_pending").fetchone() == ("pending",)

    conn.rollback()
    stores = database.stores()
    conn.execute("BEGIN")
    with pytest.raises(RuntimeError, match="transaction"):
        stores.users.create(User(user_id="not-written", username="not-written"))
    assert conn.in_transaction
    conn.rollback()
    assert stores.users.get("not-written") is None


def test_auth_database_initialize_rolls_back_um_when_my_auth_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed my-auth migration rolls back the preceding UM schema write."""

    class FailingAuthSchema:
        @staticmethod
        def inspect_sqlite_schema(_connection: sqlite3.Connection) -> object:
            return type("Inspection", (), {"state": "empty"})()

        @staticmethod
        def ensure_sqlite_schema(
            connection: sqlite3.Connection, *, transaction_mode: str
        ) -> None:
            assert transaction_mode == "external"
            connection.execute("CREATE TABLE my_auth_schema (schema_version INTEGER)")
            raise RuntimeError(_INJECTED_MY_AUTH_FAILURE)

    def import_failing_auth_schema(name: str) -> object:
        assert name == "my_auth.sqlite_schema"
        return FailingAuthSchema

    monkeypatch.setattr(importlib, "import_module", import_failing_auth_schema)
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(RuntimeError, match=_INJECTED_MY_AUTH_FAILURE):
            SQLiteAuthDatabase(conn).initialize()

        table_rows = cast(
            "list[tuple[object, ...]]",
            cast(
                "object",
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall(),
            ),
        )
        tables = {row[0] for row in table_rows}
        assert not tables.intersection(
            {
                "my_auth_schema",
                "um_schema_version",
                "um_users",
                "um_grants",
                "um_invitations",
            }
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("ddl", "extra_rows"),
    [
        ("CREATE TABLE um_schema_version (version INTEGER)", ()),
        (
            "CREATE TABLE um_schema_version (version INTEGER NOT NULL, extra TEXT)",
            (),
        ),
        ("CREATE TABLE um_schema_version (version INTEGER NOT NULL PRIMARY KEY)", ()),
        ("CREATE TABLE um_schema_version (version INTEGER NOT NULL)", (2,)),
    ],
    ids=("nullable", "extra-column", "unexpected-primary-key", "multiple-rows"),
)
def test_malformed_metadata_schema_is_unsupported_without_initialize_mutation(
    ddl: str,
    extra_rows: tuple[int, ...],
) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        create_tables(conn)
        conn.execute("DROP TABLE um_schema_version")
        conn.execute(ddl)
        conn.executemany(
            "INSERT INTO um_schema_version(version) VALUES (?)",
            [(version,) for version in (2, *extra_rows)],
        )
        conn.commit()
        before = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()

        assert sqlite_adapter.inspect_sqlite_schema(conn) == "unsupported"
        with pytest.raises(RuntimeError, match="unsupported"):
            SQLiteAuthDatabase(conn).initialize()
        assert (
            conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            == before
        )
    finally:
        conn.close()


def test_concurrent_migrations_stamp_schema_once(tmp_path: Path) -> None:
    database = tmp_path / "shared.sqlite"
    setup = sqlite3.connect(database)
    try:
        create_tables(setup)
        setup.execute("DROP TABLE um_schema_version")
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def migrate() -> None:
        connection = sqlite3.connect(database, timeout=5)
        try:
            barrier.wait()
            migrate_sqlite_schema(connection)
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    check = sqlite3.connect(database)
    try:
        assert check.execute(
            "SELECT COUNT(*), MIN(version), MAX(version) FROM um_schema_version"
        ).fetchone() == (1, 5, 5)
    finally:
        check.close()


def test_legacy_audit_schema_migrates_without_losing_events() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        create_tables(conn)
        conn.execute("DROP TABLE um_schema_version")
        conn.execute("DROP TABLE um_audit_events")
        conn.execute(
            """
            CREATE TABLE um_audit_events (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, scope_type TEXT,
                scope_id TEXT, result TEXT NOT NULL, reason TEXT, request_id TEXT,
                ip_address TEXT, user_agent TEXT, metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """INSERT INTO um_audit_events
            (event_id, timestamp, actor_id, action, target_type, target_id,
             scope_type, scope_id, result, reason, request_id, ip_address,
             user_agent, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-event",
                "2025-01-01T00:00:00+00:00",
                "actor",
                "login",
                "user",
                "user-1",
                None,
                None,
                "success",
                None,
                None,
                None,
                None,
                "{}",
            ),
        )
        conn.commit()

        # Fail closed on inspect: legacy audit is not a silent dual-read path.
        assert sqlite_adapter.inspect_sqlite_schema(conn) == "unsupported"
        sqlite_adapter.migrate_sqlite_schema(conn)

        assert sqlite_adapter.inspect_sqlite_schema(conn) == "current"
        assert conn.execute("SELECT event_id FROM um_audit_events").fetchall() == [
            ("legacy-event",)
        ]
        audit_columns = cast(
            "list[tuple[object, ...]]",
            cast(
                "object", conn.execute("PRAGMA table_info(um_audit_events)").fetchall()
            ),
        )
        assert tuple(row[1] for row in audit_columns) == (
            "event_id",
            "timestamp",
            "actor_id",
            "action",
            "target_type",
            "target_id",
            "scope_type",
            "scope_id",
            "result",
            "reason",
            "request_id",
            "ip_address",
            "user_agent",
            "metadata",
        )
    finally:
        conn.close()


def test_implicit_rowid_unversioned_audit_schema_stamps_without_losing_events() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        create_tables(conn)
        conn.execute("DROP TABLE um_schema_version")
        conn.execute("DROP TABLE um_audit_events")
        conn.execute(
            """
            CREATE TABLE um_audit_events (
                event_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, scope_type TEXT,
                scope_id TEXT, result TEXT NOT NULL, reason TEXT, request_id TEXT,
                ip_address TEXT, user_agent TEXT, metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """INSERT INTO um_audit_events
            (event_id, timestamp, actor_id, action, target_type, target_id,
             scope_type, scope_id, result, reason, request_id, ip_address,
             user_agent, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "implicit-event",
                "2025-01-01T00:00:00+00:00",
                "actor",
                "login",
                "user",
                "user-1",
                None,
                None,
                "success",
                None,
                None,
                None,
                None,
                "{}",
            ),
        )
        conn.commit()

        assert sqlite_adapter.inspect_sqlite_schema(conn) == "canonical_unversioned"
        sqlite_adapter.migrate_sqlite_schema(conn)

        assert conn.execute("SELECT event_id FROM um_audit_events").fetchall() == [
            ("implicit-event",)
        ]
        audit_columns = cast(
            "list[tuple[object, ...]]",
            cast(
                "object", conn.execute("PRAGMA table_info(um_audit_events)").fetchall()
            ),
        )
        assert tuple(row[1] for row in audit_columns) == (
            "event_id",
            "timestamp",
            "actor_id",
            "action",
            "target_type",
            "target_id",
            "scope_type",
            "scope_id",
            "result",
            "reason",
            "request_id",
            "ip_address",
            "user_agent",
            "metadata",
        )
    finally:
        conn.close()


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row], ids=["tuple", "row"])
def test_v2_explicit_rowid_schema_is_inspected_and_repaired(
    row_factory: type[sqlite3.Row] | None,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = row_factory
    try:
        create_tables(conn)
        conn.execute(
            "INSERT INTO um_users(user_id, username) VALUES (?, ?)",
            ("legacy-user", "legacy-user"),
        )
        conn.execute(
            "INSERT INTO um_grants(user_id, role_name) VALUES ('legacy-user', 'admin')"
        )
        conn.execute(
            """INSERT INTO um_audit_events
            (event_id, timestamp, actor_id, action, target_type, target_id, result)
            VALUES ('legacy-event', '2025-01-01T00:00:00+00:00', 'actor', 'login',
                    'user', 'legacy-user', 'success')"""
        )
        conn.commit()
        conn.execute("DROP TABLE um_schema_version")
        conn.execute("DROP TABLE um_grants")
        conn.execute("DROP TABLE um_audit_events")
        conn.execute(
            """
            CREATE TABLE um_grants (
                user_id TEXT NOT NULL,
                role_name TEXT NOT NULL DEFAULT '',
                permission_name TEXT NOT NULL DEFAULT '',
                scope_type TEXT NOT NULL DEFAULT '', scope_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, role_name, permission_name, scope_type, scope_id),
                CHECK ((role_name = '') != (permission_name = ''))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE um_audit_events (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, scope_type TEXT,
                scope_id TEXT, result TEXT NOT NULL, reason TEXT, request_id TEXT,
                ip_address TEXT, user_agent TEXT, metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "INSERT INTO um_grants(user_id, role_name) VALUES ('legacy-user', 'admin')"
        )
        conn.execute(
            """INSERT INTO um_audit_events
            (event_id, timestamp, actor_id, action, target_type, target_id, result)
            VALUES ('legacy-event', '2025-01-01T00:00:00+00:00', 'actor', 'login',
                    'user', 'legacy-user', 'success')"""
        )
        conn.execute("CREATE TABLE um_schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO um_schema_version(version) VALUES (4)")
        conn.commit()

        # Fail closed on inspect: legacy grants/audit require explicit migrate.
        assert sqlite_adapter.inspect_sqlite_schema(conn) == "unsupported"
        sqlite_adapter.migrate_sqlite_schema(conn)

        assert sqlite_adapter.inspect_sqlite_schema(conn) == "current"
        assert conn.execute("SELECT COUNT(*) FROM um_grants").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM um_audit_events").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            conn.execute("PRAGMA foreign_key_list(um_grants)").fetchone()[2]
            == "um_users"
        )
        grant_sql = cast(
            "str",
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'um_grants'"
            ).fetchone()[0],
        )
        assert "CHECK ((role_name = '') != (permission_name = ''))" in grant_sql
    finally:
        conn.close()


def test_auth_database_initialize_rejects_my_auth_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """my-auth legacy layouts fail closed; initialize does not auto-migrate them."""

    class LegacyAuthSchema:
        @staticmethod
        def inspect_sqlite_schema(_connection: sqlite3.Connection) -> object:
            return type("Inspection", (), {"state": "legacy"})()

        @staticmethod
        def ensure_sqlite_schema(
            connection: sqlite3.Connection, *, transaction_mode: str
        ) -> None:
            del connection, transaction_mode
            message = "ensure_sqlite_schema must not run for legacy"
            raise AssertionError(message)

    def import_legacy_auth_schema(name: str) -> object:
        assert name == "my_auth.sqlite_schema"
        return LegacyAuthSchema

    monkeypatch.setattr(importlib, "import_module", import_legacy_auth_schema)
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(RuntimeError, match="unsupported"):
            SQLiteAuthDatabase(conn).initialize()
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            == []
        )
    finally:
        conn.close()


def test_audit_schema_lookalike_is_rejected() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        create_tables(conn)
        conn.execute("DROP TABLE um_schema_version")
        conn.execute("DROP TABLE um_audit_events")
        conn.execute(
            """
            CREATE TABLE um_audit_events (
                event_id TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, scope_type TEXT,
                scope_id TEXT, result TEXT NOT NULL, reason TEXT, request_id TEXT,
                ip_address TEXT, user_agent TEXT, metadata TEXT NOT NULL DEFAULT '{}',
                CHECK (event_id <> '')
            )
            """
        )
        conn.commit()

        assert sqlite_adapter.inspect_sqlite_schema(conn) == "unsupported"
        with pytest.raises(RuntimeError, match="unsupported"):
            sqlite_adapter.migrate_sqlite_schema(conn)
    finally:
        conn.close()

    class FailingCommitConnection:
        in_transaction: bool = False
        fail_commit: bool

        def __init__(self) -> None:
            self.fail_commit = True

        def commit(self) -> None:
            if self.fail_commit:
                raise RuntimeError(_INJECTED_COMMIT_FAILURE)

        def rollback(self) -> None:
            return None

    connection = FailingCommitConnection()
    mutation = cast(
        "Callable[[sqlite3.Connection, str], AbstractContextManager[None]]",
        sqlite_adapter._mutation,  # pyright: ignore[reportPrivateUsage]
    )
    with (
        pytest.raises(RuntimeError, match=_INJECTED_COMMIT_FAILURE),
        mutation(cast("sqlite3.Connection", cast("object", connection)), "operation"),
    ):
        pass
    connection.fail_commit = False
    with mutation(cast("sqlite3.Connection", cast("object", connection)), "operation"):
        pass


def _sqlite_table_names(conn: sqlite3.Connection) -> set[object]:
    table_rows = cast(
        "list[tuple[object, ...]]",
        cast(
            "object",
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall(),
        ),
    )
    return {row[0] for row in table_rows}


def test_auth_database_initialize_creates_invitation_tables() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        SQLiteAuthDatabase(conn).initialize()
        SQLiteAuthDatabase(conn).initialize()

        assert "um_invitations" in _sqlite_table_names(conn)
        assert sqlite_adapter.inspect_sqlite_schema(conn) == "current"
    finally:
        conn.close()


def test_auth_database_initialize_stamps_invitations_on_current_schema() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        SQLiteAuthDatabase(conn).initialize()
        conn.execute("DROP TABLE um_invitations")
        conn.commit()
        assert sqlite_adapter.inspect_sqlite_schema(conn) == "current"
        assert "um_invitations" not in _sqlite_table_names(conn)

        SQLiteAuthDatabase(conn).initialize()

        assert "um_invitations" in _sqlite_table_names(conn)
        assert sqlite_adapter.inspect_sqlite_schema(conn) == "current"
    finally:
        conn.close()


def test_auth_database_initializes_caller_connection_with_foreign_keys() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        database = SQLiteAuthDatabase(conn)
        database.initialize()
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        conn.execute(
            "INSERT INTO um_users(user_id, username) VALUES (?, ?)",
            ("cascade-user", "cascade-user"),
        )
        conn.execute(
            "INSERT INTO um_grants(user_id, role_name) VALUES ('cascade-user', 'admin')"
        )
        conn.execute("DELETE FROM um_users WHERE user_id = 'cascade-user'")
        assert conn.execute("SELECT * FROM um_grants").fetchall() == []
    finally:
        conn.close()


def test_auth_database_transaction_refuses_pending_caller_transaction() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE pending (value TEXT NOT NULL)")
        conn.execute("INSERT INTO pending(value) VALUES ('keep')")
        with (
            pytest.raises(RuntimeError, match="transaction"),
            SQLiteAuthDatabase(conn).transaction(),
        ):
            pass
        assert conn.execute("SELECT value FROM pending").fetchone() == ("keep",)
        conn.rollback()
    finally:
        conn.close()


def test_external_store_requires_caller_transaction(conn: sqlite3.Connection) -> None:
    store = SQLiteUserStore(conn, transaction_mode="external")
    with pytest.raises(RuntimeError, match="transaction"):
        store.create(User(user_id="not-written", username="not-written"))
    assert conn.execute("SELECT * FROM um_users").fetchall() == []

    conn.execute("BEGIN")
    store.create(User(user_id="caller-owned", username="caller-owned"))
    assert conn.in_transaction
    conn.rollback()


def test_create_tables_external_rejects_foreign_keys_off_without_schema_changes() -> (
    None
):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")

        with pytest.raises(RuntimeError, match="foreign keys"):
            create_tables(conn, transaction_mode="external")

        assert conn.execute("PRAGMA foreign_keys").fetchone() == (0,)
        query = "SELECT name FROM sqlite_master WHERE type = 'table'"
        query += " AND name LIKE 'um_%'"
        assert conn.execute(query).fetchall() == []
        conn.rollback()
    finally:
        conn.close()


def test_create_tables_external_accepts_active_foreign_keys() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")

        create_tables(conn, transaction_mode="external")

        assert conn.in_transaction
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert conn.execute("SELECT version FROM um_schema_version").fetchone() == (5,)
        conn.rollback()
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'um_schema_version'"
            ).fetchone()
            is None
        )
    finally:
        conn.close()


def test_migrate_sqlite_schema_external_rejects_foreign_keys_off_without_changes() -> (
    None
):
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_tables(conn)
        conn.execute("DROP TABLE um_schema_version")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")

        with pytest.raises(RuntimeError, match="foreign keys"):
            migrate_sqlite_schema(conn, transaction_mode="external")

        assert conn.execute("PRAGMA foreign_keys").fetchone() == (0,)
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'um_schema_version'"
            ).fetchone()
            is None
        )
        conn.rollback()
    finally:
        conn.close()


def test_migrate_sqlite_schema_external_accepts_active_foreign_keys() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_tables(conn)
        conn.execute("DROP TABLE um_schema_version")
        conn.commit()
        conn.execute("BEGIN")

        migrate_sqlite_schema(conn, transaction_mode="external")

        assert conn.in_transaction
        assert conn.execute("SELECT version FROM um_schema_version").fetchone() == (5,)
        conn.rollback()
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'um_schema_version'"
            ).fetchone()
            is None
        )
    finally:
        conn.close()


def test_operation_connection_locks_cleanup_after_closed_connections(
    tmp_path: Path,
) -> None:
    """Closed path-owned operation connections do not retain one lock each."""

    database_path = tmp_path / "locks.db"
    setup = sqlite3.connect(database_path)
    create_tables(setup)
    setup.close()
    connection_locks_guard = sqlite_adapter._CONNECTION_LOCKS_GUARD  # pyright: ignore[reportPrivateUsage]
    connection_locks = sqlite_adapter._CONNECTION_LOCKS  # pyright: ignore[reportPrivateUsage]
    with connection_locks_guard:
        connection_locks.clear()

    database = SQLiteAuthDatabase(database_path)
    for index in range(20):
        stores = database.stores()
        stores.users.create(
            User(user_id=f"lock-user-{index}", username=f"lock-user-{index}")
        )
        stores.close()

    assert len(connection_locks) <= 1
