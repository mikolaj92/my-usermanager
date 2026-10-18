"""Executable SQLite registration execution and ownership replay."""

# Standalone script, not an importable test package.
# ruff: noqa: INP001

from __future__ import annotations

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast, override
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from my_auth import PasskeyUser  # pyright: ignore[reportMissingTypeStubs]
from my_auth.passkeys import (  # pyright: ignore[reportMissingTypeStubs]
    PasskeyCredential,
    VerifiedRegistration,
)

from my_usermanager.adapters.my_auth_fastapi import build_complete_registration
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import SQLiteUserStore
from my_usermanager.models import ExternalIdentity, Grant, Scope, User


class ReplayError(Exception):
    pass


async def callbacks() -> None:
    calls: list[tuple[str, int]] = []
    loop_thread = threading.get_ident()

    async def asynchronous(_request: object, _result: object) -> str:
        calls.append(("async", threading.get_ident()))
        await asyncio.sleep(0)
        return "awaited"

    def synchronous(_request: object, _result: object) -> None:
        calls.append(("sync", threading.get_ident()))

    class AsyncCallable:
        async def __call__(self, request: object, result: object) -> str:
            return await asynchronous(request, result)

    def returning_awaitable(request: object, result: object) -> Awaitable[str]:
        calls.append(("factory", threading.get_ident()))
        return asynchronous(request, result)

    assert await build_complete_registration(asynchronous)(None, None) == "awaited"
    assert await build_complete_registration(synchronous)(None, None) is None
    assert await build_complete_registration(AsyncCallable())(None, None) == "awaited"
    assert (
        await build_complete_registration(returning_awaitable)(None, None) == "awaited"
    )
    assert [name for name, _ in calls] == ["async", "sync", "async", "factory", "async"]
    assert all(tid == loop_thread for name, tid in calls if name == "async")
    assert all(tid != loop_thread for name, tid in calls if name != "async")


async def registration(  # noqa: C901, PLR0915
    path: Path, *, fail: bool, cancel: bool
) -> None:
    database = SQLiteAuthDatabase(path)
    database.initialize()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    events: list[tuple[str, int]] = []
    calls = 0
    original_connect = sqlite3.connect
    original_create = SQLiteUserStore.create

    class Connection(sqlite3.Connection):
        @override
        def commit(self) -> None:
            events.append(("commit", threading.get_ident()))
            super().commit()

        @override
        def rollback(self) -> None:
            events.append(("rollback", threading.get_ident()))
            super().rollback()

        @override
        def close(self) -> None:
            events.append(("close", threading.get_ident()))
            super().close()

    def connect(
        database: str | Path, *, timeout: float = 5, check_same_thread: bool = True
    ) -> Connection:
        assert check_same_thread
        events.append(("open", threading.get_ident()))
        connection = original_connect(database, timeout=timeout, factory=Connection)
        connection.set_trace_callback(
            lambda sql: events.append((sql, threading.get_ident()))
        )
        return connection

    def create(store: SQLiteUserStore, user: User) -> User:
        events.append(("store", threading.get_ident()))
        _ = loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise TimeoutError
        created = original_create(store, user)
        if fail:
            raise ReplayError
        return created

    verified = VerifiedRegistration(
        PasskeyUser("alice", b"handle", "alice", "Alice"),
        PasskeyCredential(b"credential", "alice", b"public-key"),
    )
    identity = ExternalIdentity("my-auth", "alice")

    def complete(request: object, result: object) -> User:
        nonlocal calls
        calls += 1
        return database.complete_registration(
            request,
            result,
            user=User("alice", "alice"),
            identity=identity,
            grants=(Grant("alice", scope=Scope.global_(), role_name="member"),),
        )

    with (
        patch("sqlite3.connect", side_effect=connect),
        patch.object(SQLiteUserStore, "create", create),
    ):
        task = asyncio.ensure_future(
            build_complete_registration(complete)(None, verified)
        )
        try:
            async with asyncio.timeout(5):
                _ = await entered.wait()
                # This independent coroutine advances while the store is blocked.
                progressed = asyncio.Event()

                async def progress() -> None:
                    progressed.set()

                await asyncio.create_task(progress())
                assert progressed.is_set()
                assert not release.is_set()
                if cancel:
                    _ = task.cancel()
                    await asyncio.sleep(0)
                    _ = task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done()
                release.set()
                try:
                    completed = await task
                except ReplayError as exc:
                    assert fail
                    assert isinstance(exc.__cause__, asyncio.CancelledError) == cancel  # noqa: PT017
                except asyncio.CancelledError:
                    assert cancel
                    assert not fail
                else:
                    assert not cancel
                    assert not fail
                    assert completed is not None
                    assert completed.external_identities == frozenset({identity})
        finally:
            release.set()
            if not task.done():
                _ = await asyncio.gather(task, return_exceptions=True)

    assert calls == 1
    assert events[0][0] == "open"
    assert events[-1][0] == "close"
    assert len({tid for _, tid in events}) == 1
    assert events[0][1] != threading.get_ident()
    names = [name for name, _ in events]
    assert ("rollback" in names) == fail
    assert ("commit" in names) == (not fail)
    if not fail:
        assert any("SELECT" in name and "um_users" in name for name in names)
    with original_connect(path) as conn:
        for table in (
            "passkey_users",
            "passkey_credentials",
            "um_users",
            "um_external_identities",
            "um_grants",
        ):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            assert bool(rows) == (not fail)


async def borrowed(path: Path) -> None:
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as owner:

        async def execute[T](operation: Callable[[], T]) -> T:
            return await loop.run_in_executor(owner, operation)

        connection = await execute(lambda: sqlite3.connect(path))
        database = SQLiteAuthDatabase(connection)
        try:
            await execute(database.initialize)
            verified = VerifiedRegistration(
                PasskeyUser("bob", b"bob-handle", "bob"),
                PasskeyCredential(b"bob-credential", "bob", b"key"),
            )

            def complete(request: object, result: object) -> User:
                return database.complete_registration(
                    request,
                    result,
                    user=User("bob", "bob"),
                    identity=ExternalIdentity("my-auth", "bob"),
                )

            # The default worker must not silently disable the owner's thread check.
            try:
                _ = await build_complete_registration(complete)(None, verified)
            except sqlite3.ProgrammingError:
                pass
            else:
                raise AssertionError
            completed = await build_complete_registration(complete, executor=execute)(
                None, verified
            )
            assert completed is not None
            assert completed.user_id == "bob"

            def pending() -> None:
                _ = connection.execute("BEGIN")
                _ = connection.execute("UPDATE um_users SET display_name='host'")
                try:
                    _ = complete(None, verified)
                except RuntimeError:
                    assert connection.in_transaction
                    row = cast(
                        "tuple[str]",
                        connection.execute(
                            "SELECT display_name FROM um_users"
                        ).fetchone(),
                    )
                    assert row == ("host",)
                else:
                    raise AssertionError
                finally:
                    connection.rollback()
                assert connection.execute("SELECT 1").fetchone() == (1,)

            await execute(pending)
        finally:
            await execute(connection.close)


async def main(root: Path) -> None:
    await callbacks()
    for fail in (False, True):
        for cancel in (False, True):
            await registration(
                root / f"registration-{fail}-{cancel}.db", fail=fail, cancel=cancel
            )
    await borrowed(root / "borrowed.db")


if __name__ == "__main__":
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        asyncio.run(main(Path(directory)))
