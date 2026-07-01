"""Awaitable normalization for host callbacks."""

from __future__ import annotations

from inspect import isawaitable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_usermanager.adapters.fastapi_htmx.protocols import MaybeAwaitable


async def resolve[T](value: MaybeAwaitable[T]) -> T:
    """Return a callback value after awaiting it when needed."""
    if isawaitable(value):
        return await value
    return value
