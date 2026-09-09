from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest

from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import create_tables
from my_usermanager.adapters.sqlite_sessions import (
    SQLiteSessionStore,
    create_session_tables,
)
from my_usermanager.models import Permission
from my_usermanager.sessions import SessionPrincipal, SessionTokenStore

if TYPE_CHECKING:
    from my_usermanager.stores import SessionRevoker


@pytest.fixture
def connection() -> Generator[sqlite3.Connection, None, None]:
    connection = sqlite3.connect(":memory:")
    _ = connection.execute("PRAGMA foreign_keys = ON")
    create_tables(connection)
    create_session_tables(connection)
    _ = connection.execute(
        """INSERT INTO um_users(user_id, username, status, disabled, system)
        VALUES ('user_123', 'alice', 'active', 0, 0)"""
    )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def test_sqlite_session_store_hashes_tokens_and_round_trips_principals(
    connection: sqlite3.Connection,
) -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    store = SQLiteSessionStore(connection, now=lambda: now, ttl_seconds=60)
    principal = SessionPrincipal(user_id="user_123", username="alice")

    record = store.create(
        "raw-cookie-token",
        principal,
        session_id="session_123",
        user_agent="browser",
        ip_address="192.0.2.1",
    )

    assert isinstance(store, SessionTokenStore)
    assert record.user_id == principal.user_id
    assert record.token_hash == hashlib.sha256(b"raw-cookie-token").hexdigest()
    assert record.expires_at == now + timedelta(seconds=60)
    assert store.get("raw-cookie-token") == principal
    assert store.list_for_user("user_123") == (record,)
    row = cast(
        "tuple[object, object] | None",
        connection.execute(
            "SELECT token_hash, principal_json FROM um_sessions WHERE session_id=?",
            ("session_123",),
        ).fetchone(),
    )
    assert row is not None
    assert row[0] != "raw-cookie-token"
    assert "raw-cookie-token" not in str(row)
    assert "raw-cookie-token" not in "".join(connection.iterdump())


def test_session_store_ttl_revoke_owner_scope_and_revoke_all(
    connection: sqlite3.Connection,
) -> None:
    _ = connection.execute(
        """INSERT INTO um_users(user_id, username, status, disabled, system)
        VALUES ('user_b', 'bob', 'active', 0, 0)"""
    )
    connection.commit()
    clock = [datetime(2026, 1, 1, 12, tzinfo=UTC)]
    store = SQLiteSessionStore(connection, now=lambda: clock[0], ttl_seconds=60)
    principal_a = SessionPrincipal(user_id="user_123")
    principal_b = SessionPrincipal(user_id="user_b")
    _ = store.create("token-a1", principal_a, session_id="session-a1")
    _ = store.create("token-a2", principal_a, session_id="session-a2")
    _ = store.create("token-b1", principal_b, session_id="session-b1")

    assert [item.session_id for item in store.list_for_user("user_123")] == [
        "session-a1",
        "session-a2",
    ]
    assert store.revoke("session-a1", user_id="user_b") is False
    assert store.get("token-a1") == principal_a
    assert store.revoke_all("user_123", except_session_id="session-a2") == 1
    assert store.get("token-a1") is None
    assert store.get("token-a2") == principal_a
    assert store.get("token-b1") == principal_b

    clock[0] += timedelta(seconds=61)
    assert store.get("token-a2") is None
    assert store.list_for_user("user_123") == ()


def test_disabled_user_cannot_use_a_previously_saved_session(
    connection: sqlite3.Connection,
) -> None:
    store = SQLiteSessionStore(connection)
    _ = store.create("raw-token", SessionPrincipal(user_id="user_123"))
    _ = connection.execute(
        "UPDATE um_users SET status='disabled', disabled=1 WHERE user_id='user_123'"
    )
    connection.commit()

    assert store.get("raw-token") is None


def test_session_store_refreshes_principal_from_host_owned_current_grants(
    connection: sqlite3.Connection,
) -> None:
    stored = SessionPrincipal(user_id="user_123", permissions=frozenset())
    refreshed = SessionPrincipal(
        user_id="user_123",
        permissions=frozenset({Permission("reports.read")}),
    )
    store = SQLiteSessionStore(
        connection,
        refresh_principal=lambda user_id: (
            refreshed if user_id == stored.user_id else None
        ),
    )
    _ = store.create("refresh-token", stored)

    assert store.get("refresh-token") == refreshed


def test_malformed_saved_principal_fails_closed(
    connection: sqlite3.Connection,
) -> None:
    store = SQLiteSessionStore(connection)
    _ = store.create(
        "malformed-token",
        SessionPrincipal(user_id="user_123"),
        session_id="session-malformed",
    )
    _ = connection.execute(
        "UPDATE um_sessions SET principal_json=? WHERE session_id=?",
        ('{"user_id": 123}', "session-malformed"),
    )
    # Use the real digest so the malformed row is reached without exposing a raw token.
    _ = connection.execute(
        "UPDATE um_sessions SET token_hash=? WHERE session_id IS NOT NULL",
        (hashlib.sha256(b"malformed-token").hexdigest(),),
    )
    connection.commit()

    assert store.get("malformed-token") is None


def test_shared_database_initialization_creates_session_schema(
    connection: sqlite3.Connection,
) -> None:
    SQLiteAuthDatabase(connection).initialize()

    rows = cast(
        "list[tuple[object, ...]]",
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall(),
    )
    names = {str(row[0]) for row in rows}
    assert "um_sessions" in names


def test_session_store_implements_save_delete_and_session_revoker(
    connection: sqlite3.Connection,
) -> None:
    store = SQLiteSessionStore(connection)
    principal = SessionPrincipal(user_id="user_123")

    assert store.save("raw-token", principal) == principal
    revoker: SessionRevoker = store
    revoker.revoke_sessions("user_123")

    assert store.get("raw-token") is None
    store.delete("raw-token")
    assert store.list_for_user("user_123") == ()


def test_session_store_purges_expired_metadata_under_host_retention_policy(
    connection: sqlite3.Connection,
) -> None:
    clock = [datetime(2026, 1, 1, 12, tzinfo=UTC)]
    store = SQLiteSessionStore(connection, now=lambda: clock[0], ttl_seconds=60)
    _ = store.create("retained-token", SessionPrincipal(user_id="user_123"))
    clock[0] += timedelta(seconds=61)

    assert store.purge_expired() == 1
    assert store.get("retained-token") is None
    assert connection.execute("SELECT COUNT(*) FROM um_sessions").fetchone() == (0,)


def test_external_session_mutations_roll_back_without_crud_ddl(
    connection: sqlite3.Connection,
) -> None:
    before = tuple(
        connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name='um_sessions'"
        ).fetchall()
    )
    store = SQLiteSessionStore(connection, transaction_mode="external")

    _ = connection.execute("BEGIN IMMEDIATE")

    def create_then_fail() -> None:
        _ = store.create(
            "rolled-back-token",
            SessionPrincipal(user_id="user_123"),
            session_id="rolled-back-session",
        )
        message = "rollback"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="rollback"):
        create_then_fail()
    connection.rollback()

    after = tuple(
        connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name='um_sessions'"
        ).fetchall()
    )
    assert before == after
    assert store.get("rolled-back-token") is None


def test_session_store_rejects_naive_clock(connection: sqlite3.Connection) -> None:
    naive = datetime.fromisoformat("2026-01-01")
    store = SQLiteSessionStore(
        connection,
        now=lambda: naive,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        _ = store.create("token", SessionPrincipal(user_id="user_123"))
