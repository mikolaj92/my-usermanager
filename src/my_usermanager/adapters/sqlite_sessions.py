# pyright: reportAny=false, reportUnusedCallResult=false, reportPrivateUsage=false
"""Optional SQLite-backed opaque application sessions.

The host still generates and rotates the raw cookie token. This adapter stores
only its SHA-256 digest and a serialized :class:`SessionPrincipal`. It never
creates a table during a CRUD call; call :func:`create_session_tables` during
startup or migration.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast, final

from my_usermanager.adapters.sqlite_stores import _mutation
from my_usermanager.models import validate_identifier
from my_usermanager.sessions import SessionPrincipal

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Mapping

__all__: Final[tuple[str, ...]] = (
    "SQLiteSessionStore",
    "SessionRecord",
    "create_session_tables",
)

_CREATE_SQL: Final = """
CREATE TABLE IF NOT EXISTS um_sessions (
    session_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
    principal_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT,
    user_agent TEXT,
    ip_address TEXT,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);
CREATE INDEX IF NOT EXISTS um_sessions_user_id ON um_sessions(user_id);
CREATE INDEX IF NOT EXISTS um_sessions_expires_at ON um_sessions(expires_at);
"""
_SCHEMA_TRANSACTION_MODE_ERROR: Final = (
    "transaction_mode must be 'standalone' or 'external'"
)
_STORE_TRANSACTION_MODE_ERROR: Final = (
    "transaction_mode must be 'operation' or 'external'"
)
_PENDING_SCHEMA_ERROR: Final = (
    "cannot initialize session schema while a transaction is pending"
)
_FK_ERROR: Final = (
    "cannot initialize session schema without SQLite foreign keys enabled"
)
_TTL_ERROR: Final = "ttl_seconds must be positive"
_TOKEN_ERROR: Final = "session token must be a non-empty string"  # noqa: S105
_ACTIVE_USER_ERROR: Final = "session user must be active"
_NO_CURSOR_ERROR: Final = "session mutation returned no cursor"


def _check_schema_transaction_mode(mode: str) -> None:
    if mode not in {"standalone", "external"}:
        raise ValueError(_SCHEMA_TRANSACTION_MODE_ERROR)


def _check_store_transaction_mode(mode: str) -> None:
    if mode not in {"operation", "external"}:
        raise ValueError(_STORE_TRANSACTION_MODE_ERROR)


def _check_schema_preconditions(connection: sqlite3.Connection, mode: str) -> None:
    if mode == "external" and not connection.in_transaction:
        raise RuntimeError(_PENDING_SCHEMA_ERROR)
    if mode == "standalone" and connection.in_transaction:
        raise RuntimeError(_PENDING_SCHEMA_ERROR)
    if mode == "standalone":
        _ = connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys = cast(
        "tuple[int] | None", connection.execute("PRAGMA foreign_keys").fetchone()
    )
    if foreign_keys is None or foreign_keys[0] != 1:
        raise RuntimeError(_FK_ERROR)


def create_session_tables(
    connection: sqlite3.Connection,
    *,
    transaction_mode: str = "standalone",
) -> None:
    """Create session metadata tables without committing an external transaction."""
    _check_schema_transaction_mode(transaction_mode)
    _check_schema_preconditions(connection, transaction_mode)
    owns_transaction = transaction_mode == "standalone"
    if owns_transaction:
        _ = connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in (
            part.strip() for part in _CREATE_SQL.split(";") if part.strip()
        ):
            _ = connection.execute(statement)
        if owns_transaction:
            connection.commit()
    except BaseException:
        if owns_transaction:
            connection.rollback()
        raise


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = f"{field_name} must be timezone-aware"
        raise ValueError(message)
    return value.astimezone(UTC)


def _session_id(value: str) -> str:
    return validate_identifier(value, field_name="session_id")


def _row_values(cursor: sqlite3.Cursor) -> list[tuple[object, ...]]:
    return cast("list[tuple[object, ...]]", cursor.fetchall())


def _one_row(cursor: sqlite3.Cursor) -> tuple[object, ...] | None:
    return cast("tuple[object, ...] | None", cursor.fetchone())


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Safe owner-scoped metadata for one application session."""

    session_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    token_hash: str
    last_seen_at: datetime | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    revoked: bool = False


@final
class SQLiteSessionStore:
    """SQLite implementation of opaque sessions and session revocation.

    ``transaction_mode="operation"`` owns a commit for each mutation and
    rejects a connection with a pending transaction. ``external`` requires an
    already-open caller transaction and uses a savepoint, leaving commit and
    rollback to the caller. Read methods never start a transaction or perform
    DDL.

    ``refresh_principal`` is an optional host callback. When supplied, it is
    called with the local ``user_id`` after the account status and session TTL
    checks. Returning ``None`` denies the session; otherwise the returned
    principal is used instead of the serialized snapshot. Hosts can therefore
    re-project current local grants without making this adapter own policy.
    """

    __slots__ = (
        "_connection",
        "_now",
        "_refresh_principal",
        "_transaction_mode",
        "_ttl_seconds",
    )

    _connection: sqlite3.Connection
    _now: Callable[[], datetime]
    _refresh_principal: Callable[[str], SessionPrincipal | None] | None
    _transaction_mode: str
    _ttl_seconds: int

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        now: Callable[[], datetime] | None = None,
        ttl_seconds: int = 30 * 24 * 60 * 60,
        transaction_mode: str = "operation",
        refresh_principal: Callable[[str], SessionPrincipal | None] | None = None,
    ) -> None:
        """Bind the store to a caller-owned connection and explicit policy."""
        if ttl_seconds <= 0:
            raise ValueError(_TTL_ERROR)
        _check_store_transaction_mode(transaction_mode)
        self._connection = connection
        self._now = now or (lambda: datetime.now(UTC))
        self._refresh_principal = refresh_principal
        self._transaction_mode = transaction_mode
        self._ttl_seconds = ttl_seconds

    def create(
        self,
        token: str,
        principal: SessionPrincipal,
        *,
        session_id: str | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> SessionRecord:
        """Persist an opaque token and return safe metadata for the new session."""
        if not token:
            raise ValueError(_TOKEN_ERROR)
        created_at = _aware(self._now(), field_name="now")
        expires_at = created_at + timedelta(seconds=self._ttl_seconds)
        checked_session_id = _session_id(session_id or secrets.token_urlsafe(24))

        def insert() -> None:
            row = _one_row(
                self._connection.execute(
                    "SELECT status FROM um_users WHERE user_id=?",
                    (principal.user_id,),
                )
            )
            if row is None or row[0] != "active":
                raise ValueError(_ACTIVE_USER_ERROR)
            _ = self._connection.execute(
                """INSERT INTO um_sessions
                (session_id, token_hash, user_id, principal_json, created_at,
                 expires_at, last_seen_at, user_agent, ip_address, revoked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    checked_session_id,
                    _token_hash(token),
                    principal.user_id,
                    json.dumps(
                        principal.to_session(),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    created_at.isoformat(),
                    user_agent,
                    ip_address,
                ),
            )

        with _mutation(self._connection, self._transaction_mode):
            insert()
        return SessionRecord(
            session_id=checked_session_id,
            user_id=principal.user_id,
            created_at=created_at,
            expires_at=expires_at,
            token_hash=_token_hash(token),
            last_seen_at=created_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )

    def save(self, token: str, principal: SessionPrincipal) -> SessionPrincipal:
        """Implement the generic opaque-token session protocol."""
        _ = self.create(token, principal)
        return principal

    def get(self, token: str) -> SessionPrincipal | None:
        """Return a current principal for an active, unexpired session."""
        if not token:
            return None
        row = _one_row(
            self._connection.execute(
                """SELECT s.user_id, s.principal_json, s.expires_at
                FROM um_sessions AS s
                JOIN um_users AS u ON u.user_id=s.user_id
                WHERE s.token_hash=? AND s.revoked=0 AND u.status='active'""",
                (_token_hash(token),),
            )
        )
        if row is None:
            return None
        now = _aware(self._now(), field_name="now")
        expires_at = _parse_timestamp(row[2], field_name="expires_at")
        if expires_at is None or now >= expires_at:
            return None
        user_id = str(row[0])
        if self._refresh_principal is not None:
            return self._refresh_principal(user_id)
        return _principal_from_json(row[1])

    def delete(self, token: str) -> None:
        """Delete a session by token hash."""
        if not token:
            return
        with _mutation(self._connection, self._transaction_mode):
            _ = self._connection.execute(
                "DELETE FROM um_sessions WHERE token_hash=?",
                (_token_hash(token),),
            )

    def list_for_user(self, user_id: str) -> tuple[SessionRecord, ...]:
        """List active, unexpired sessions for exactly one local user."""
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        now = _aware(self._now(), field_name="now")
        cursor = self._connection.execute(
            """SELECT s.session_id, s.user_id, s.token_hash, s.created_at,
            s.expires_at, s.last_seen_at, s.user_agent, s.ip_address, s.revoked
            FROM um_sessions AS s
            JOIN um_users AS u ON u.user_id=s.user_id
            WHERE s.user_id=? AND s.revoked=0 AND u.status='active'
            ORDER BY s.created_at, s.session_id""",
            (checked_user_id,),
        )
        return tuple(
            record
            for row in _row_values(cursor)
            if (record := _record_from_row(row, now)) is not None
        )

    def revoke(self, session_id: str, *, user_id: str) -> bool:
        """Revoke one session only when its owner matches ``user_id``."""
        checked_session_id = _session_id(session_id)
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        cursor = self._execute_mutation(
            """UPDATE um_sessions SET revoked=1
            WHERE session_id=? AND user_id=? AND revoked=0""",
            (checked_session_id, checked_user_id),
        )
        return cursor.rowcount == 1

    def revoke_all(
        self,
        user_id: str,
        *,
        except_session_id: str | None = None,
    ) -> int:
        """Revoke all sessions for one user, optionally retaining one session."""
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        checked_session_id = (
            None if except_session_id is None else _session_id(except_session_id)
        )
        if checked_session_id is None:
            cursor = self._execute_mutation(
                "UPDATE um_sessions SET revoked=1 WHERE user_id=? AND revoked=0",
                (checked_user_id,),
            )
        else:
            cursor = self._execute_mutation(
                """UPDATE um_sessions SET revoked=1
                WHERE user_id=? AND session_id<>? AND revoked=0""",
                (checked_user_id, checked_session_id),
            )
        return cursor.rowcount

    def revoke_sessions(self, user_id: str) -> None:
        """Implement the SessionRevoker seam for account lifecycle hooks."""
        _ = self.revoke_all(user_id)

    def _execute_mutation(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> sqlite3.Cursor:
        cursor: sqlite3.Cursor | None = None
        with _mutation(self._connection, self._transaction_mode):
            cursor = self._connection.execute(statement, parameters)
        if cursor is None:  # pragma: no cover - execute always assigns it
            raise RuntimeError(_NO_CURSOR_ERROR)
        return cursor

    def purge_expired(self) -> int:
        """Delete expired metadata according to the host retention policy."""
        now = _aware(self._now(), field_name="now")
        cursor = self._execute_mutation(
            "DELETE FROM um_sessions WHERE expires_at <= ?",
            (now.isoformat(),),
        )
        return cursor.rowcount


def _parse_timestamp(value: object, *, field_name: str) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(str(value)), field_name=field_name)
    except ValueError:
        return None


def _principal_from_json(value: object) -> SessionPrincipal | None:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return SessionPrincipal.from_session(cast("Mapping[str, object]", payload))
    except (TypeError, ValueError):
        return None


def _record_from_row(row: tuple[object, ...], now: datetime) -> SessionRecord | None:
    expires_at = _parse_timestamp(row[4], field_name="expires_at")
    if expires_at is None or now >= expires_at:
        return None
    created_at = _parse_timestamp(row[3], field_name="created_at")
    if created_at is None:
        return None
    last_seen_at = (
        None if row[5] is None else _parse_timestamp(row[5], field_name="last_seen_at")
    )
    if row[5] is not None and last_seen_at is None:
        return None
    return SessionRecord(
        session_id=str(row[0]),
        user_id=str(row[1]),
        created_at=created_at,
        expires_at=expires_at,
        token_hash=str(row[2]),
        last_seen_at=last_seen_at,
        user_agent=None if row[6] is None else str(row[6]),
        ip_address=None if row[7] is None else str(row[7]),
        revoked=bool(row[8]),
    )
