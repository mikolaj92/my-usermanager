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
from typing import ClassVar, Final, cast

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
)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS um_users (
    user_id      TEXT PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    last_name    TEXT,
    display_name TEXT,
    email        TEXT,
    disabled     INTEGER NOT NULL DEFAULT 0,
    system       INTEGER NOT NULL DEFAULT 0,
    scope_type   TEXT,
    scope_id     TEXT
);

CREATE TABLE IF NOT EXISTS um_external_identities (
    provider TEXT NOT NULL,
    subject  TEXT NOT NULL,
    user_id  TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
    PRIMARY KEY (provider, subject)
);

CREATE INDEX IF NOT EXISTS um_ext_id_user_id ON um_external_identities(user_id);

CREATE TABLE IF NOT EXISTS um_grants (
    user_id         TEXT NOT NULL,
    role_name       TEXT NOT NULL DEFAULT '',
    permission_name TEXT NOT NULL DEFAULT '',
    scope_type      TEXT NOT NULL DEFAULT '',
    scope_id        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, role_name, permission_name, scope_type, scope_id),
    CHECK ((role_name = '') != (permission_name = ''))
);

CREATE INDEX IF NOT EXISTS um_grants_user_id ON um_grants(user_id);

CREATE TABLE IF NOT EXISTS um_audit_events (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    timestamp    TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    action       TEXT NOT NULL,
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    scope_type   TEXT,
    scope_id     TEXT,
    result       TEXT NOT NULL,
    reason       TEXT,
    request_id   TEXT,
    ip_address   TEXT,
    user_agent   TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}'
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
_INSERT_IDENTITY_SQL: Final = """
INSERT OR IGNORE INTO um_external_identities
(provider, subject, user_id) VALUES (?, ?, ?)
"""
_SELECT_IDENTITY_USER_SQL: Final = """
SELECT user_id FROM um_external_identities
WHERE provider = ? AND subject = ?
"""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all my-usermanager tables if they do not exist."""
    _ = conn.executescript(_CREATE_TABLES_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_page(*, limit: int, offset: int) -> None:
    if limit < 0:
        raise InvalidPageError(_LIMIT_FIELD, limit, _PAGE_ERROR_MESSAGE)
    if offset < 0:
        raise InvalidPageError(_OFFSET_FIELD, offset, _PAGE_ERROR_MESSAGE)


def _scope_from_row(scope_type: str | None, scope_id: str | None) -> Scope:
    return Scope(scope_type=scope_type or None, scope_id=scope_id or None)


def _fetchone_row(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _fetchall_rows(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _row_object(row: sqlite3.Row, field: str | int) -> object:
    return cast("object", row[field])


def _row_str(row: sqlite3.Row, field: str | int) -> str:
    value = _row_object(row, field)
    if not isinstance(value, str):
        raise TypeError(_ROW_STR_ERROR)
    return value


def _row_optional_str(row: sqlite3.Row, field: str | int) -> str | None:
    value = _row_object(row, field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(_ROW_OPTIONAL_STR_ERROR)
    return value


def _row_int(row: sqlite3.Row, field: str | int) -> int:
    value = _row_object(row, field)
    if not isinstance(value, int):
        raise TypeError(_ROW_INT_ERROR)
    return value


def _user_from_row(
    row: sqlite3.Row,
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


def _save_identities(
    conn: sqlite3.Connection,
    user_id: str,
    identities: frozenset[ExternalIdentity],
) -> None:
    _ = conn.execute("DELETE FROM um_external_identities WHERE user_id = ?", (user_id,))
    _ = conn.executemany(
        _INSERT_IDENTITY_SQL,
        [(i.provider, i.subject, user_id) for i in identities],
    )


def _grant_from_row(row: sqlite3.Row) -> Grant:
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


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
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
    """SQLite-backed UserStore and ExternalIdentityUserStore implementation.

    Also implements the ``ExternalIdentityUserStore`` protocol from
    ``my_usermanager.subjects`` for passkey and OIDC identity linking.
    """

    __slots__: ClassVar[tuple[str, ...]] = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the store to an open SQLite connection."""
        conn.row_factory = sqlite3.Row
        self._conn: sqlite3.Connection = conn

    def create(self, user: User) -> User:
        """Store a new user or raise DuplicateUserError."""
        try:
            _ = self._conn.execute(
                """INSERT INTO um_users
                   (user_id, username, first_name, last_name, display_name,
                    email, disabled, system, scope_type, scope_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user.user_id,
                    user.username,
                    user.first_name,
                    user.last_name,
                    user.display_name,
                    user.email,
                    1 if user.disabled else 0,
                    1 if user.system else 0,
                    user.scope.scope_type,
                    user.scope.scope_id,
                ),
            )
        except sqlite3.IntegrityError as err:
            raise DuplicateUserError(user.user_id) from err
        _save_identities(self._conn, user.user_id, user.external_identities)
        self._conn.commit()
        return user

    def get(self, user_id: str) -> User | None:
        """Return a user by id or None when missing."""
        checked = validate_identifier(user_id, field_name="user_id")
        row = _fetchone_row(
            self._conn.execute("SELECT * FROM um_users WHERE user_id = ?", (checked,))
        )
        if row is None:
            return None
        return _user_from_row(row, _load_identities(self._conn, checked))

    def update(self, user: User) -> User:
        """Replace an existing user or raise UserNotFoundError."""
        cur = self._conn.execute(
            """UPDATE um_users SET
               username=?, first_name=?, last_name=?, display_name=?,
               email=?, disabled=?, system=?, scope_type=?, scope_id=?
               WHERE user_id=?""",
            (
                user.username,
                user.first_name,
                user.last_name,
                user.display_name,
                user.email,
                1 if user.disabled else 0,
                1 if user.system else 0,
                user.scope.scope_type,
                user.scope.scope_id,
                user.user_id,
            ),
        )
        if cur.rowcount == 0:
            raise UserNotFoundError(user.user_id)
        _save_identities(self._conn, user.user_id, user.external_identities)
        self._conn.commit()
        return user

    def list(self, *, limit: int, offset: int, query: UserQuery) -> tuple[User, ...]:
        """Return users sorted by user_id after applying query filters."""
        _validate_page(limit=limit, offset=offset)
        where, params = _matches_user_query_sql(query)
        # `where` comes from fixed clauses with all values bound as parameters.
        statement = f"SELECT * FROM um_users {where} ORDER BY user_id LIMIT ? OFFSET ?"  # noqa: S608
        rows = _fetchall_rows(
            self._conn.execute(
                statement,
                (*params, limit, offset),
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
        if row is None:
            return 0
        return _row_int(row, 0)

    # ------------------------------------------------------------------
    # ExternalIdentityUserStore protocol
    # ------------------------------------------------------------------

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the linked user or None when the identity is unlinked."""
        row = _fetchone_row(
            self._conn.execute(
                _SELECT_IDENTITY_USER_SQL,
                (identity.provider, identity.subject),
            )
        )
        if row is None:
            return None
        return self.get(_row_str(row, "user_id"))

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        """Link an external identity to an existing user or raise on conflict."""
        existing_row = _fetchone_row(
            self._conn.execute(
                _SELECT_IDENTITY_USER_SQL,
                (identity.provider, identity.subject),
            )
        )
        existing_user_id = (
            None if existing_row is None else _row_str(existing_row, "user_id")
        )
        if existing_user_id is not None and existing_user_id != user_id:
            raise ExternalIdentityConflictError(
                identity=identity,
                existing_user_id=existing_user_id,
                requested_user_id=user_id,
            )
        _ = self._conn.execute(
            _INSERT_IDENTITY_SQL,
            (identity.provider, identity.subject, user_id),
        )
        self._conn.commit()
        user = self.get(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        return user


# ---------------------------------------------------------------------------
# SQLiteRoleStore
# ---------------------------------------------------------------------------


class SQLiteRoleStore:
    """Read-only SQLite-backed RoleStore seeded with built-in roles.

    Built-in roles are kept in memory (identical to ``MemoryRoleStore``).
    Custom roles are not supported yet.
    """

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
        return tuple(sorted(self._roles.values(), key=lambda r: r.name))


# ---------------------------------------------------------------------------
# SQLiteGrantStore
# ---------------------------------------------------------------------------


class SQLiteGrantStore:
    """SQLite-backed GrantStore implementation."""

    __slots__: ClassVar[tuple[str, ...]] = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the store to an open SQLite connection."""
        conn.row_factory = sqlite3.Row
        self._conn: sqlite3.Connection = conn

    def add_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Store a role grant or raise DuplicateGrantError."""
        grant = Grant.for_role(user_id, role_name, scope)
        try:
            _ = self._conn.execute(
                """INSERT INTO um_grants
                   (user_id, role_name, permission_name, scope_type, scope_id)
                   VALUES (?, ?, '', ?, ?)""",
                (user_id, role_name, scope.scope_type or "", scope.scope_id or ""),
            )
        except sqlite3.IntegrityError as err:
            raise DuplicateGrantError(grant) from err
        self._conn.commit()
        return grant

    def remove_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Remove a role grant or raise GrantNotFoundError."""
        grant = Grant.for_role(user_id, role_name, scope)
        cur = self._conn.execute(
            """DELETE FROM um_grants
               WHERE user_id=? AND role_name=? AND permission_name=''
               AND scope_type=? AND scope_id=?""",
            (user_id, role_name, scope.scope_type or "", scope.scope_id or ""),
        )
        if cur.rowcount == 0:
            raise GrantNotFoundError(grant)
        self._conn.commit()
        return grant

    def add_permission_grant(
        self,
        user_id: str,
        permission: Permission,
        scope: Scope,
    ) -> Grant:
        """Store a direct permission grant or raise DuplicateGrantError."""
        grant = Grant.for_permission(user_id, permission, scope)
        try:
            _ = self._conn.execute(
                """INSERT INTO um_grants
                   (user_id, role_name, permission_name, scope_type, scope_id)
                   VALUES (?, '', ?, ?, ?)""",
                (
                    user_id,
                    permission.name,
                    scope.scope_type or "",
                    scope.scope_id or "",
                ),
            )
        except sqlite3.IntegrityError as err:
            raise DuplicateGrantError(grant) from err
        self._conn.commit()
        return grant

    def remove_permission_grant(
        self,
        user_id: str,
        permission: Permission,
        scope: Scope,
    ) -> Grant:
        """Remove a direct permission grant or raise GrantNotFoundError."""
        grant = Grant.for_permission(user_id, permission, scope)
        cur = self._conn.execute(
            """DELETE FROM um_grants
               WHERE user_id=? AND role_name='' AND permission_name=?
               AND scope_type=? AND scope_id=?""",
            (user_id, permission.name, scope.scope_type or "", scope.scope_id or ""),
        )
        if cur.rowcount == 0:
            raise GrantNotFoundError(grant)
        self._conn.commit()
        return grant

    def list_grants_for_user(self, user_id: str) -> tuple[Grant, ...]:
        """Return all grants for a user in deterministic order."""
        checked = validate_identifier(user_id, field_name="user_id")
        rows = _fetchall_rows(
            self._conn.execute("SELECT * FROM um_grants WHERE user_id = ?", (checked,))
        )
        grants = [_grant_from_row(row) for row in rows]
        return tuple(sorted(grants, key=_grant_sort_key))


# ---------------------------------------------------------------------------
# SQLiteAuditStore
# ---------------------------------------------------------------------------


class SQLiteAuditStore:
    """SQLite-backed append-only AuditStore preserving insertion order."""

    __slots__: ClassVar[tuple[str, ...]] = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the store to an open SQLite connection."""
        conn.row_factory = sqlite3.Row
        self._conn: sqlite3.Connection = conn

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append an audit event or raise DuplicateAuditEventError."""
        try:
            _ = self._conn.execute(
                """INSERT INTO um_audit_events
                   (event_id, timestamp, actor_id, action, target_type, target_id,
                    scope_type, scope_id, result, reason, request_id, ip_address,
                    user_agent, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        self._conn.commit()
        return event

    def list(
        self,
        *,
        limit: int,
        offset: int,
        filters: AuditFilters,
    ) -> tuple[AuditEvent, ...]:
        """Return append-ordered audit events after applying filters."""
        _validate_page(limit=limit, offset=offset)
        where, params = _matches_audit_filters_sql(filters)
        # `where` comes from fixed clauses with all values bound as parameters.
        statement = (
            f"SELECT * FROM um_audit_events {where} ORDER BY rowid LIMIT ? OFFSET ?"  # noqa: S608
        )
        rows = _fetchall_rows(
            self._conn.execute(
                statement,
                (*params, limit, offset),
            )
        )
        return tuple(_audit_from_row(row) for row in rows)
