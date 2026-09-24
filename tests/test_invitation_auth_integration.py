from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from my_auth.enrollment import (  # pyright: ignore[reportMissingTypeStubs]
    EnrollmentCapabilityNotFound,
    SQLiteEnrollmentCapabilityStore,
)

from my_usermanager.adapters.my_auth_enrollment import (
    build_enrollment_capability_issuer,
)
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import (
    SQLiteAuditStore,
    SQLiteGrantStore,
    SQLiteUserStore,
)
from my_usermanager.adapters.sqlite_invitations import SQLiteInvitationStore
from my_usermanager.invitations import (
    InvitationActivation,
    InvitationGrant,
    InvitationService,
)
from my_usermanager.manager import UserManager
from my_usermanager.memory import MemoryRoleStore
from my_usermanager.models import ExternalIdentity, Permission, Scope, User
from my_usermanager.stores import AuditFilters

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Clock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _service(
    database: Path, clock: Clock
) -> tuple[InvitationService, sqlite3.Connection, SQLiteEnrollmentCapabilityStore]:
    SQLiteAuthDatabase(database).initialize()
    connection = sqlite3.connect(database, check_same_thread=False)
    _ = connection.execute("PRAGMA foreign_keys = ON")
    users = SQLiteUserStore(connection)
    grants = SQLiteGrantStore(connection)
    capabilities = SQLiteEnrollmentCapabilityStore(database, now=clock.now)
    service = InvitationService(
        manager=UserManager(users, MemoryRoleStore(), grants),
        users=users,
        identities=users,
        invitations=SQLiteInvitationStore(connection),
        enrollment=build_enrollment_capability_issuer(
            cast("object", capabilities)  # pyright: ignore[reportArgumentType]
        ),
        audit=SQLiteAuditStore(connection),
        now=clock.now,
    )
    _ = users.create(User("admin", "admin"))
    _ = grants.add_permission_grant(
        "admin", Permission("users.invite"), Scope.global_()
    )
    return service, connection, capabilities


def test_expired_invitation_reissue_uses_real_my_auth_sqlite_capabilities(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    service, connection, capabilities = _service(tmp_path / "identity.sqlite", clock)
    try:
        first = service.invite(
            actor_id="admin",
            user=User("anna", "anna", status="pending"),
            grants=(InvitationGrant(permission=Permission("workflow.run")),),
            ttl_seconds=60,
        )
        clock.value += timedelta(seconds=61)

        with pytest.raises(EnrollmentCapabilityNotFound):
            _ = capabilities.claim(
                token=first.token,
                flow_id="expired-flow",
                expected_purpose="invitation",
            )

        renewed = service.reissue(
            actor_id="admin",
            invitation_id=first.invitation.invitation_id,
            ttl_seconds=300,
        )

        with pytest.raises(EnrollmentCapabilityNotFound):
            _ = capabilities.claim(
                token=first.token,
                flow_id="old-token-flow",
                expected_purpose="invitation",
            )
        claimed = capabilities.claim(
            token=renewed.token,
            flow_id="new-token-flow",
            expected_purpose="invitation",
        )
        assert claimed.capability_id == renewed.invitation.capability_id
        _ = capabilities.consume(flow_id="new-token-flow")

        active = service.activate(
            InvitationActivation(
                invitation_id=renewed.invitation.invitation_id,
                capability_id=claimed.capability_id,
                identity=ExternalIdentity("my-auth", "anna"),
            )
        )

        assert active.status == "active"
        assert active.external_identities == frozenset(
            {ExternalIdentity("my-auth", "anna")}
        )
        events = SQLiteAuditStore(connection).list(
            limit=10, offset=0, filters=AuditFilters()
        )
        assert [event.action for event in events] == [
            "invitation.issue",
            "invitation.reissue",
            "invitation.activate",
        ]
    finally:
        connection.close()
