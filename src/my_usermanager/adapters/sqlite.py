# pyright: reportAny=false, reportUnusedCallResult=false
"""SQLite-backed store implementations for my-usermanager protocols.

Requires only the Python standard library (sqlite3).  Pass an open
``sqlite3.Connection`` to each store; the caller owns the connection
life-cycle and any transaction management.

Schema bootstrap::

    from my_usermanager.adapters.sqlite import create_tables

    create_tables(conn)

All stores are synchronous and thread-safe when each thread uses its own
connection (the SQLite default).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from threading import RLock
from typing import ClassVar, Final, Literal, Self, cast

from my_usermanager.models import (
    AuditEvent,
    ExternalIdentity,
    Grant,
    Permission,
    Role,
    Scope,
    User,
    validate_identifier,
)
from my_usermanager.permissions import BUILTIN_ROLES
from my_usermanager.stores import (
    AuditFilters,
    DuplicateAuditEventError,
    DuplicateGrantError,
    DuplicateUserError,
    GrantNotFoundError,
    InvalidPageError,
    UserNotFoundError,
    UserQuery,
)
from my_usermanager.subjects import ExternalIdentityConflictError

__all__: Final[tuple[str, ...]] = (
    "SQLiteAuditStore",
    "SQLiteGrantStore",
    "SQLiteRoleStore",
    "SQLiteUserStore",
    "create_tables",
    "inspect_sqlite_schema",
    "migrate_sqlite_schema",
)

_SCHEMA_VERSION: Final = 2
_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS um_schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS um_users (
    user_id TEXT PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
    display_name TEXT, email TEXT, disabled INTEGER NOT NULL DEFAULT 0,
    system INTEGER NOT NULL DEFAULT 0, scope_type TEXT, scope_id TEXT
);
CREATE TABLE IF NOT EXISTS um_external_identities (
    provider TEXT NOT NULL, subject TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
    PRIMARY KEY (provider, subject)
);
CREATE INDEX IF NOT EXISTS um_ext_id_user_id ON um_external_identities(user_id);
CREATE TABLE IF NOT EXISTS um_grants (
    user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
    role_name TEXT NOT NULL DEFAULT '', permission_name TEXT NOT NULL DEFAULT '',
    scope_type TEXT NOT NULL DEFAULT '', scope_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, role_name, permission_name, scope_type, scope_id),
    CHECK ((role_name = '') != (permission_name = ''))
);
CREATE INDEX IF NOT EXISTS um_grants_user_id ON um_grants(user_id);
CREATE TABLE IF NOT EXISTS um_audit_events (
    event_id TEXT NOT NULL UNIQUE,
    timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
    target_type TEXT NOT NULL, target_id TEXT NOT NULL, scope_type TEXT,
    scope_id TEXT, result TEXT NOT NULL, reason TEXT, request_id TEXT,
    ip_address TEXT, user_agent TEXT, metadata TEXT NOT NULL DEFAULT '{}'
);
"""

_LIMIT_FIELD: Final = "limit"
_OFFSET_FIELD: Final = "offset"
_PAGE_ERROR_MESSAGE: Final = "must be greater than or equal to zero"
_ROW_STR_ERROR: Final = "expected SQLite column to be str"
_ROW_OPTIONAL_STR_ERROR: Final = "expected SQLite column to be str or None"
_ROW_INT_ERROR: Final = "expected SQLite column to be int"
_METADATA_OBJECT_ERROR: Final = "expected metadata to be an object"
_METADATA_STRING_ERROR: Final = "expected metadata keys and values to be strings"
_SCHEMA_PENDING_ERROR: Final = "cannot initialize schema while a transaction is pending"
_MIGRATION_PENDING_ERROR: Final = "cannot migrate schema while a transaction is pending"
_FOREIGN_KEYS_ERROR: Final = (
    "cannot initialize schema without SQLite foreign keys enabled"
)
_VALUE_ERROR: Final = "transaction_mode must be 'operation' or 'external'"
_TRANSACTION_MODE_ERROR: Final = "transaction_mode must be 'standalone' or 'external'"
_INSERT_IDENTITY_SQL: Final = (
    "INSERT INTO um_external_identities (provider, subject, user_id) VALUES (?, ?, ?)"
)
_SELECT_IDENTITY_USER_SQL: Final = (
    "SELECT user_id FROM um_external_identities WHERE provider = ? AND subject = ?"
)
_ORPHAN_GRANTS_SQL: Final = (
    "SELECT DISTINCT g.user_id FROM um_grants g "
    "LEFT JOIN um_users u ON u.user_id = g.user_id "
    "WHERE u.user_id IS NULL"
)
_INSERT_USER_SQL: Final = (
    "INSERT INTO um_users (user_id, username, first_name, last_name, display_name, "
    "email, disabled, system, scope_type, scope_id) VALUES "
    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_UPDATE_USER_SQL: Final = (
    "UPDATE um_users SET username=?, first_name=?, last_name=?, display_name=?, "
    "email=?, disabled=?, system=?, scope_type=?, scope_id=? WHERE user_id=?"
)
_LIST_USERS_SQL: Final = (
    "SELECT * FROM um_users {where} ORDER BY user_id LIMIT ? OFFSET ?"
)
_INSERT_ROLE_GRANT_SQL: Final = "INSERT INTO um_grants (user_id, role_name, permission_name, scope_type, scope_id) VALUES (?, ?, '', ?, ?)"  # noqa: E501
_DELETE_ROLE_GRANT_SQL: Final = "DELETE FROM um_grants WHERE user_id=? AND role_name=? AND permission_name='' AND scope_type=? AND scope_id=?"  # noqa: E501
_INSERT_PERM_GRANT_SQL: Final = "INSERT INTO um_grants (user_id, role_name, permission_name, scope_type, scope_id) VALUES (?, '', ?, ?, ?)"  # noqa: E501
_DELETE_PERM_GRANT_SQL: Final = "DELETE FROM um_grants WHERE user_id=? AND role_name='' AND permission_name=? AND scope_type=? AND scope_id=?"  # noqa: E501
_INSERT_AUDIT_SQL: Final = "INSERT INTO um_audit_events (event_id, timestamp, actor_id, action, target_type, target_id, scope_type, scope_id, result, reason, request_id, ip_address, user_agent, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"  # noqa: E501
_LIST_AUDIT_SQL: Final = (
    "SELECT * FROM um_audit_events {where} ORDER BY rowid LIMIT ? OFFSET ?"
)


def _prepare_schema_transaction(
    conn: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"],
    pending_error: str,
) -> None:
    if transaction_mode == "external":
        if not conn.in_transaction:
            raise RuntimeError(pending_error)
    elif conn.in_transaction:
        raise RuntimeError(pending_error)

    if transaction_mode == "standalone":
        _ = conn.execute("PRAGMA foreign_keys = ON")
    fk_enabled = cast(
        "tuple[int] | None", conn.execute("PRAGMA foreign_keys").fetchone()
    )
    if fk_enabled is None or fk_enabled[0] != 1:
        raise RuntimeError(_FOREIGN_KEYS_ERROR)


def _apply_create_tables(conn: sqlite3.Connection) -> None:
    for statement in (
        item.strip() for item in _CREATE_TABLES_SQL.split(";") if item.strip()
    ):
        _ = conn.execute(statement)


def create_tables(
    conn: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"] = "standalone",
) -> None:
    """Bootstrap canonical schema; standalone mode owns its commit."""
    if transaction_mode not in {"standalone", "external"}:
        raise ValueError(_TRANSACTION_MODE_ERROR)
    _prepare_schema_transaction(
        conn,
        transaction_mode=transaction_mode,
        pending_error=_SCHEMA_PENDING_ERROR,
    )
    if transaction_mode == "standalone":
        conn.execute("BEGIN IMMEDIATE")
    try:
        state = inspect_sqlite_schema(conn)
        if state == "canonical_unversioned":
            message = "unversioned my-usermanager schema requires migration"
            raise RuntimeError(message)  # noqa: TRY301
        _apply_create_tables(conn)
        row = _fetchone_row(
            conn.execute("SELECT version FROM um_schema_version LIMIT 1")
        )
        if row is None:
            _ = conn.execute(
                "INSERT INTO um_schema_version(version) VALUES (?)", (_SCHEMA_VERSION,)
            )
        elif _row_object(row, 0) != _SCHEMA_VERSION:
            version = _row_object(row, 0)
            message = f"unsupported my-usermanager schema version: {version}"
            raise RuntimeError(message)  # noqa: TRY301
        if transaction_mode == "standalone":
            conn.commit()
    except BaseException:
        if transaction_mode == "standalone":
            conn.rollback()
        raise


_UM_TABLE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "um_schema_version": ("version",),
    "um_users": (
        "user_id",
        "username",
        "first_name",
        "last_name",
        "display_name",
        "email",
        "disabled",
        "system",
        "scope_type",
        "scope_id",
    ),
    "um_external_identities": ("provider", "subject", "user_id"),
    "um_grants": ("user_id", "role_name", "permission_name", "scope_type", "scope_id"),
    "um_audit_events": (
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
    ),
}


def _um_table_is_canonical(  # noqa: PLR0911
    conn: sqlite3.Connection, table: str
) -> bool:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = tuple(cast("str", row[1]) for row in rows)
    if columns != _UM_TABLE_COLUMNS[table]:
        return False
    if table == "um_users":
        return rows[0][5] == 1 and rows[6][4] == "0" and rows[7][4] == "0"
    if table == "um_schema_version":
        metadata = tuple(
            (str(row[1]), str(row[2]).upper(), row[3], row[4], row[5]) for row in rows
        )
        return metadata == (("version", "INTEGER", 1, None, 0),)
    if table == "um_external_identities":
        foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(um_external_identities)"
        ).fetchall()
        return any(row[2] == "um_users" and row[6] == "CASCADE" for row in foreign_keys)
    if table == "um_grants":
        foreign_keys = conn.execute("PRAGMA foreign_key_list(um_grants)").fetchall()
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        sql = "" if sql_row is None or sql_row[0] is None else str(sql_row[0]).upper()
        return (
            any(row[2] == "um_users" and row[6] == "CASCADE" for row in foreign_keys)
            and "CHECK ((ROLE_NAME = '') != (PERMISSION_NAME = ''))" in sql
        )
    if table == "um_audit_events":
        return _um_audit_events_is_legacy_canonical(conn)
    return True


def _um_grants_is_legacy_canonical(conn: sqlite3.Connection) -> bool:
    """Recognize the prior grants layout that v2 can rebuild losslessly."""
    rows = conn.execute('PRAGMA table_info("um_grants")').fetchall()
    expected = (
        ("user_id", "TEXT", 1, None, 1),
        ("role_name", "TEXT", 1, "''", 2),
        ("permission_name", "TEXT", 1, "''", 3),
        ("scope_type", "TEXT", 1, "''", 4),
        ("scope_id", "TEXT", 1, "''", 5),
    )
    metadata = tuple(
        (str(row[1]), str(row[2]).upper(), row[3], row[4], row[5]) for row in rows
    )
    foreign_keys = conn.execute("PRAGMA foreign_key_list(um_grants)").fetchall()
    if metadata != expected or foreign_keys:
        return False
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='um_grants'"
    ).fetchone()
    sql = (
        ""
        if sql_row is None or sql_row[0] is None
        else "".join(str(sql_row[0]).upper().split())
    )
    return (
        "PRIMARYKEY(USER_ID,ROLE_NAME,PERMISSION_NAME,SCOPE_TYPE,SCOPE_ID)" in sql
        and "REFERENCES" not in sql
        and "CHECK((ROLE_NAME='')!=(PERMISSION_NAME=''))" in sql
    )


def _um_audit_events_needs_rebuild(conn: sqlite3.Connection) -> bool:
    """Return whether v2 audit storage uses the released explicit rowid layout."""
    rows = conn.execute('PRAGMA table_info("um_audit_events")').fetchall()
    return bool(rows and rows[0][1] == "rowid")


def _um_audit_events_is_legacy_canonical(conn: sqlite3.Connection) -> bool:
    """Recognize the released v0.1 audit layouts."""
    rows = conn.execute('PRAGMA table_info("um_audit_events")').fetchall()
    expected = (
        ("event_id", "TEXT", 1, None, 0),
        ("timestamp", "TEXT", 1, None, 0),
        ("actor_id", "TEXT", 1, None, 0),
        ("action", "TEXT", 1, None, 0),
        ("target_type", "TEXT", 1, None, 0),
        ("target_id", "TEXT", 1, None, 0),
        ("scope_type", "TEXT", 0, None, 0),
        ("scope_id", "TEXT", 0, None, 0),
        ("result", "TEXT", 1, None, 0),
        ("reason", "TEXT", 0, None, 0),
        ("request_id", "TEXT", 0, None, 0),
        ("ip_address", "TEXT", 0, None, 0),
        ("user_agent", "TEXT", 0, None, 0),
        ("metadata", "TEXT", 1, "'{}'", 0),
    )
    metadata = tuple(
        (str(row[1]), str(row[2]).upper(), row[3], row[4], row[5]) for row in rows
    )
    explicit_rowid = (
        len(rows) == len(expected) + 1
        and tuple(rows[0][1:]) == ("rowid", "INTEGER", 0, None, 1)
        and metadata[1:] == expected
    )
    implicit_rowid = len(rows) == len(expected) and metadata == expected
    if not (explicit_rowid or implicit_rowid):
        return False

    index_rows = conn.execute('PRAGMA index_list("um_audit_events")').fetchall()
    if len(index_rows) != 1:
        return False
    index = index_rows[0]
    if tuple(index[2:]) != (1, "u", 0):
        return False
    index_name = str(index[1])
    index_columns = conn.execute(
        f'PRAGMA index_info("{index_name.replace(chr(34), chr(34) * 2)}")'
    ).fetchall()
    expected_cid = 1 if explicit_rowid else 0
    if len(index_columns) != 1 or tuple(index_columns[0]) != (
        0,
        expected_cid,
        "event_id",
    ):
        return False

    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='um_audit_events'"
    ).fetchone()
    if sql_row is None or sql_row[0] is None:
        return False
    sql = "".join(str(sql_row[0]).upper().replace('"', "").split())
    expected_sql = (
        "CREATETABLEUM_AUDIT_EVENTS("
        "ROWIDINTEGERPRIMARYKEYAUTOINCREMENT,"
        "EVENT_IDTEXTNOTNULLUNIQUE,TIMESTAMPTEXTNOTNULL,ACTOR_IDTEXTNOTNULL,"
        "ACTIONTEXTNOTNULL,TARGET_TYPETEXTNOTNULL,TARGET_IDTEXTNOTNULL,"
        "SCOPE_TYPETEXT,SCOPE_IDTEXT,RESULTTEXTNOTNULL,REASONTEXT,"
        "REQUEST_IDTEXT,IP_ADDRESSTEXT,USER_AGENTTEXT,"
        "METADATATEXTNOTNULLDEFAULT'{}')"
    )
    implicit_sql = expected_sql.replace("ROWIDINTEGERPRIMARYKEYAUTOINCREMENT,", "", 1)
    return sql in {expected_sql, implicit_sql}


def inspect_sqlite_schema(  # noqa: PLR0911
    conn: sqlite3.Connection,
) -> str:
    """Return empty, canonical_unversioned, current, or unsupported."""
    names = {
        cast("str", row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    um_names = {name for name in names if name.startswith("um_")}
    if not names:
        return "empty"
    expected_without_version = set(_UM_TABLE_COLUMNS) - {"um_schema_version"}
    if not um_names:
        return "empty"
    if not expected_without_version.issubset(um_names):
        return "unsupported"
    grants_canonical = _um_table_is_canonical(conn, "um_grants")
    grants_legacy = _um_grants_is_legacy_canonical(conn)
    audit_canonical = _um_table_is_canonical(conn, "um_audit_events")
    audit_legacy = _um_audit_events_is_legacy_canonical(conn)
    other_tables_canonical = all(
        _um_table_is_canonical(conn, table)
        for table in expected_without_version
        if table not in {"um_grants", "um_audit_events"}
    )
    if not all(
        (
            other_tables_canonical,
            grants_canonical or grants_legacy,
            audit_canonical or audit_legacy,
        )
    ):
        return "unsupported"
    has_version = "um_schema_version" in um_names
    if has_version and not _um_table_is_canonical(conn, "um_schema_version"):
        return "unsupported"
    if not has_version:
        return "canonical_unversioned"
    rows = conn.execute("SELECT version FROM um_schema_version").fetchall()
    return (
        "current" if len(rows) == 1 and rows[0][0] == _SCHEMA_VERSION else "unsupported"
    )


def migrate_sqlite_schema(  # noqa: C901, PLR0912
    conn: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"] = "standalone",
) -> None:
    """Explicitly stamp/migrate schema; standalone mode owns its commit."""
    if transaction_mode not in {"standalone", "external"}:
        raise ValueError(_TRANSACTION_MODE_ERROR)
    _prepare_schema_transaction(
        conn,
        transaction_mode=transaction_mode,
        pending_error=_MIGRATION_PENDING_ERROR,
    )
    if transaction_mode == "standalone":
        conn.execute("BEGIN IMMEDIATE")
    try:
        state = inspect_sqlite_schema(conn)
        if state == "current":
            needs_grants_rebuild = _um_grants_is_legacy_canonical(conn)
            needs_audit_rebuild = _um_audit_events_needs_rebuild(conn)
            if needs_grants_rebuild or needs_audit_rebuild:
                orphan_rows = _fetchall_rows(conn.execute(_ORPHAN_GRANTS_SQL))
                orphans = [_row_str(row, 0) for row in orphan_rows]
                if orphans:
                    message = f"orphan grants refuse migration: {', '.join(orphans)}"
                    raise RuntimeError(message)  # noqa: TRY301
                if needs_grants_rebuild:
                    _rebuild_grants_table(conn)
                if needs_audit_rebuild:
                    _rebuild_audit_events_table(conn)
            if transaction_mode == "standalone":
                conn.commit()
            return
        if state == "empty":
            _apply_create_tables(conn)
            conn.execute(
                "INSERT INTO um_schema_version(version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            if transaction_mode == "standalone":
                conn.commit()
            return
        if state != "canonical_unversioned":
            message = "unsupported my-usermanager schema version"
            raise RuntimeError(message)  # noqa: TRY301
        orphan_rows = _fetchall_rows(conn.execute(_ORPHAN_GRANTS_SQL))
        orphans = [_row_str(row, 0) for row in orphan_rows]
        if orphans:
            message = f"orphan grants refuse migration: {', '.join(orphans)}"
            raise RuntimeError(message)  # noqa: TRY301
        _rebuild_grants_table(conn)
        _rebuild_audit_events_table(conn)
        _apply_create_tables(conn)
        conn.execute(
            "INSERT INTO um_schema_version(version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        if transaction_mode == "standalone":
            conn.commit()
    except BaseException:
        if transaction_mode == "standalone":
            conn.rollback()
        raise


_CONNECTION_LOCKS: dict[int, tuple[sqlite3.Connection, RLock]] = {}
_CONNECTION_LOCKS_GUARD = RLock()


def _connection_lock(conn: sqlite3.Connection) -> RLock:
    key = id(conn)
    with _CONNECTION_LOCKS_GUARD:
        # sqlite3.Connection cannot be weak-referenced or instrumented, so
        # discard closed connections opportunistically while looking up a lock.
        for stale_key, (registered_conn, _) in tuple(_CONNECTION_LOCKS.items()):
            try:
                _ = registered_conn.in_transaction
            except sqlite3.ProgrammingError:
                del _CONNECTION_LOCKS[stale_key]
        registered = _CONNECTION_LOCKS.get(key)
        if registered is not None and registered[0] is conn:
            return registered[1]
        lock = RLock()
        _CONNECTION_LOCKS[key] = (conn, lock)
        return lock


class _Mutation:
    conn: sqlite3.Connection
    mode: str
    savepoint: str
    _lock: RLock

    def __init__(self, conn: sqlite3.Connection, mode: str) -> None:
        """Initialize mutation context."""
        self.conn, self.mode = conn, mode
        self.savepoint = "um_store_mutation"
        self._lock = _connection_lock(conn)

    def __enter__(self) -> Self:
        acquired = False
        try:
            if self.mode == "operation":
                self._lock = _connection_lock(self.conn)
                self._lock.acquire()
                acquired = True
                if self.conn.in_transaction:
                    message = _SCHEMA_PENDING_ERROR
                    raise RuntimeError(message)  # noqa: TRY301
            elif self.mode == "external" and not self.conn.in_transaction:
                message = _SCHEMA_PENDING_ERROR
                raise RuntimeError(message)  # noqa: TRY301
            if self.mode == "external":
                _ = self.conn.execute(f'SAVEPOINT "{self.savepoint}"')
        except BaseException:
            if acquired:
                self._lock.release()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            if self.mode == "external":
                if exc_type is not None:
                    _ = self.conn.execute(f'ROLLBACK TO SAVEPOINT "{self.savepoint}"')
                _ = self.conn.execute(f'RELEASE SAVEPOINT "{self.savepoint}"')
            elif exc_type is not None:
                self.conn.rollback()
            else:
                try:
                    self.conn.commit()
                except BaseException:
                    self.conn.rollback()
                    raise
        finally:
            if self.mode == "operation":
                self._lock.release()
        return False


def _mutation(conn: sqlite3.Connection, mode: str) -> _Mutation:
    return _Mutation(conn, mode)


def _grants_have_user_fk(conn: sqlite3.Connection) -> bool:
    rows = cast(
        "list[tuple[object, ...]]",
        conn.execute("PRAGMA foreign_key_list(um_grants)").fetchall(),
    )
    return any(row[2] == "um_users" and row[6] == "CASCADE" for row in rows)


def _rebuild_grants_table(conn: sqlite3.Connection) -> None:
    """Rebuild legacy grants so deletes cascade to users."""
    tables = {
        _row_object(row, 0)
        for row in _fetchall_rows(
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )
    }
    if "um_grants" not in tables or _grants_have_user_fk(conn):
        return
    statements = (
        """
        CREATE TABLE um_grants_new (
            user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
            role_name TEXT NOT NULL DEFAULT '',
            permission_name TEXT NOT NULL DEFAULT '',
            scope_type TEXT NOT NULL DEFAULT '', scope_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, role_name, permission_name, scope_type, scope_id),
            CHECK ((role_name = '') != (permission_name = ''))
        )
        """,
        """
        INSERT INTO um_grants_new(
            user_id, role_name, permission_name, scope_type, scope_id
        )
        SELECT user_id, role_name, permission_name, scope_type, scope_id
        FROM um_grants
        """,
        "DROP TABLE um_grants",
        "ALTER TABLE um_grants_new RENAME TO um_grants",
        "CREATE INDEX IF NOT EXISTS um_grants_user_id ON um_grants(user_id)",
    )
    for statement in statements:
        _ = conn.execute(statement)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rebuild_audit_events_table(conn: sqlite3.Connection) -> None:
    """Rebuild the v0.1 audit table without dropping event data or order."""
    if not _um_audit_events_is_legacy_canonical(conn):
        return
    statements = (
        """
        CREATE TABLE um_audit_events_new (
            event_id TEXT NOT NULL UNIQUE,
            timestamp TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
            target_type TEXT NOT NULL, target_id TEXT NOT NULL, scope_type TEXT,
            scope_id TEXT, result TEXT NOT NULL, reason TEXT, request_id TEXT,
            ip_address TEXT, user_agent TEXT, metadata TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        INSERT INTO um_audit_events_new(
            event_id, timestamp, actor_id, action, target_type, target_id,
            scope_type, scope_id, result, reason, request_id, ip_address,
            user_agent, metadata
        )
        SELECT event_id, timestamp, actor_id, action, target_type, target_id,
            scope_type, scope_id, result, reason, request_id, ip_address,
            user_agent, metadata
        FROM um_audit_events ORDER BY rowid
        """,
        "DROP TABLE um_audit_events",
        "ALTER TABLE um_audit_events_new RENAME TO um_audit_events",
    )
    for statement in statements:
        _ = conn.execute(statement)


def _validate_page(*, limit: int, offset: int) -> None:
    if limit < 0:
        raise InvalidPageError(_LIMIT_FIELD, limit, _PAGE_ERROR_MESSAGE)
    if offset < 0:
        raise InvalidPageError(_OFFSET_FIELD, offset, _PAGE_ERROR_MESSAGE)


def _scope_from_row(scope_type: str | None, scope_id: str | None) -> Scope:
    return Scope(scope_type=scope_type or None, scope_id=scope_id or None)


class _CompatRow:
    """Tuple-backed row supporting positional and column-name lookup."""

    __slots__: ClassVar[tuple[str, ...]] = ("_columns", "_values")
    _values: tuple[object, ...]
    _columns: tuple[str, ...]

    def __init__(self, values: tuple[object, ...], columns: tuple[str, ...]) -> None:
        self._values = values
        self._columns = columns

    def __getitem__(self, field: str | int) -> object:
        if isinstance(field, int):
            return self._values[field]
        return self._values[self._columns.index(field)]


def _compat_row(
    cursor: sqlite3.Cursor, row: tuple[object, ...] | None
) -> _CompatRow | None:
    if row is None:
        return None
    description = cursor.description
    columns = tuple(column[0] for column in description) if description else ()
    return _CompatRow(row, columns)


def _fetchone_row(cursor: sqlite3.Cursor) -> _CompatRow | None:
    row = cast("tuple[object, ...] | None", cursor.fetchone())
    return _compat_row(cursor, row)


def _fetchall_rows(cursor: sqlite3.Cursor) -> list[_CompatRow]:
    description = cursor.description
    columns = tuple(column[0] for column in description) if description else ()
    rows = cast("list[tuple[object, ...]]", cursor.fetchall())
    return [_CompatRow(row, columns) for row in rows]


def _row_object(row: _CompatRow, field: str | int) -> object:
    return row[field]


def _row_str(row: _CompatRow, field: str | int) -> str:
    value = _row_object(row, field)
    if not isinstance(value, str):
        raise TypeError(_ROW_STR_ERROR)
    return value


def _row_optional_str(row: _CompatRow, field: str | int) -> str | None:
    value = _row_object(row, field)
    if value is not None and not isinstance(value, str):
        raise TypeError(_ROW_OPTIONAL_STR_ERROR)
    return value


def _row_int(row: _CompatRow, field: str | int) -> int:
    value = _row_object(row, field)
    if not isinstance(value, int):
        raise TypeError(_ROW_INT_ERROR)
    return value


def _save_identities(
    conn: sqlite3.Connection,
    user_id: str,
    identities: frozenset[ExternalIdentity],
) -> None:
    _ = conn.execute("DELETE FROM um_external_identities WHERE user_id = ?", (user_id,))
    for identity in identities:
        try:
            _ = conn.execute(
                _INSERT_IDENTITY_SQL,
                (identity.provider, identity.subject, user_id),
            )
        except sqlite3.IntegrityError as err:
            row = _fetchone_row(
                conn.execute(
                    _SELECT_IDENTITY_USER_SQL,
                    (identity.provider, identity.subject),
                )
            )
            if row is not None and _row_str(row, "user_id") != user_id:
                raise ExternalIdentityConflictError(
                    identity=identity,
                    existing_user_id=_row_str(row, "user_id"),
                    requested_user_id=user_id,
                ) from err
            raise


def _user_from_row(
    row: _CompatRow,
    identities: frozenset[ExternalIdentity],
) -> User:
    return User(
        user_id=_row_str(row, "user_id"),
        external_identities=identities,
        username=_row_optional_str(row, "username"),
        first_name=_row_optional_str(row, "first_name"),
        last_name=_row_optional_str(row, "last_name"),
        display_name=_row_optional_str(row, "display_name"),
        email=_row_optional_str(row, "email"),
        disabled=bool(_row_int(row, "disabled")),
        system=bool(_row_int(row, "system")),
        scope=_scope_from_row(
            _row_optional_str(row, "scope_type"),
            _row_optional_str(row, "scope_id"),
        ),
    )


def _load_identities(
    conn: sqlite3.Connection,
    user_id: str,
) -> frozenset[ExternalIdentity]:
    rows = _fetchall_rows(
        conn.execute(
            "SELECT provider, subject FROM um_external_identities WHERE user_id = ?",
            (user_id,),
        )
    )
    return frozenset(
        ExternalIdentity(
            provider=_row_str(row, "provider"),
            subject=_row_str(row, "subject"),
        )
        for row in rows
    )


def _grant_from_row(row: _CompatRow) -> Grant:
    scope = _scope_from_row(_row_str(row, "scope_type"), _row_str(row, "scope_id"))
    role_name = _row_str(row, "role_name")
    permission_name = _row_str(row, "permission_name")
    if role_name:
        return Grant.for_role(_row_str(row, "user_id"), role_name, scope)
    return Grant.for_permission(
        _row_str(row, "user_id"),
        Permission(permission_name),
        scope,
    )


def _grant_sort_key(grant: Grant) -> tuple[str, str, str, str, str]:
    scope_type = "" if grant.scope.scope_type is None else grant.scope.scope_type
    scope_id = "" if grant.scope.scope_id is None else grant.scope.scope_id
    if grant.role_name is not None:
        return (grant.user_id, scope_type, scope_id, "role", grant.role_name)
    if grant.permission is not None:
        return (
            grant.user_id,
            scope_type,
            scope_id,
            "permission",
            grant.permission.name,
        )
    return (grant.user_id, scope_type, scope_id, "invalid", "")


def _audit_from_row(row: _CompatRow) -> AuditEvent:
    ts = datetime.fromisoformat(_row_str(row, "timestamp"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    metadata_text = _row_str(row, "metadata")
    metadata: dict[str, str] = {}
    if metadata_text:
        metadata_obj = cast("object", json.loads(metadata_text))
        if not isinstance(metadata_obj, dict):
            raise TypeError(_METADATA_OBJECT_ERROR)
        for key, value in cast("dict[object, object]", metadata_obj).items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError(_METADATA_STRING_ERROR)
            metadata[key] = value
    return AuditEvent(
        event_id=_row_str(row, "event_id"),
        timestamp=ts,
        actor_id=_row_str(row, "actor_id"),
        action=_row_str(row, "action"),
        target_type=_row_str(row, "target_type"),
        target_id=_row_str(row, "target_id"),
        scope=_scope_from_row(
            _row_optional_str(row, "scope_type"),
            _row_optional_str(row, "scope_id"),
        ),
        result=_row_str(row, "result"),
        reason=_row_optional_str(row, "reason"),
        request_id=_row_optional_str(row, "request_id"),
        ip_address=_row_optional_str(row, "ip_address"),
        user_agent=_row_optional_str(row, "user_agent"),
        metadata=metadata,
    )


def _matches_user_query_sql(query: UserQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if query.disabled is not None:
        clauses.append("disabled = ?")
        params.append(1 if query.disabled else 0)
    if query.system is not None:
        clauses.append("system = ?")
        params.append(1 if query.system else 0)
    if query.scope is not None:
        if query.scope.scope_type is None:
            clauses.append("scope_type IS NULL AND scope_id IS NULL")
        else:
            clauses.append("scope_type = ? AND scope_id = ?")
            params.extend([query.scope.scope_type, query.scope.scope_id])
    if query.text is not None:
        needle = f"%{query.text.casefold()}%"
        clauses.append("""
(lower(user_id) LIKE ? OR lower(coalesce(username,'')) LIKE ?
OR lower(coalesce(first_name,'')) LIKE ?
OR lower(coalesce(last_name,'')) LIKE ?
OR lower(coalesce(display_name,'')) LIKE ?
OR lower(coalesce(email,'')) LIKE ?)
""")
        params.extend([needle] * 6)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _append_optional_filter(
    clauses: list[str],
    params: list[object],
    clause: str,
    value: object | None,
) -> None:
    if value is None:
        return
    clauses.append(clause)
    params.append(value)


def _append_optional_time_filter(
    clauses: list[str],
    params: list[object],
    clause: str,
    value: datetime | None,
) -> None:
    if value is None:
        return
    clauses.append(clause)
    params.append(value.isoformat())


def _append_scope_filter(
    clauses: list[str],
    params: list[object],
    scope: Scope | None,
) -> None:
    if scope is None:
        return
    if scope.scope_type is None:
        clauses.append("scope_type IS NULL AND scope_id IS NULL")
        return
    clauses.append("scope_type = ? AND scope_id = ?")
    params.extend([scope.scope_type, scope.scope_id])


def _matches_audit_filters_sql(filters: AuditFilters) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    _append_optional_filter(clauses, params, "actor_id = ?", filters.actor_id)
    _append_optional_filter(clauses, params, "action = ?", filters.action)
    _append_optional_filter(clauses, params, "target_type = ?", filters.target_type)
    _append_optional_filter(clauses, params, "target_id = ?", filters.target_id)
    _append_optional_filter(clauses, params, "result = ?", filters.result)
    _append_optional_filter(clauses, params, "request_id = ?", filters.request_id)
    _append_scope_filter(clauses, params, filters.scope)
    _append_optional_time_filter(clauses, params, "timestamp >= ?", filters.since)
    _append_optional_time_filter(clauses, params, "timestamp <= ?", filters.until)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ---------------------------------------------------------------------------
# SQLiteUserStore
# ---------------------------------------------------------------------------


class SQLiteUserStore:
    """SQLite-backed UserStore and ExternalIdentityUserStore implementation."""

    __slots__: ClassVar[tuple[str, ...]] = ("_conn", "_mode")
    _conn: sqlite3.Connection
    _mode: str

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        transaction_mode: str = "operation",
    ) -> None:
        """Create a user store using the supplied connection."""
        if transaction_mode not in {"operation", "external"}:
            raise ValueError(_VALUE_ERROR)
        self._conn, self._mode = conn, transaction_mode

    def create(self, user: User) -> User:
        """Create and return a user, raising on duplicate identifiers."""
        try:
            with _mutation(self._conn, self._mode):
                _ = self._conn.execute(
                    _INSERT_USER_SQL,
                    (
                        user.user_id,
                        user.username,
                        user.first_name,
                        user.last_name,
                        user.display_name,
                        user.email,
                        int(user.disabled),
                        int(user.system),
                        user.scope.scope_type,
                        user.scope.scope_id,
                    ),
                )
                _save_identities(self._conn, user.user_id, user.external_identities)
        except ExternalIdentityConflictError:
            raise
        except sqlite3.IntegrityError as err:
            raise DuplicateUserError(user.user_id) from err

        return user

    def get(self, user_id: str) -> User | None:
        """Return a user by identifier, or None when missing."""
        checked = validate_identifier(user_id, field_name="user_id")
        row = _fetchone_row(
            self._conn.execute("SELECT * FROM um_users WHERE user_id = ?", (checked,))
        )
        return (
            None
            if row is None
            else _user_from_row(row, _load_identities(self._conn, checked))
        )

    def update(self, user: User) -> User:
        """Update and return a user, raising when it does not exist."""
        with _mutation(self._conn, self._mode):
            cur = self._conn.execute(
                _UPDATE_USER_SQL,
                (
                    user.username,
                    user.first_name,
                    user.last_name,
                    user.display_name,
                    user.email,
                    int(user.disabled),
                    int(user.system),
                    user.scope.scope_type,
                    user.scope.scope_id,
                    user.user_id,
                ),
            )
            if cur.rowcount == 0:
                raise UserNotFoundError(user.user_id)
            _save_identities(self._conn, user.user_id, user.external_identities)

        return user

    def list(self, *, limit: int, offset: int, query: UserQuery) -> tuple[User, ...]:
        """Return users matching the query and page."""
        _validate_page(limit=limit, offset=offset)
        where, params = _matches_user_query_sql(query)
        rows = _fetchall_rows(
            self._conn.execute(
                _LIST_USERS_SQL.format(where=where), (*params, limit, offset)
            )
        )
        return tuple(
            _user_from_row(row, _load_identities(self._conn, _row_str(row, "user_id")))
            for row in rows
        )

    def count_active(self) -> int:
        """Return the number of non-disabled users."""
        row = _fetchone_row(
            self._conn.execute("SELECT COUNT(*) FROM um_users WHERE disabled = 0")
        )
        return 0 if row is None else _row_int(row, 0)

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the user linked to an external identity, if any."""
        row = _fetchone_row(
            self._conn.execute(
                _SELECT_IDENTITY_USER_SQL,
                (identity.provider, identity.subject),
            )
        )
        return None if row is None else self.get(_row_str(row, "user_id"))

    def link_external_identity(
        self, *, user_id: str, identity: ExternalIdentity
    ) -> User:
        """Link an external identity to a user and return that user."""
        with _mutation(self._conn, self._mode):
            if self.get(user_id) is None:
                raise UserNotFoundError(user_id)
            row = _fetchone_row(
                self._conn.execute(
                    _SELECT_IDENTITY_USER_SQL,
                    (identity.provider, identity.subject),
                )
            )
            if row is not None and _row_str(row, "user_id") != user_id:
                raise ExternalIdentityConflictError(
                    identity=identity,
                    existing_user_id=_row_str(row, "user_id"),
                    requested_user_id=user_id,
                )
            try:
                _ = self._conn.execute(
                    _INSERT_IDENTITY_SQL,
                    (identity.provider, identity.subject, user_id),
                )
            except sqlite3.IntegrityError:
                raced = _fetchone_row(
                    self._conn.execute(
                        _SELECT_IDENTITY_USER_SQL,
                        (identity.provider, identity.subject),
                    )
                )
                if raced is None or _row_str(raced, "user_id") != user_id:
                    raise ExternalIdentityConflictError(
                        identity=identity,
                        existing_user_id=(
                            _row_str(raced, "user_id") if raced is not None else ""
                        ),
                        requested_user_id=user_id,
                    ) from None

        result = self.get(user_id)
        if result is None:
            raise UserNotFoundError(user_id)
        return result


class SQLiteRoleStore:
    """Read-only SQLite-backed RoleStore seeded with built-in roles."""

    __slots__: ClassVar[tuple[str, ...]] = ("_roles",)

    def __init__(self) -> None:
        """Create a role store containing only built-in roles."""
        self._roles: dict[str, Role] = dict(BUILTIN_ROLES)

    def get(self, role_name: str) -> Role | None:
        """Return a role by name or None when missing."""
        checked = validate_identifier(role_name, field_name="role_name")
        return self._roles.get(checked)

    def list(self) -> tuple[Role, ...]:
        """Return built-in roles sorted by role name."""
        return tuple(sorted(self._roles.values(), key=lambda role: role.name))


class SQLiteGrantStore:
    """SQLite-backed GrantStore implementation."""

    __slots__: ClassVar[tuple[str, ...]] = ("_conn", "_mode")
    _conn: sqlite3.Connection
    _mode: str

    def __init__(
        self, conn: sqlite3.Connection, *, transaction_mode: str = "operation"
    ) -> None:
        """Create a grant store using the supplied connection."""
        if transaction_mode not in {"operation", "external"}:
            raise ValueError(_VALUE_ERROR)
        self._conn, self._mode = conn, transaction_mode

    def _require_user(self, user_id: str) -> None:
        row = _fetchone_row(
            self._conn.execute("SELECT 1 FROM um_users WHERE user_id = ?", (user_id,))
        )
        if row is None:
            raise UserNotFoundError(user_id)

    def add_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Add and return a role grant."""
        grant = Grant.for_role(user_id, role_name, scope)
        try:
            with _mutation(self._conn, self._mode):
                self._require_user(user_id)
                _ = self._conn.execute(
                    _INSERT_ROLE_GRANT_SQL,
                    (
                        user_id,
                        role_name,
                        scope.scope_type or "",
                        scope.scope_id or "",
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DuplicateGrantError(grant) from err

        return grant

    def remove_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Remove and return a role grant."""
        grant = Grant.for_role(user_id, role_name, scope)
        with _mutation(self._conn, self._mode):
            cur = self._conn.execute(
                _DELETE_ROLE_GRANT_SQL,
                (user_id, role_name, scope.scope_type or "", scope.scope_id or ""),
            )
            if cur.rowcount == 0:
                raise GrantNotFoundError(grant)

        return grant

    def add_permission_grant(
        self, user_id: str, permission: Permission, scope: Scope
    ) -> Grant:
        """Add and return a permission grant."""
        grant = Grant.for_permission(user_id, permission, scope)
        try:
            with _mutation(self._conn, self._mode):
                self._require_user(user_id)
                _ = self._conn.execute(
                    _INSERT_PERM_GRANT_SQL,
                    (
                        user_id,
                        permission.name,
                        scope.scope_type or "",
                        scope.scope_id or "",
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DuplicateGrantError(grant) from err

        return grant

    def remove_permission_grant(
        self, user_id: str, permission: Permission, scope: Scope
    ) -> Grant:
        """Remove and return a permission grant."""
        grant = Grant.for_permission(user_id, permission, scope)
        with _mutation(self._conn, self._mode):
            cur = self._conn.execute(
                _DELETE_PERM_GRANT_SQL,
                (
                    user_id,
                    permission.name,
                    scope.scope_type or "",
                    scope.scope_id or "",
                ),
            )
            if cur.rowcount == 0:
                raise GrantNotFoundError(grant)

        return grant

    def list_grants_for_user(self, user_id: str) -> tuple[Grant, ...]:
        """Return all grants for a user in stable order."""
        checked = validate_identifier(user_id, field_name="user_id")
        rows = _fetchall_rows(
            self._conn.execute("SELECT * FROM um_grants WHERE user_id = ?", (checked,))
        )
        return tuple(
            sorted((_grant_from_row(row) for row in rows), key=_grant_sort_key)
        )


class SQLiteAuditStore:
    """SQLite-backed append-only AuditStore preserving insertion order."""

    __slots__: ClassVar[tuple[str, ...]] = ("_conn", "_mode")
    _conn: sqlite3.Connection
    _mode: str

    def __init__(
        self, conn: sqlite3.Connection, *, transaction_mode: str = "operation"
    ) -> None:
        """Create an audit store using the supplied connection."""
        if transaction_mode not in {"operation", "external"}:
            raise ValueError(_VALUE_ERROR)
        self._conn, self._mode = conn, transaction_mode

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append and return an audit event."""
        try:
            with _mutation(self._conn, self._mode):
                _ = self._conn.execute(
                    _INSERT_AUDIT_SQL,
                    (
                        event.event_id,
                        event.timestamp.isoformat(),
                        event.actor_id,
                        event.action,
                        event.target_type,
                        event.target_id,
                        event.scope.scope_type,
                        event.scope.scope_id,
                        event.result,
                        event.reason,
                        event.request_id,
                        event.ip_address,
                        event.user_agent,
                        json.dumps(dict(event.metadata)),
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DuplicateAuditEventError(event.event_id) from err

        return event

    def list(
        self, *, limit: int, offset: int, filters: AuditFilters
    ) -> tuple[AuditEvent, ...]:
        """Return audit events matching filters and page."""
        _validate_page(limit=limit, offset=offset)
        where, params = _matches_audit_filters_sql(filters)
        rows = _fetchall_rows(
            self._conn.execute(
                _LIST_AUDIT_SQL.format(where=where), (*params, limit, offset)
            )
        )
        return tuple(_audit_from_row(row) for row in rows)
