from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import iscoroutinefunction
from typing import cast


async def dispatch[**P, T](
    callback: Callable[P, T | Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    if iscoroutinefunction(callback) or iscoroutinefunction(callback.__call__):
        result = callback(*args, **kwargs)
    else:
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(callback, *args, **kwargs)
    if isinstance(result, Awaitable):
        return await cast("Awaitable[T]", result)
    return result
