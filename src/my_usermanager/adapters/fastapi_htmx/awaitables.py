"""Awaitable normalization for host callbacks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable, iscoroutinefunction

from starlette.concurrency import run_in_threadpool

type MaybeAwaitable[T] = T | Awaitable[T]


async def resolve[T](
    callback: Callable[..., MaybeAwaitable[T]],
    *args: object,
    consume: Callable[[T], T] | None = None,
    **kwargs: object,
) -> T:
    """Return a callback value after awaiting it when needed."""

    def invoke() -> MaybeAwaitable[T]:
        value = callback(*args, **kwargs)
        if not isawaitable(value) and consume is not None:
            return consume(value)
        return value

    value = (
        callback(*args, **kwargs)
        if iscoroutinefunction(callback)
        else await run_in_threadpool(invoke)
    )
    if isawaitable(value):
        result = await value
        if consume is not None:
            return await run_in_threadpool(consume, result)
        return result
    return value
