"""Awaitable normalization for host callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from inspect import isawaitable

from starlette.concurrency import run_in_threadpool

type MaybeAwaitable[T] = T | Awaitable[T]


async def resolve[T](
    callback: Callable[[], MaybeAwaitable[T]],
    *,
    consume: Callable[[T], T] | None = None,
) -> T:
    """Return a callback value after awaiting it when needed."""

    def invoke() -> MaybeAwaitable[T]:
        value = callback()
        if consume is not None and not isawaitable(value):
            return consume(value)
        return value

    async def complete() -> T:
        value = await run_in_threadpool(invoke)
        if not isawaitable(value):
            return value
        value = await value
        if consume is not None:
            return await run_in_threadpool(consume, value)
        return value

    task = asyncio.create_task(complete())
    cancelled = False
    while not task.done():
        try:
            _ = await asyncio.wait({task})
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not task.cancelled():
            _ = task.exception()
        raise asyncio.CancelledError
    return task.result()
