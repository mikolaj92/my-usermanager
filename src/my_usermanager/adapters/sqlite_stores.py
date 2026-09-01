# pyright: reportAny=false, reportUnusedCallResult=false
"""SQLite-backed store implementations for my-usermanager protocols.

Requires only the Python standard library (sqlite3).  Pass an open
``sqlite3.Connection`` to each store; the caller owns the connection
life-cycle and any transaction management.

All stores are synchronous and thread-safe when each thread uses its own
connection (the SQLite default).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from threading import RLock
from typing import ClassVar, Final, Self, cast

from my_usermanager.models import (
    AccountStatus,
    AuditEvent,
    ExternalIdentity,
    Gender,
    Grant,
    Permission,
    Scope,
    User,
    validate_gender,
    validate_identifier,
)
from my_usermanager.stores import (
    AuditFilters,
    DuplicateAuditEventError,
    DuplicateGrantError,
    DuplicateUserError,
    DuplicateUsernameError,
    GrantNotFoundError,
    InvalidPageError,
    UserNotFoundError,
    UserQuery,
)
from my_usermanager.subjects import ExternalIdentityConflictError

__all__: Final[tuple[str, ...]] = (
    "ImmediateTransaction",
    "SQLiteAuditStore",
    "SQLiteGrantStore",
    "SQLiteUserStore",
    "immediate_transaction",
)


_LIMIT_FIELD: Final = "limit"
_OFFSET_FIELD: Final = "offset"
_PAGE_ERROR_MESSAGE: Final = "must be greater than or equal to zero"
_ROW_STR_ERROR: Final = "expected SQLite column to be str"
_SCHEMA_PENDING_ERROR: Final = "cannot initialize schema while a transaction is pending"
_ROW_OPTIONAL_STR_ERROR: Final = "expected SQLite column to be str or None"
_ROW_INT_ERROR: Final = "expected SQLite column to be int"
_METADATA_OBJECT_ERROR: Final = "expected metadata to be an object"
_METADATA_STRING_ERROR: Final = "expected metadata keys and values to be strings"
_VALUE_ERROR: Final = "transaction_mode must be 'operation' or 'external'"
_IMMEDIATE_PENDING_ERROR: Final = (
    "immediate_transaction requires a connection that is not already in a transaction"
)
_INSERT_IDENTITY_SQL: Final = (
    "INSERT INTO um_external_identities (provider, subject, user_id) VALUES (?, ?, ?)"
)
_SELECT_IDENTITY_USER_SQL: Final = (
    "SELECT user_id FROM um_external_identities WHERE provider = ? AND subject = ?"
)
_INSERT_USER_SQL: Final = (
    "INSERT INTO um_users (user_id, username, first_name, last_name, display_name, "
    "email, birth_date, gender, disabled, status, system, scope_type, scope_id) VALUES "
    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_UPDATE_USER_SQL: Final = (
    "UPDATE um_users SET username=?, first_name=?, last_name=?, display_name=?, "
    "email=?, birth_date=?, gender=?, disabled=?, status=?, system=?, "
    "scope_type=?, scope_id=? "
    "WHERE user_id=?"
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


class ImmediateTransaction:
    """Begin an exclusive SQLite transaction for check-and-mutate invariants.

    Pair this with stores constructed using ``transaction_mode="external"`` so
    last-admin disable/revoke checks and the resulting write commit atomically.

    Implemented as a class (not ``@contextmanager``) so frozen exception
    dataclasses can propagate out of the ``with`` block unchanged.
    """

    __slots__: ClassVar[tuple[str, ...]] = ("_conn",)
    _conn: sqlite3.Connection

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the connection that will own the immediate transaction."""
        self._conn = conn

    def __enter__(self) -> Self:
        """Start ``BEGIN IMMEDIATE`` or fail if a transaction is already open."""
        if self._conn.in_transaction:
            raise RuntimeError(_IMMEDIATE_PENDING_ERROR)
        _ = self._conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        """Roll back on failure; commit on success."""
        if exc_type is not None:
            self._conn.rollback()
        else:
            self._conn.commit()
        return False


immediate_transaction = ImmediateTransaction


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


def _user_write_params(user: User) -> tuple[object, ...]:
    return (
        user.user_id,
        user.username,
        user.first_name,
        user.last_name,
        user.display_name,
        user.email,
        None if user.birth_date is None else user.birth_date.isoformat(),
        user.gender,
        int(user.disabled),
        user.status,
        int(user.system),
        user.scope.scope_type,
        user.scope.scope_id,
    )


def _user_from_row(
    row: _CompatRow,
    identities: frozenset[ExternalIdentity],
) -> User:
    birth_raw = _row_optional_str(row, "birth_date")
    birth_date = date.fromisoformat(birth_raw) if birth_raw else None
    gender_raw = _row_optional_str(row, "gender")
    gender: Gender | None = validate_gender(gender_raw) if gender_raw else None
    return User(
        user_id=_row_str(row, "user_id"),
        username=_row_str(row, "username"),
        external_identities=identities,
        first_name=_row_optional_str(row, "first_name"),
        last_name=_row_optional_str(row, "last_name"),
        display_name=_row_optional_str(row, "display_name"),
        email=_row_optional_str(row, "email"),
        birth_date=birth_date,
        gender=gender,
        disabled=bool(_row_int(row, "disabled")),
        status=cast("AccountStatus", _row_str(row, "status")),
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
    if query.status is not None:
        clauses.append("status = ?")
        params.append(query.status)
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


def _raise_user_not_found(user_id: str) -> None:
    raise UserNotFoundError(user_id)


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
                    _user_write_params(user),
                )
                _save_identities(self._conn, user.user_id, user.external_identities)
        except ExternalIdentityConflictError:
            raise
        except sqlite3.IntegrityError as err:
            message = str(err).casefold()
            # Re-creating an existing user_id is always DuplicateUserError even when
            # SQLite reports the username unique index first.
            existing = self.get(user.user_id)
            if existing is not None:
                raise DuplicateUserError(user.user_id) from err
            if "username" in message:
                raise DuplicateUsernameError(user.username) from err
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

    def get_by_username(self, username: str) -> User | None:
        """Return a user by case-insensitive username or None when missing."""
        checked = validate_identifier(username, field_name="username")
        row = _fetchone_row(
            self._conn.execute(
                "SELECT * FROM um_users WHERE lower(username) = lower(?)",
                (checked,),
            )
        )
        if row is None:
            return None
        user_id = _row_str(row, "user_id")
        return _user_from_row(row, _load_identities(self._conn, user_id))

    def update(self, user: User) -> User:
        """Update and return a user, raising when it does not exist."""
        try:
            with _mutation(self._conn, self._mode):
                cur = self._conn.execute(
                    _UPDATE_USER_SQL,
                    (
                        user.username,
                        user.first_name,
                        user.last_name,
                        user.display_name,
                        user.email,
                        None
                        if user.birth_date is None
                        else user.birth_date.isoformat(),
                        user.gender,
                        int(user.disabled),
                        user.status,
                        int(user.system),
                        user.scope.scope_type,
                        user.scope.scope_id,
                        user.user_id,
                    ),
                )
                if cur.rowcount == 0:
                    _raise_user_not_found(user.user_id)
                _save_identities(self._conn, user.user_id, user.external_identities)
        except UserNotFoundError:
            raise
        except sqlite3.IntegrityError as err:
            message = str(err).casefold()
            if "username" in message:
                raise DuplicateUsernameError(user.username) from err
            raise

        return user

    def delete(self, user_id: str) -> None:
        """Purge a user and its cascade-owned records after policy approval."""
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        with _mutation(self._conn, self._mode):
            cursor = self._conn.execute(
                "DELETE FROM um_users WHERE user_id = ?", (checked_user_id,)
            )
            if cursor.rowcount == 0:
                _raise_user_not_found(checked_user_id)

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
            self._conn.execute("SELECT COUNT(*) FROM um_users WHERE status = 'active'")
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
