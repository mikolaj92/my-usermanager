"""Awaitable normalization for host callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from inspect import isawaitable

from starlette.concurrency import run_in_threadpool

type MaybeAwaitable[T] = T | Awaitable[T]

execution_loop: ContextVar[asyncio.AbstractEventLoop] = ContextVar("execution_loop")


async def resolve[T](
    callback: Callable[[], MaybeAwaitable[T]],
    *,
    consume: Callable[[T], T] | None = None,
) -> T:
    """Return a callback value after awaiting it when needed."""
    loop = asyncio.get_running_loop()

    def invoke() -> MaybeAwaitable[T]:
        token = execution_loop.set(loop)
        try:
            value = callback()
            if consume is not None and not isawaitable(value):
                return consume(value)
            return value
        finally:
            execution_loop.reset(token)

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
