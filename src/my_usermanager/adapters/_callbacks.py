from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from inspect import iscoroutinefunction
from typing import cast

callback_executor: ContextVar[
    Callable[[Callable[[], object]], Awaitable[object]] | None
] = ContextVar("callback_executor", default=None)


def _is_async(callback: Callable[..., object]) -> bool:
    return iscoroutinefunction(callback) or iscoroutinefunction(callback.__call__)


async def _execute[T](callback: Callable[..., object], operation: Callable[[], T]) -> T:
    owner: object = getattr(callback, "__self__", callback)
    executor = cast(
        "Callable[[Callable[[], T]], Awaitable[T]] | None",
        getattr(owner, "callback_executor", None) or callback_executor.get(),
    )
    from starlette.concurrency import run_in_threadpool

    return await wait_for_owner((executor or run_in_threadpool)(operation))


async def wait_for_owner[T](work: Awaitable[T]) -> T:
    from anyio import CancelScope

    with CancelScope(shield=True):
        try:
            _ = asyncio.get_running_loop()
        except RuntimeError:
            return await work
        task = asyncio.ensure_future(work)
        cancelled: asyncio.CancelledError | None = None
        while not task.done():
            try:
                _ = await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if task.cancelled():
                    raise
                cancelled = exc
        result = task.result()
        if cancelled is not None:
            raise cancelled
        return result
    message = "owner wait was interrupted"
    raise RuntimeError(message)


async def dispatch[**P, T](
    callback: Callable[P, T | Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    if _is_async(callback):
        result = callback(*args, **kwargs)
    else:
        result = await _execute(callback, lambda: callback(*args, **kwargs))
    if isinstance(result, Awaitable):
        return await cast("Awaitable[T]", result)
    return result


async def dispatch_materialized[**P, T, R](
    callback: Callable[P, T | Awaitable[T]],
    materialize: Callable[[T], R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    if _is_async(callback):
        return materialize(await dispatch(callback, *args, **kwargs))

    def operation() -> R | Awaitable[T]:
        result = callback(*args, **kwargs)
        if isinstance(result, Awaitable):
            return cast("Awaitable[T]", result)
        return materialize(result)

    result = await _execute(callback, operation)
    if isinstance(result, Awaitable):
        return materialize(await cast("Awaitable[T]", result))
    return result


async def dispatch_tuple[**P, T](
    callback: Callable[P, Iterable[T] | Awaitable[Iterable[T]]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> tuple[T, ...]:
    return await dispatch_materialized(callback, tuple, *args, **kwargs)
