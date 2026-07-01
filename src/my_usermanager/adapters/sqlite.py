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
from typing import ClassVar, Final

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


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all my-usermanager tables if they do not exist."""
    conn.executescript(_CREATE_TABLES_SQL)
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


def _user_from_row(
    row: sqlite3.Row,
    identities: frozenset[ExternalIdentity],
) -> User:
    return User(
        user_id=row["user_id"],
        external_identities=identities,
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        display_name=row["display_name"],
        email=row["email"],
        disabled=bool(row["disabled"]),
        system=bool(row["system"]),
        scope=_scope_from_row(row["scope_type"], row["scope_id"]),
    )


def _load_identities(
    conn: sqlite3.Connection,
    user_id: str,
) -> frozenset[ExternalIdentity]:
    rows = conn.execute(
        "SELECT provider, subject FROM um_external_identities WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return frozenset(
        ExternalIdentity(provider=r["provider"], subject=r["subject"]) for r in rows
    )


def _save_identities(
    conn: sqlite3.Connection,
    user_id: str,
    identities: frozenset[ExternalIdentity],
) -> None:
    conn.execute("DELETE FROM um_external_identities WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO um_external_identities "
        "(provider, subject, user_id) VALUES (?, ?, ?)",
        [(i.provider, i.subject, user_id) for i in identities],
    )


def _grant_from_row(row: sqlite3.Row) -> Grant:
    scope = _scope_from_row(row["scope_type"], row["scope_id"])
    role_name: str = row["role_name"]
    permission_name: str = row["permission_name"]
    if role_name:
        return Grant.for_role(row["user_id"], role_name, scope)
    return Grant.for_permission(
        row["user_id"],
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
    ts = datetime.fromisoformat(row["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    metadata: dict[str, str] = json.loads(row["metadata"]) if row["metadata"] else {}
    return AuditEvent(
        event_id=row["event_id"],
        timestamp=ts,
        actor_id=row["actor_id"],
        action=row["action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        scope=_scope_from_row(row["scope_type"], row["scope_id"]),
        result=row["result"],
        reason=row["reason"],
        request_id=row["request_id"],
        ip_address=row["ip_address"],
        user_agent=row["user_agent"],
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
        clauses.append(
            "(lower(user_id) LIKE ? OR lower(coalesce(username,'')) LIKE ?"
            " OR lower(coalesce(first_name,'')) LIKE ?"
            " OR lower(coalesce(last_name,'')) LIKE ?"
            " OR lower(coalesce(display_name,'')) LIKE ?"
            " OR lower(coalesce(email,'')) LIKE ?)"
        )
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
        self._conn = conn

    def create(self, user: User) -> User:
        """Store a new user or raise DuplicateUserError."""
        try:
            self._conn.execute(
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
        row = self._conn.execute(
            "SELECT * FROM um_users WHERE user_id = ?", (checked,)
        ).fetchone()
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
        statement = (
            f"SELECT * FROM um_users {where} "  # noqa: S608
            "ORDER BY user_id LIMIT ? OFFSET ?"
        )
        rows = self._conn.execute(
            statement,
            (*params, limit, offset),
        ).fetchall()
        return tuple(
            _user_from_row(r, _load_identities(self._conn, r["user_id"])) for r in rows
        )

    def count_active(self) -> int:
        """Return the number of non-disabled users."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM um_users WHERE disabled = 0"
        ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # ExternalIdentityUserStore protocol
    # ------------------------------------------------------------------

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the linked user or None when the identity is unlinked."""
        row = self._conn.execute(
            "SELECT user_id FROM um_external_identities "
            "WHERE provider = ? AND subject = ?",
            (identity.provider, identity.subject),
        ).fetchone()
        if row is None:
            return None
        return self.get(row["user_id"])

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        """Link an external identity to an existing user or raise on conflict."""
        existing_row = self._conn.execute(
            "SELECT user_id FROM um_external_identities "
            "WHERE provider = ? AND subject = ?",
            (identity.provider, identity.subject),
        ).fetchone()
        if existing_row is not None and existing_row["user_id"] != user_id:
            raise ExternalIdentityConflictError(
                identity=identity,
                existing_user_id=existing_row["user_id"],
                requested_user_id=user_id,
            )
        self._conn.execute(
            "INSERT OR IGNORE INTO um_external_identities "
            "(provider, subject, user_id) VALUES (?, ?, ?)",
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
        self._roles = dict(BUILTIN_ROLES)

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
        self._conn = conn

    def add_role_grant(self, user_id: str, role_name: str, scope: Scope) -> Grant:
        """Store a role grant or raise DuplicateGrantError."""
        grant = Grant.for_role(user_id, role_name, scope)
        try:
            self._conn.execute(
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
            self._conn.execute(
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
        rows = self._conn.execute(
            "SELECT * FROM um_grants WHERE user_id = ?", (checked,)
        ).fetchall()
        grants = [_grant_from_row(r) for r in rows]
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
        self._conn = conn

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append an audit event or raise DuplicateAuditEventError."""
        try:
            self._conn.execute(
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
            f"SELECT * FROM um_audit_events {where} "  # noqa: S608
            "ORDER BY rowid LIMIT ? OFFSET ?"
        )
        rows = self._conn.execute(
            statement,
            (*params, limit, offset),
        ).fetchall()
        return tuple(_audit_from_row(r) for r in rows)
