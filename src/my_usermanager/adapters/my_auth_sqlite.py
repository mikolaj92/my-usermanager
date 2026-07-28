"""Canonical shared SQLite stack for my-auth and my-usermanager."""

from __future__ import annotations

import importlib
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, cast

from my_usermanager.adapters.sqlite import (
    SQLiteGrantStore,
    SQLiteRoleStore,
    SQLiteUserStore,
    create_tables,
    inspect_sqlite_schema,
    migrate_sqlite_schema,
)
from my_usermanager.stores import DuplicateGrantError, DuplicateUserError

if TYPE_CHECKING:
    from collections.abc import Generator

    from my_usermanager.models import ExternalIdentity, Grant, Permission, User


_MEMORY_PATH_ERROR: Final = "path-mode :memory: is unsupported; pass a connection"
_MY_AUTH_ERROR: Final = "my-auth is required; install my-usermanager[myauth]"
_UNSUPPORTED_SCHEMA_ERROR: Final = "unsupported SQLite schema"
_MISSING_REGISTRATION_USER_ERROR: Final = "completed registration user is missing"
_SCHEMA_PENDING_ERROR: Final = "cannot initialize schema while a transaction is pending"
_FOREIGN_KEYS_ERROR: Final = "cannot enable SQLite foreign keys"


class _SchemaInspection(Protocol):
    state: Literal["empty", "canonical_unversioned", "legacy", "current", "unsupported"]


class _SchemaModule(Protocol):
    def inspect_sqlite_schema(
        self, connection: sqlite3.Connection
    ) -> _SchemaInspection: ...

    def ensure_sqlite_schema(
        self,
        connection: sqlite3.Connection,
        *,
        transaction_mode: Literal["external"],
    ) -> object: ...

    def migrate_sqlite_schema(
        self,
        connection: sqlite3.Connection,
        *,
        transaction_mode: Literal["external"],
    ) -> object: ...


class _CredentialStore(Protocol):
    def save_registration(self, result: object) -> None: ...


class _CredentialStoreFactory(Protocol):
    def __call__(
        self,
        database: sqlite3.Connection,
        *,
        transaction_mode: Literal["external"],
    ) -> _CredentialStore: ...


__all__ = ("SQLiteAuthDatabase", "SQLiteAuthTransaction")


class SQLiteAuthTransaction:
    """Stores bound to one connection with an explicit transaction mode."""

    __slots__: ClassVar[tuple[str, ...]] = (
        "_conn",
        "_connections",
        "_owns_connection",
        "grants",
        "roles",
        "users",
    )
    _conn: sqlite3.Connection
    _connections: tuple[sqlite3.Connection, ...]
    _owns_connection: bool
    grants: SQLiteGrantStore
    roles: SQLiteRoleStore
    users: SQLiteUserStore

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        transaction_mode: str = "external",
        owns_connection: bool = False,
        grants_conn: sqlite3.Connection | None = None,
    ) -> None:
        """Bind stores to one or independent connections."""
        self._conn = conn
        self._owns_connection = owns_connection
        grant_conn = conn if grants_conn is None else grants_conn
        self._connections = (conn,) if grant_conn is conn else (conn, grant_conn)
        self.users = SQLiteUserStore(conn, transaction_mode=transaction_mode)
        self.roles = SQLiteRoleStore()
        self.grants = SQLiteGrantStore(grant_conn, transaction_mode=transaction_mode)

    def close(self) -> None:
        """Close private connections owned by this transaction, if any."""
        if self._owns_connection:
            for conn in self._connections:
                conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the connection shared by extension writes in this transaction."""
        return self._conn

    def external_store(self, factory: _CredentialStoreFactory) -> _CredentialStore:
        """Construct an external store on this transaction's private connection."""
        return factory(self._conn, transaction_mode="external")


class SQLiteAuthDatabase:
    """Own canonical passkey/UM SQLite configuration and transaction boundaries."""

    __slots__: ClassVar[tuple[str, ...]] = ("database",)
    database: str | Path | sqlite3.Connection

    def __init__(self, database: str | Path | sqlite3.Connection) -> None:
        """Configure a shared SQLite database path or connection."""
        if isinstance(database, sqlite3.Connection):
            self.database = database
        else:
            self.database = Path(database)
            if str(self.database) == ":memory:":
                raise ValueError(_MEMORY_PATH_ERROR)

    def _connect(self) -> sqlite3.Connection:
        if isinstance(self.database, sqlite3.Connection):
            return self.database
        conn = sqlite3.connect(self.database, timeout=30, check_same_thread=False)
        _ = conn.execute("PRAGMA busy_timeout=30000")
        _ = conn.execute("PRAGMA foreign_keys=ON")
        _ = conn.execute("PRAGMA journal_mode=WAL")
        _ = conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def initialize(self) -> None:
        """Bootstrap or migrate both schemas in one owned transaction."""
        conn = self._connect()
        try:
            if conn.in_transaction:
                raise RuntimeError(_SCHEMA_PENDING_ERROR)
            _ = conn.execute("PRAGMA foreign_keys=ON")
            fk_enabled = cast(
                "tuple[int] | None", conn.execute("PRAGMA foreign_keys").fetchone()
            )
            if fk_enabled is None or fk_enabled[0] != 1:
                raise RuntimeError(_FOREIGN_KEYS_ERROR)
            auth_schema = cast(
                "_SchemaModule",
                cast("object", importlib.import_module("my_auth.sqlite_schema")),
            )
            _ = conn.execute("BEGIN IMMEDIATE")
            try:
                auth_state = auth_schema.inspect_sqlite_schema(conn)
                um_state = inspect_sqlite_schema(conn)
                if um_state == "unsupported" or auth_state.state == "unsupported":
                    message = _UNSUPPORTED_SCHEMA_ERROR
                    raise RuntimeError(message)  # noqa: TRY301
                if um_state == "empty":
                    create_tables(conn, transaction_mode="external")
                elif um_state in {"canonical_unversioned", "v2"}:
                    # v2 → v3: username NOT NULL + unique + birth_date/gender.
                    migrate_sqlite_schema(conn, transaction_mode="external")
                if auth_state.state in {"empty", "canonical_unversioned"}:
                    _ = auth_schema.ensure_sqlite_schema(
                        conn, transaction_mode="external"
                    )
                elif auth_state.state == "legacy":
                    _ = auth_schema.migrate_sqlite_schema(
                        conn, transaction_mode="external"
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            if not isinstance(self.database, sqlite3.Connection):
                conn.close()

    def stores(self) -> SQLiteAuthTransaction:
        """Return operation-mode stores on the configured database."""
        if isinstance(self.database, sqlite3.Connection):
            if self.database.in_transaction:
                raise RuntimeError(_SCHEMA_PENDING_ERROR)
            return SQLiteAuthTransaction(self._connect(), transaction_mode="operation")
        conn = self._connect()
        grants_conn = self._connect()
        return SQLiteAuthTransaction(
            conn,
            transaction_mode="operation",
            owns_connection=True,
            grants_conn=grants_conn,
        )

    @contextmanager
    def transaction(self) -> Generator[SQLiteAuthTransaction, None, None]:
        """Yield transaction-bound stores and atomically commit or roll back."""
        conn = self._connect()
        if conn.in_transaction:
            raise RuntimeError(_SCHEMA_PENDING_ERROR)
        try:
            _ = conn.execute("BEGIN IMMEDIATE")
            tx = SQLiteAuthTransaction(conn)
            yield tx
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            if not isinstance(self.database, sqlite3.Connection):
                conn.close()

    def complete_registration(
        self,
        request: object,
        result: object,
        *,
        user: User,
        identity: ExternalIdentity,
        grants: tuple[Grant, ...] = (),
    ) -> User:
        """Atomically persist verified passkey, UM user, identity, and grants."""
        del request
        with self.transaction() as tx:
            self._auth_store(tx).save_registration(result)
            try:
                _ = tx.users.create(user)
            except DuplicateUserError:
                existing = tx.users.get(user.user_id)
                if existing is None or existing != replace(
                    user, external_identities=existing.external_identities
                ):
                    raise
            _ = tx.users.link_external_identity(user_id=user.user_id, identity=identity)
            for grant in grants:
                try:
                    if grant.role_name is not None:
                        _ = tx.grants.add_role_grant(
                            grant.user_id, grant.role_name, grant.scope
                        )
                    else:
                        _ = tx.grants.add_permission_grant(
                            grant.user_id,
                            cast("Permission", grant.permission),
                            grant.scope,
                        )
                except DuplicateGrantError:
                    if grant not in tx.grants.list_grants_for_user(grant.user_id):
                        raise
            completed = tx.users.get(user.user_id)
            if completed is None:
                raise RuntimeError(_MISSING_REGISTRATION_USER_ERROR)
            return completed

    @staticmethod
    def _auth_store(tx: SQLiteAuthTransaction) -> _CredentialStore:
        """Construct my-auth SQLite stores on the same connection."""
        try:
            auth_passkeys = importlib.import_module("my_auth.passkeys")
        except ModuleNotFoundError as exc:
            if exc.name == "my_auth" or (exc.name and exc.name.startswith("my_auth.")):
                raise ModuleNotFoundError(_MY_AUTH_ERROR) from exc
            raise
        store_type = cast(
            "_CredentialStoreFactory",
            auth_passkeys.SQLiteCredentialStore,
        )
        return tx.external_store(store_type)
