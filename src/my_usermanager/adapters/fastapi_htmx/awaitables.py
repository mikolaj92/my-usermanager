"""Awaitable normalization for host callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from functools import partial
from inspect import isawaitable, iscoroutinefunction
from typing import cast

from my_usermanager.adapters.my_auth_fastapi import build_complete_registration

type MaybeAwaitable[T] = T | Awaitable[T]


async def resolve[T, **P](
    callback: Callable[P, MaybeAwaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    """Return a callback value after awaiting it when needed."""
    if iscoroutinefunction(callback) or iscoroutinefunction(
        getattr(callback, "__call__", None)
    ):
        value = callback(*args, **kwargs)
        if isawaitable(value):
            return await value
        return value
    executor = getattr(getattr(callback, "__self__", None), "executor", asyncio.to_thread)
    operation = partial(callback, *args, **kwargs)

    def invoke(_request: None, _result: None) -> MaybeAwaitable[T]:
        value = operation()
        if isinstance(value, Iterator):
            return cast("T", tuple(value))
        return value

    if executor is None:
        value = invoke(None, None)
        if isawaitable(value):
            return await value
        return value
    return cast(
        "T", await build_complete_registration(invoke, executor=executor)(None, None)
    )
