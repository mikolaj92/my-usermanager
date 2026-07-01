"""DOM-safe identifier helpers for the user-manager UI adapter."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256
from typing import Final

_ROW_KEY_PREFIX: Final = "user_"


def row_key_from_user_id(user_id: str) -> str:
    """Return a stable DOM-safe key derived from a raw user id."""
    digest = urlsafe_b64encode(sha256(user_id.encode()).digest()).decode().rstrip("=")
    return f"{_ROW_KEY_PREFIX}{digest}"
