# pyright: reportAny=false, reportUnusedCallResult=false
"""Canonical SQLite schema inspection, bootstrap, and one-shot migration."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final, Literal, cast

if TYPE_CHECKING:
    import sqlite3

__all__: Final[tuple[str, ...]] = (
    "create_tables",
    "inspect_sqlite_schema",
    "migrate_sqlite_schema",
)

_SCHEMA_VERSION: Final = 5

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS um_schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS um_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    display_name TEXT,
    email TEXT,
    birth_date TEXT,
    gender TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    system INTEGER NOT NULL DEFAULT 0,
    scope_type TEXT,
    scope_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('pending', 'active', 'disabled', 'deleted'))
);
CREATE UNIQUE INDEX IF NOT EXISTS um_users_username_ci
    ON um_users(lower(username));
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

_ROW_STR_ERROR: Final = "expected SQLite column to be str"

_SCHEMA_PENDING_ERROR: Final = "cannot initialize schema while a transaction is pending"

_MIGRATION_PENDING_ERROR: Final = "cannot migrate schema while a transaction is pending"

_FOREIGN_KEYS_ERROR: Final = (
    "cannot initialize schema without SQLite foreign keys enabled"
)

_TRANSACTION_MODE_ERROR: Final = "transaction_mode must be 'standalone' or 'external'"

_ORPHAN_GRANTS_SQL: Final = (
    "SELECT DISTINCT g.user_id FROM um_grants g "
    "LEFT JOIN um_users u ON u.user_id = g.user_id "
    "WHERE u.user_id IS NULL"
)

_UM_TABLE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "um_schema_version": ("version",),
    "um_users": (
        "user_id",
        "username",
        "first_name",
        "last_name",
        "display_name",
        "email",
        "birth_date",
        "gender",
        "disabled",
        "system",
        "scope_type",
        "scope_id",
        "status",
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

_UM_AUDIT_EVENT_COLUMNS: Final[tuple[tuple[str, str, int, object, int], ...]] = (
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


def _um_table_is_canonical(  # noqa: PLR0911
    conn: sqlite3.Connection, table: str
) -> bool:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = tuple(cast("str", row[1]) for row in rows)
    if columns != _UM_TABLE_COLUMNS[table]:
        return False
    if table == "um_users":
        # user_id PK (pk=1), disabled/system default "0"
        # columns: 0 user_id, 1 username, ... 8 disabled, 9 system
        return (
            rows[0][5] == 1
            and rows[1][3] == 1  # username NOT NULL
            and rows[8][4] == "0"
            and rows[9][4] == "0"
            and rows[12][4] == "'active'"
        )
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
        return _um_audit_events_is_canonical(conn)
    return True


def _um_audit_events_matches_sql(
    conn: sqlite3.Connection, *, explicit_rowid: bool
) -> bool:
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
    if explicit_rowid:
        return sql == expected_sql
    implicit_sql = expected_sql.replace("ROWIDINTEGERPRIMARYKEYAUTOINCREMENT,", "", 1)
    return sql == implicit_sql


def _um_audit_events_unique_event_id(
    conn: sqlite3.Connection, *, event_id_cid: int
) -> bool:
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
    return len(index_columns) == 1 and tuple(index_columns[0]) == (
        0,
        event_id_cid,
        "event_id",
    )


def _um_audit_events_is_canonical(conn: sqlite3.Connection) -> bool:
    """Recognize the modern audit layout (implicit rowid, unique event_id)."""
    rows = conn.execute('PRAGMA table_info("um_audit_events")').fetchall()
    metadata = tuple(
        (str(row[1]), str(row[2]).upper(), row[3], row[4], row[5]) for row in rows
    )
    if len(rows) != len(_UM_AUDIT_EVENT_COLUMNS) or metadata != _UM_AUDIT_EVENT_COLUMNS:
        return False
    return _um_audit_events_unique_event_id(
        conn, event_id_cid=0
    ) and _um_audit_events_matches_sql(conn, explicit_rowid=False)


def _um_grants_is_legacy_migratable(conn: sqlite3.Connection) -> bool:
    """Recognize the prior grants layout that one-shot migrate can rebuild."""
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


def _um_audit_events_is_legacy_migratable(conn: sqlite3.Connection) -> bool:
    """Recognize the released explicit-rowid audit layout for one-shot migrate."""
    rows = conn.execute('PRAGMA table_info("um_audit_events")').fetchall()
    metadata = tuple(
        (str(row[1]), str(row[2]).upper(), row[3], row[4], row[5]) for row in rows
    )
    explicit_rowid = (
        len(rows) == len(_UM_AUDIT_EVENT_COLUMNS) + 1
        and tuple(rows[0][1:]) == ("rowid", "INTEGER", 0, None, 1)
        and metadata[1:] == _UM_AUDIT_EVENT_COLUMNS
    )
    if not explicit_rowid:
        return False
    return _um_audit_events_unique_event_id(
        conn, event_id_cid=1
    ) and _um_audit_events_matches_sql(conn, explicit_rowid=True)


def inspect_sqlite_schema(  # noqa: C901, PLR0911
    conn: sqlite3.Connection,
) -> str:
    """Return empty, canonical_unversioned, current, v2/v3/v4, or unsupported.

    Inspection is fail-closed: only the modern grants/audit layouts count as
    recognized versioned/unversioned states. Migratable legacy layouts are
    ``unsupported`` here and must go through explicit ``migrate_sqlite_schema``.
    """
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
    audit_canonical = _um_table_is_canonical(conn, "um_audit_events")
    users_v4 = _um_table_is_canonical(conn, "um_users")
    users_v3 = _um_users_is_v3_layout(conn)
    users_v2 = _um_users_is_v2_layout(conn)
    other_non_user_canonical = all(
        _um_table_is_canonical(conn, table)
        for table in expected_without_version
        if table not in {"um_grants", "um_audit_events", "um_users"}
    )
    if not all(
        (
            other_non_user_canonical,
            users_v4 or users_v3 or users_v2,
            grants_canonical,
            audit_canonical,
        )
    ):
        return "unsupported"
    has_version = "um_schema_version" in um_names
    if has_version and not _um_table_is_canonical(conn, "um_schema_version"):
        return "unsupported"
    if not has_version:
        return "canonical_unversioned"
    rows = conn.execute("SELECT version FROM um_schema_version").fetchall()
    if len(rows) != 1:
        return "unsupported"
    version = rows[0][0]
    if version == _SCHEMA_VERSION and users_v4:
        return "current"
    if version == _SCHEMA_VERSION - 1 and users_v4:
        return "v4"
    if version == _SCHEMA_VERSION - 2 and users_v3:
        return "v3"
    if version == _SCHEMA_VERSION - 3 and users_v2:
        return "v2"
    return "unsupported"


def _um_schema_is_migratable(conn: sqlite3.Connection) -> bool:  # noqa: PLR0911
    """Return whether an unsupported layout is a supported one-shot upgrade source."""
    names = {
        cast("str", row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    um_names = {name for name in names if name.startswith("um_")}
    expected_without_version = set(_UM_TABLE_COLUMNS) - {"um_schema_version"}
    if not expected_without_version.issubset(um_names):
        return False
    grants_ok = _um_table_is_canonical(
        conn, "um_grants"
    ) or _um_grants_is_legacy_migratable(conn)
    audit_ok = _um_table_is_canonical(
        conn, "um_audit_events"
    ) or _um_audit_events_is_legacy_migratable(conn)
    users_v4 = _um_table_is_canonical(conn, "um_users")
    users_v3 = _um_users_is_v3_layout(conn)
    users_v2 = _um_users_is_v2_layout(conn)
    other_ok = all(
        _um_table_is_canonical(conn, table)
        for table in expected_without_version
        if table not in {"um_grants", "um_audit_events", "um_users"}
    )
    if not all((other_ok, users_v4 or users_v3 or users_v2, grants_ok, audit_ok)):
        return False
    has_version = "um_schema_version" in um_names
    if has_version and not _um_table_is_canonical(conn, "um_schema_version"):
        return False
    if not has_version:
        return True
    rows = conn.execute("SELECT version FROM um_schema_version").fetchall()
    if len(rows) != 1:
        return False
    version = rows[0][0]
    if version == _SCHEMA_VERSION:
        return users_v4 and (
            _um_grants_is_legacy_migratable(conn)
            or _um_audit_events_is_legacy_migratable(conn)
        )
    if version == _SCHEMA_VERSION - 1:
        return users_v4
    if version == _SCHEMA_VERSION - 2:
        return users_v3
    if version == _SCHEMA_VERSION - 3:
        return users_v2
    return False


def _um_users_is_v3_layout(conn: sqlite3.Connection) -> bool:
    rows = conn.execute('PRAGMA table_info("um_users")').fetchall()
    return tuple(str(row[1]) for row in rows) == (
        "user_id",
        "username",
        "first_name",
        "last_name",
        "display_name",
        "email",
        "birth_date",
        "gender",
        "disabled",
        "system",
        "scope_type",
        "scope_id",
    )


def _um_users_is_v2_layout(conn: sqlite3.Connection) -> bool:
    """Recognize the v2 um_users layout (no birth_date/gender, username nullable)."""
    rows = conn.execute('PRAGMA table_info("um_users")').fetchall()
    columns = tuple(str(row[1]) for row in rows)
    return columns == (
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
    )


def _refuse_orphan_grants(conn: sqlite3.Connection) -> None:
    orphan_rows = _fetchall_rows(conn.execute(_ORPHAN_GRANTS_SQL))
    orphans = [_row_str(row, 0) for row in orphan_rows]
    if orphans:
        message = f"orphan grants refuse migration: {', '.join(orphans)}"
        raise RuntimeError(message)


def _upgrade_um_schema_to_current(conn: sqlite3.Connection) -> None:
    """One-shot upgrade path: rebuild migratable legacy tables, then stamp v5."""
    if _um_grants_is_legacy_migratable(conn):
        _refuse_orphan_grants(conn)
        _rebuild_grants_table(conn)
    if _um_audit_events_is_legacy_migratable(conn):
        _rebuild_audit_events_table(conn)
    if _um_users_is_v2_layout(conn):
        _migrate_users_v2_to_v3(conn)
    if _um_users_is_v3_layout(conn):
        _migrate_users_v3_to_v4(conn)
    tables = {
        cast("str", row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    version_row = (
        None
        if "um_schema_version" not in tables
        else _fetchone_row(
            conn.execute("SELECT version FROM um_schema_version LIMIT 1")
        )
    )
    current_version = None if version_row is None else _row_object(version_row, 0)
    if current_version != _SCHEMA_VERSION and _um_table_is_canonical(conn, "um_users"):
        _migrate_users_v4_to_v5(conn)
    _apply_create_tables(conn)
    if "um_schema_version" not in tables or version_row is None:
        _ = conn.execute("DELETE FROM um_schema_version")
        _ = conn.execute(
            "INSERT INTO um_schema_version(version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
    else:
        _ = conn.execute("UPDATE um_schema_version SET version = ?", (_SCHEMA_VERSION,))


def migrate_sqlite_schema(  # noqa: C901, PLR0912
    conn: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"] = "standalone",
) -> None:
    """Explicitly stamp/migrate schema; standalone mode owns its commit.

    Modern layouts use a single path. Supported legacy grant/audit layouts are
    upgraded only through this explicit one-shot migrate (inspection fails
    closed on them).
    """
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
            if transaction_mode == "standalone":
                conn.commit()
            return
        if state in {"v2", "v3", "v4"}:
            if state == "v2":
                _migrate_users_v2_to_v3(conn)
            if state in {"v2", "v3"}:
                _migrate_users_v3_to_v4(conn)
            _migrate_users_v4_to_v5(conn)
            _ = conn.execute(
                "UPDATE um_schema_version SET version = ?", (_SCHEMA_VERSION,)
            )
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
        if state == "canonical_unversioned":
            # Modern grants/audit already; upgrade older user layouts and stamp.
            if _um_users_is_v2_layout(conn):
                _migrate_users_v2_to_v3(conn)
            if _um_users_is_v3_layout(conn):
                _migrate_users_v3_to_v4(conn)
            if _um_table_is_canonical(conn, "um_users"):
                _migrate_users_v4_to_v5(conn)
            _apply_create_tables(conn)
            conn.execute(
                "INSERT INTO um_schema_version(version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            if transaction_mode == "standalone":
                conn.commit()
            return
        if not _um_schema_is_migratable(conn):
            message = "unsupported my-usermanager schema version"
            raise RuntimeError(message)  # noqa: TRY301
        _upgrade_um_schema_to_current(conn)
        if transaction_mode == "standalone":
            conn.commit()
    except BaseException:
        if transaction_mode == "standalone":
            conn.rollback()
        raise


def _migrate_users_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Extend the lifecycle constraint while preserving all FK child rows."""
    child_tables: list[tuple[str, tuple[str, ...], list[tuple[object, ...]]]] = []
    table_sql = (
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    )
    table_rows = conn.execute(table_sql).fetchall()
    for table_row in table_rows:
        table = str(table_row[0])
        if table == "um_users":
            continue
        quoted_table = table.replace('"', '""')
        foreign_keys = conn.execute(
            f'PRAGMA foreign_key_list("{quoted_table}")'
        ).fetchall()
        if not any(str(foreign_key[2]) == "um_users" for foreign_key in foreign_keys):
            continue
        columns = tuple(
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{quoted_table}")').fetchall()
        )
        rows = [
            tuple(row)
            for row in conn.execute(
                f'SELECT * FROM "{quoted_table}"'  # noqa: S608
            ).fetchall()
        ]
        child_tables.append((table, columns, rows))

    conn.execute(
        """CREATE TABLE um_users_new (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            display_name TEXT,
            email TEXT,
            birth_date TEXT,
            gender TEXT,
            disabled INTEGER NOT NULL DEFAULT 0,
            system INTEGER NOT NULL DEFAULT 0,
            scope_type TEXT,
            scope_id TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('pending', 'active', 'disabled', 'deleted'))
        )"""
    )
    conn.execute(
        """INSERT INTO um_users_new SELECT user_id, username, first_name,
        last_name, display_name, email, birth_date, gender, disabled, system,
        scope_type, scope_id, status FROM um_users"""
    )
    conn.execute("DROP TABLE um_users")
    conn.execute("ALTER TABLE um_users_new RENAME TO um_users")
    index_sql = (
        "CREATE UNIQUE INDEX IF NOT EXISTS um_users_username_ci "
        "ON um_users(lower(username))"
    )
    conn.execute(index_sql)
    for table, columns, rows in child_tables:
        if not rows:
            continue
        quoted_table = table.replace('"', '""')
        conn.execute(f'DELETE FROM "{quoted_table}"')  # noqa: S608
        quoted_columns = ", ".join(
            f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns
        )
        placeholders = ", ".join("?" for _ in columns)
        insert_sql = (
            f'INSERT INTO "{quoted_table}" ({quoted_columns}) '  # noqa: S608
            f"VALUES ({placeholders})"
        )
        conn.executemany(insert_sql, rows)


def _migrate_users_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Add explicit lifecycle status while preserving enabled users as active."""
    add_status_sql = (
        "ALTER TABLE um_users ADD COLUMN status TEXT NOT NULL DEFAULT 'active' "
        "CHECK (status IN ('pending', 'active', 'disabled'))"
    )
    set_status_sql = (
        "UPDATE um_users SET status = CASE WHEN disabled = 1 "
        "THEN 'disabled' ELSE 'active' END"
    )
    _ = conn.execute(add_status_sql)
    _ = conn.execute(set_status_sql)


def _migrate_users_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Upgrade um_users: mandatory unique username + optional birth_date/gender.

    Child rows (identities, grants) are snapshotted first: with foreign keys
    enabled, ``DROP TABLE um_users`` would cascade-delete them.
    """
    normalize_username_sql = (
        "UPDATE um_users SET username = user_id "
        "WHERE username IS NULL OR trim(username) = ''"
    )
    _ = conn.execute(normalize_username_sql)
    identity_rows = _fetchall_rows(
        conn.execute("SELECT provider, subject, user_id FROM um_external_identities")
    )
    select_grants_sql = (
        "SELECT user_id, role_name, permission_name, scope_type, scope_id "
        "FROM um_grants"
    )
    grant_rows = _fetchall_rows(conn.execute(select_grants_sql))
    statements = (
        """
        CREATE TABLE um_users_new (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            display_name TEXT,
            email TEXT,
            birth_date TEXT,
            gender TEXT,
            disabled INTEGER NOT NULL DEFAULT 0,
            system INTEGER NOT NULL DEFAULT 0,
            scope_type TEXT,
            scope_id TEXT
        )
        """,
        """
        INSERT INTO um_users_new(
            user_id, username, first_name, last_name, display_name, email,
            birth_date, gender, disabled, system, scope_type, scope_id
        )
        SELECT user_id, username, first_name, last_name, display_name, email,
            NULL, NULL, disabled, system, scope_type, scope_id
        FROM um_users
        """,
        "DROP TABLE um_users",
        "ALTER TABLE um_users_new RENAME TO um_users",
        """CREATE UNIQUE INDEX IF NOT EXISTS um_users_username_ci
        ON um_users(lower(username))""",
    )
    for statement in statements:
        _ = conn.execute(statement)
    for row in identity_rows:
        _ = conn.execute(
            """INSERT INTO um_external_identities (provider, subject, user_id)
            VALUES (?, ?, ?)""",
            (_row_str(row, 0), _row_str(row, 1), _row_str(row, 2)),
        )
    for row in grant_rows:
        _ = conn.execute(
            """INSERT INTO um_grants
            (user_id, role_name, permission_name, scope_type, scope_id)
            VALUES (?, ?, ?, ?, ?)""",
            (
                _row_str(row, 0),
                _row_str(row, 1),
                _row_str(row, 2),
                _row_str(row, 3),
                _row_str(row, 4),
            ),
        )


def _grants_have_user_fk(conn: sqlite3.Connection) -> bool:
    rows = cast(
        "list[tuple[object, ...]]",
        conn.execute("PRAGMA foreign_key_list(um_grants)").fetchall(),
    )
    return any(row[2] == "um_users" and row[6] == "CASCADE" for row in rows)


def _rebuild_grants_table(conn: sqlite3.Connection) -> None:
    """Rebuild migratable legacy grants so deletes cascade to users."""
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


def _rebuild_audit_events_table(conn: sqlite3.Connection) -> None:
    """Rebuild the explicit-rowid audit table without dropping event data or order."""
    if not _um_audit_events_is_legacy_migratable(conn):
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
