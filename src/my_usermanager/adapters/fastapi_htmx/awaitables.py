"""Awaitable normalization for host callbacks."""

from __future__ import annotations

from collections.abc import Awaitable
from inspect import isawaitable

type MaybeAwaitable[T] = T | Awaitable[T]


async def resolve[T](value: MaybeAwaitable[T]) -> T:
    """Return a callback value after awaiting it when needed."""
    if isawaitable(value):
        return await value
    return value
