# pyright: reportAny=false, reportUnusedCallResult=false
# ruff: noqa: D102, D107
"""SQLite invitation metadata store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Literal, cast, final

from my_usermanager.invitations import (
    Invitation,
    InvitationError,
    InvitationGrant,
    InvitationStatus,
)
from my_usermanager.models import Permission, Scope

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS um_invitations (
    invitation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
    capability_id TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    grants_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'used', 'revoked')),
    created_at TEXT,
    completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS um_invitations_pending_user
    ON um_invitations(user_id) WHERE status = 'pending';
"""

_TRANSACTION_MODE_ERROR = "transaction_mode must be 'standalone' or 'external'"
_SCHEMA_PENDING_ERROR = "cannot initialize schema while a transaction is pending"
_FOREIGN_KEYS_ERROR = "cannot initialize schema without SQLite foreign keys enabled"


def _prepare_invitation_transaction(
    connection: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"],
) -> None:
    if transaction_mode == "external":
        if not connection.in_transaction:
            raise RuntimeError(_SCHEMA_PENDING_ERROR)
    elif connection.in_transaction:
        raise RuntimeError(_SCHEMA_PENDING_ERROR)

    if transaction_mode == "standalone":
        _ = connection.execute("PRAGMA foreign_keys = ON")
    fk_enabled = cast(
        "tuple[int] | None", connection.execute("PRAGMA foreign_keys").fetchone()
    )
    if fk_enabled is None or fk_enabled[0] != 1:
        raise RuntimeError(_FOREIGN_KEYS_ERROR)


def _apply_invitation_tables(connection: sqlite3.Connection) -> None:
    for statement in (item.strip() for item in _CREATE_SQL.split(";") if item.strip()):
        _ = connection.execute(statement)


def create_invitation_tables(
    connection: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"] = "standalone",
) -> None:
    """Create durable invitation metadata storage without raw token columns.

    Standalone mode owns its commit. External mode requires an open
    transaction and does not commit or roll back.
    """
    if transaction_mode not in {"standalone", "external"}:
        raise ValueError(_TRANSACTION_MODE_ERROR)
    _prepare_invitation_transaction(connection, transaction_mode=transaction_mode)
    if transaction_mode == "standalone":
        _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _apply_invitation_tables(connection)
        if transaction_mode == "standalone":
            connection.commit()
    except BaseException:
        if transaction_mode == "standalone":
            connection.rollback()
        raise


@final
class SQLiteInvitationStore:
    """Durable invitation metadata store backed by a caller-owned connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, invitation: Invitation) -> Invitation:
        try:
            self._connection.execute(
                """INSERT INTO um_invitations
                (invitation_id,user_id,capability_id,expires_at,issued_by,
                 grants_json,status,created_at,completed_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                _invitation_values(invitation),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as error:
            raise InvitationError from error
        return invitation

    def get(self, invitation_id: str) -> Invitation | None:
        row = self._connection.execute(
            "SELECT * FROM um_invitations WHERE invitation_id=?", (invitation_id,)
        ).fetchone()
        return None if row is None else _invitation_from_row(row)

    def get_pending_for_user(self, user_id: str) -> Invitation | None:
        row = self._connection.execute(
            "SELECT * FROM um_invitations WHERE user_id=? AND status='pending'",
            (user_id,),
        ).fetchone()
        return None if row is None else _invitation_from_row(row)

    def update(self, invitation: Invitation) -> Invitation:
        cursor = self._connection.execute(
            """UPDATE um_invitations SET user_id=?,capability_id=?,expires_at=?,
            issued_by=?,grants_json=?,status=?,created_at=?,completed_at=?
            WHERE invitation_id=?""",
            (*_invitation_values(invitation)[1:], invitation.invitation_id),
        )
        if cursor.rowcount != 1:
            raise InvitationError
        self._connection.commit()
        return invitation


def _invitation_values(invitation: Invitation) -> tuple[object, ...]:
    grants = [
        {
            "role_name": grant.role_name,
            "permission": None if grant.permission is None else grant.permission.name,
            "scope_type": grant.scope.scope_type,
            "scope_id": grant.scope.scope_id,
        }
        for grant in invitation.grants
    ]
    return (
        invitation.invitation_id,
        invitation.user_id,
        invitation.capability_id,
        invitation.expires_at.isoformat(),
        invitation.issued_by,
        json.dumps(grants, separators=(",", ":"), sort_keys=True),
        invitation.status,
        None if invitation.created_at is None else invitation.created_at.isoformat(),
        None
        if invitation.completed_at is None
        else invitation.completed_at.isoformat(),
    )


def _invitation_from_row(row: tuple[object, ...]) -> Invitation:
    raw_grants = json.loads(str(row[5]))
    grants = tuple(
        InvitationGrant(
            role_name=item["role_name"],
            permission=None
            if item["permission"] is None
            else Permission(item["permission"]),
            scope=Scope(item["scope_type"], item["scope_id"]),
        )
        for item in raw_grants
    )
    return Invitation(
        invitation_id=str(row[0]),
        user_id=str(row[1]),
        capability_id=str(row[2]),
        expires_at=datetime.fromisoformat(str(row[3])),
        issued_by=str(row[4]),
        grants=grants,
        status=cast("InvitationStatus", str(row[6])),
        created_at=None if row[7] is None else datetime.fromisoformat(str(row[7])),
        completed_at=None if row[8] is None else datetime.fromisoformat(str(row[8])),
    )
