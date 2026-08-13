# pyright: reportAny=false, reportUnusedCallResult=false

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import (
    SQLiteUserStore,
    create_tables,
    inspect_sqlite_schema,
)
from my_usermanager.adapters.sqlite_invitations import (
    SQLiteInvitationStore,
    create_invitation_tables,
)
from my_usermanager.invitations import (
    Invitation,
    InvitationError,
    InvitationGrant,
)
from my_usermanager.models import User


def test_sqlite_invitation_store_persists_metadata_without_token() -> None:
    connection = sqlite3.connect(":memory:")
    create_tables(connection)
    create_invitation_tables(connection)
    SQLiteUserStore(connection).create(User("anna", "anna", status="pending"))
    invitation = Invitation(
        invitation_id="invite-1",
        user_id="anna",
        capability_id="capability-1",
        expires_at=datetime(2026, 8, 12, tzinfo=UTC),
        issued_by="admin",
        grants=(InvitationGrant(role_name="admin"),),
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    store = SQLiteInvitationStore(connection)

    store.create(invitation)

    assert store.get("invite-1") == invitation
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(um_invitations)")
    }
    assert "token" not in columns
    with pytest.raises(InvitationError):
        store.create(
            Invitation(
                invitation_id="invite-2",
                user_id="anna",
                capability_id="capability-2",
                expires_at=datetime(2026, 8, 12, tzinfo=UTC),
                issued_by="admin",
                grants=(InvitationGrant(role_name="admin"),),
            )
        )
    connection.close()


def test_create_invitation_tables_external_does_not_commit() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    create_tables(connection, transaction_mode="external")

    create_invitation_tables(connection, transaction_mode="external")

    assert connection.in_transaction
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "um_invitations" in tables
    connection.rollback()
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "um_invitations" not in tables
    connection.close()


def test_create_invitation_tables_external_requires_open_transaction() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    create_tables(connection)
    with pytest.raises(RuntimeError, match="transaction"):
        create_invitation_tables(connection, transaction_mode="external")
    connection.close()


def test_invitation_tables_keep_um_schema_inspect_current() -> None:
    connection = sqlite3.connect(":memory:")
    create_tables(connection)
    create_invitation_tables(connection)

    assert inspect_sqlite_schema(connection) == "current"
    connection.close()


def test_initialize_owns_invitation_ddl_without_a_second_call() -> None:
    connection = sqlite3.connect(":memory:")
    SQLiteAuthDatabase(connection).initialize()
    SQLiteUserStore(connection).create(User("anna", "anna", status="pending"))
    invitation = Invitation(
        invitation_id="invite-1",
        user_id="anna",
        capability_id="capability-1",
        expires_at=datetime(2026, 8, 12, tzinfo=UTC),
        issued_by="admin",
        grants=(InvitationGrant(role_name="admin"),),
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )

    stored = SQLiteInvitationStore(connection).create(invitation)

    assert stored == invitation
    assert inspect_sqlite_schema(connection) == "current"
    connection.close()
