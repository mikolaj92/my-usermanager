# pyright: reportAny=false, reportPrivateUsage=false, reportUnusedCallResult=false
# ruff: noqa: D107
"""Durable SQLite store for OIDC authorization-code flow secrets.

State, nonce, and the PKCE verifier live in host SQLite. The browser only
holds an opaque httponly binding cookie; its SHA-256 digest is stored, never
the raw cookie value. Importing table DDL must not pull Authlib/joserfc.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING, ClassVar, Final, Literal, cast, final

from my_usermanager.adapters.sqlite_stores import _mutation

if TYPE_CHECKING:
    import sqlite3

    from my_usermanager.adapters.oidc import OidcAuthorizationFlow

__all__: Final[tuple[str, ...]] = (
    "SQLiteOidcFlowStore",
    "create_oidc_flow_tables",
)

_CREATE_SQL: Final = """
CREATE TABLE IF NOT EXISTS um_oidc_flows (
    state TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    verifier TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS um_oidc_flows_expires_at ON um_oidc_flows(expires_at);
"""
_TRANSACTION_MODE_ERROR: Final = "transaction_mode must be 'standalone' or 'external'"
_STORE_MODE_ERROR: Final = "transaction_mode must be 'operation' or 'external'"
_SCHEMA_PENDING_ERROR: Final = "cannot initialize schema while a transaction is pending"
_FOREIGN_KEYS_ERROR: Final = (
    "cannot initialize schema without SQLite foreign keys enabled"
)
_TTL_ERROR: Final = "ttl_seconds must be positive"
_BINDING_ERROR: Final = "browser binding is required"
_UNAVAILABLE: Final = "authentication unavailable"
_DEFAULT_TTL_SECONDS: Final = 300.0


def _prepare_schema_transaction(
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


def create_oidc_flow_tables(
    connection: sqlite3.Connection,
    *,
    transaction_mode: Literal["standalone", "external"] = "standalone",
) -> None:
    """Create durable OIDC flow storage without a raw browser-binding column."""
    if transaction_mode not in {"standalone", "external"}:
        raise ValueError(_TRANSACTION_MODE_ERROR)
    _prepare_schema_transaction(connection, transaction_mode=transaction_mode)
    if transaction_mode == "standalone":
        _ = connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in (
            part.strip() for part in _CREATE_SQL.split(";") if part.strip()
        ):
            _ = connection.execute(statement)
        if transaction_mode == "standalone":
            connection.commit()
    except BaseException:
        if transaction_mode == "standalone":
            connection.rollback()
        raise


def _binding_hash(binding: str) -> str:
    return hashlib.sha256(binding.encode("utf-8")).hexdigest()


def _authorization_flow() -> type[OidcAuthorizationFlow]:
    from my_usermanager.adapters.oidc import OidcAuthorizationFlow  # noqa: PLC0415

    return OidcAuthorizationFlow


@final
class SQLiteOidcFlowStore:
    """SQLite ``OidcFlowStore`` bound to one browser cookie digest."""

    __slots__: ClassVar[tuple[str, ...]] = ("_connection", "_mode", "_ttl_seconds")
    _connection: sqlite3.Connection
    _ttl_seconds: float
    _mode: str

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        transaction_mode: str = "operation",
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(_TTL_ERROR)
        if transaction_mode not in {"operation", "external"}:
            raise ValueError(_STORE_MODE_ERROR)
        self._connection = connection
        self._ttl_seconds = ttl_seconds
        self._mode = transaction_mode

    def start(self, *, now: float, binding: str = "") -> OidcAuthorizationFlow:
        """Issue a one-time flow bound to a non-empty browser cookie."""
        if not binding:
            raise ValueError(_BINDING_ERROR)
        flow = _authorization_flow()(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            verifier=secrets.token_urlsafe(48),
        )
        with _mutation(self._connection, self._mode):
            _ = self._connection.execute(
                "DELETE FROM um_oidc_flows WHERE expires_at <= ?",
                (now,),
            )
            _ = self._connection.execute(
                """INSERT INTO um_oidc_flows
                (state, nonce, verifier, binding_hash, expires_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    flow.state,
                    flow.nonce,
                    flow.verifier,
                    _binding_hash(binding),
                    now + self._ttl_seconds,
                ),
            )
        return flow

    def consume(
        self,
        state: str,
        *,
        now: float,
        binding: str = "",
    ) -> OidcAuthorizationFlow:
        """Return and delete a still-valid flow for this browser, or fail closed."""
        if not binding or not state:
            raise PermissionError(_UNAVAILABLE)
        fetched: object | None = None
        with _mutation(self._connection, self._mode):
            _ = self._connection.execute(
                "DELETE FROM um_oidc_flows WHERE expires_at <= ?",
                (now,),
            )
            fetched = self._connection.execute(
                """DELETE FROM um_oidc_flows
                WHERE state = ? AND binding_hash = ? AND expires_at > ?
                RETURNING nonce, verifier""",
                (state, _binding_hash(binding), now),
            ).fetchone()
        row = cast("tuple[str, str] | None", fetched)
        if row is None:
            raise PermissionError(_UNAVAILABLE)
        nonce, verifier = row
        return _authorization_flow()(state=state, nonce=nonce, verifier=verifier)
