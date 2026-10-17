from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from inspect import iscoroutinefunction
from threading import RLock
from typing import cast

from my_usermanager.manager import UserManager

callback_executor: ContextVar[
    Callable[[Callable[[], object]], Awaitable[object]] | None
] = ContextVar("callback_executor", default=None)
_SQLITE_CALLBACK_LOCK = RLock()


def _is_async(callback: Callable[..., object]) -> bool:
    return iscoroutinefunction(callback) or iscoroutinefunction(callback.__call__)


async def _execute[T](callback: Callable[..., object], operation: Callable[[], T]) -> T:
    owner: object = getattr(callback, "__self__", callback)
    executor = cast(
        "Callable[[Callable[[], T]], Awaitable[T]] | None",
        getattr(owner, "callback_executor", None) or callback_executor.get(),
    )
    if executor is not None:
        return await executor(operation)
    manager: object = getattr(owner, "manager", None)
    connections: list[sqlite3.Connection] = []
    if isinstance(manager, UserManager):
        for store in (manager.users, manager.roles, manager.grants):
            connection: object = getattr(store, "_conn", None)
            if isinstance(connection, sqlite3.Connection):
                connections.append(connection)

    def guarded() -> T:
        with _SQLITE_CALLBACK_LOCK:
            for connection in connections:
                try:
                    connection.cursor().close()
                except sqlite3.ProgrammingError as exc:
                    message = "SQLite hooks require callback_executor for their owner"
                    raise RuntimeError(message) from exc
            return operation()

    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(guarded if connections else operation)


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
