from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from my_usermanager.adapters.sqlite import SQLiteUserStore, create_tables
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
