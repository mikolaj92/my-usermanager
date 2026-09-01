"""Stable SQLite adapter facade.

Schema lifecycle and store CRUD live in single-purpose modules while existing
``my_usermanager.adapters.sqlite`` imports remain source compatible.
"""

from __future__ import annotations

from typing import Final

from my_usermanager.adapters.sqlite_schema import (
    create_tables,
    inspect_sqlite_schema,
    migrate_sqlite_schema,
)
from my_usermanager.adapters.sqlite_stores import (
    ImmediateTransaction,
    SQLiteAuditStore,
    SQLiteGrantStore,
    SQLiteUserStore,
    immediate_transaction,
)

__all__: Final[tuple[str, ...]] = (
    "ImmediateTransaction",
    "SQLiteAuditStore",
    "SQLiteGrantStore",
    "SQLiteUserStore",
    "create_tables",
    "immediate_transaction",
    "inspect_sqlite_schema",
    "migrate_sqlite_schema",
)
