"""Awaitable normalization for host callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import cast

from starlette.concurrency import run_in_threadpool

type MaybeAwaitable[T] = T | Awaitable[T]


async def resolve[T](
    operation: Callable[[], MaybeAwaitable[T]],
    *,
    consume: Callable[[T], T] | None = None,
) -> T:
    """Return a callback value after awaiting it when needed."""

    def invoke() -> MaybeAwaitable[T]:
        value = operation()
        if not isawaitable(value) and consume is not None:
            return consume(value)
        return value

    async def execute() -> T:
        value = await run_in_threadpool(invoke)
        awaited = False
        while isawaitable(value):
            value = await value
            awaited = True
        result = cast("T", value)
        if awaited and consume is not None:
            return await run_in_threadpool(consume, result)
        return result

    work = asyncio.create_task(execute())
    cancelled: asyncio.CancelledError | None = None
    while not work.done():
        try:
            _ = await asyncio.wait((work,))
        except asyncio.CancelledError as exc:
            cancelled = exc
    result = work.result()
    if cancelled is not None:
        raise cancelled
    return result
