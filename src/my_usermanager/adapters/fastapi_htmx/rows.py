"""User row normalization for DOM-safe HTMX targets."""

from __future__ import annotations

from dataclasses import replace
from re import Pattern
from re import compile as compile_regex
from typing import TYPE_CHECKING, Final

from my_usermanager.adapters.fastapi_htmx.ids import row_key_from_user_id

if TYPE_CHECKING:
    from my_usermanager.adapters.fastapi_htmx.config import UserRow

_ROW_KEY_PATTERN: Final[Pattern[str]] = compile_regex(r"[A-Za-z][A-Za-z0-9_-]*")


def safe_row(row: UserRow) -> UserRow:
    """Return a row whose row_key is safe for DOM ids and selectors."""
    row_key_matches = _ROW_KEY_PATTERN.fullmatch(row.row_key) is not None
    if row_key_matches and row.row_key != row.user_id:
        return row
    return replace(row, row_key=row_key_from_user_id(row.user_id))
