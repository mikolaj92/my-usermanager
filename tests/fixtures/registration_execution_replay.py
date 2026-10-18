from __future__ import annotations

# ruff: noqa: INP001
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, override
from unittest.mock import patch

import pytest
from my_auth.passkeys import (  # pyright: ignore[reportMissingTypeStubs]
    PasskeyCredential,
    PasskeyUser,
    SQLiteCredentialStore,
    VerifiedRegistration,
)

from my_usermanager import ExternalIdentity, User
from my_usermanager.adapters.my_auth_fastapi import build_complete_registration
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import SQLiteUserStore

if TYPE_CHECKING:
    from collections.abc import Callable


async def callbacks() -> None:
    calls: list[str] = []

    async def asynchronous(_request: object, _result: object) -> str:
        calls.append("async")
        await asyncio.sleep(0)
        return "ready"

    def returning_awaitable(request: object, result: object) -> object:
        calls.append("factory")
        return asynchronous(request, result)

    class Completion:
        async def __call__(self, request: object, result: object) -> None:
            calls.append("object")
            await asyncio.sleep(0)

    assert await build_complete_registration(asynchronous)(None, None) == "ready"
    assert await build_complete_registration(returning_awaitable)(None, None) == "ready"
    assert await build_complete_registration(Completion())(None, None) is None
    assert calls == ["async", "factory", "async", "object"]


async def registration(  # noqa: C901, PLR0915
    path: Path, *, cancel: bool, fail: bool
) -> None:
    database = SQLiteAuthDatabase(path)
    database.initialize()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    release = threading.Event()
    events: list[tuple[str, int]] = []
    calls = 0
    original_connect = sqlite3.connect
    original_create = SQLiteUserStore.create

    def record(event: str) -> None:
        events.append((event, threading.get_ident()))

    class Connection(sqlite3.Connection):
        @override
        def commit(self) -> None:
            record("commit")
            super().commit()

        @override
        def rollback(self) -> None:
            record("rollback")
            super().rollback()

        @override
        def close(self) -> None:
            record("close")
            super().close()

    def connect(database: str | Path, *, timeout: float = 5) -> Connection:
        record("open")
        return original_connect(database, timeout=timeout, factory=Connection)

    def create(store: SQLiteUserStore, user: User) -> User:
        record("store")
        _ = loop.call_soon_threadsafe(entered.set)
        if not release.wait(10):
            message = "replay did not release store"
            raise TimeoutError(message)
        created = original_create(store, user)
        if fail:
            message = "partial registration"
            raise RuntimeError(message)
        return created

    verified = VerifiedRegistration(
        PasskeyUser("alice", b"alice-handle", "alice"),
        PasskeyCredential(b"alice-credential", "alice", b"public-key"),
    )
    identity = ExternalIdentity(provider="my-auth", subject="alice")

    def complete(request: object, result: object) -> User:
        nonlocal calls
        calls += 1
        user = database.complete_registration(
            request, result, user=User("alice", "alice"), identity=identity
        )
        record("materialized")
        assert user.external_identities == frozenset({identity})
        return user

    with (
        patch("sqlite3.connect", connect),
        patch.object(SQLiteUserStore, "create", create),
    ):
        task = asyncio.ensure_future(
            build_complete_registration(complete)(None, verified)
        )
        try:
            async with asyncio.timeout(5):
                _ = await entered.wait()
            assert not task.done()
            if cancel:
                _ = task.cancel()
                await asyncio.sleep(0)
                _ = task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
        finally:
            release.set()
        if fail:
            with pytest.raises(RuntimeError, match="partial registration") as caught:
                _ = await asyncio.wait_for(task, 5)
            assert isinstance(caught.value.__cause__, asyncio.CancelledError) == cancel
        elif cancel:
            with pytest.raises(asyncio.CancelledError):
                _ = await asyncio.wait_for(task, 5)
        else:
            user = await asyncio.wait_for(task, 5)
            assert user is not None
            assert user.external_identities == frozenset({identity})

    assert calls == 1
    assert {thread for _, thread in events} != {threading.get_ident()}
    assert len({thread for _, thread in events}) == 1
    names = [name for name, _ in events]
    assert names == (
        ["open", "store", "rollback", "close"]
        if fail
        else ["open", "store", "commit", "close", "materialized"]
    )
    with original_connect(path) as connection:
        assert (SQLiteUserStore(connection).get("alice") is None) == fail
        credentials = SQLiteCredentialStore(connection)
        assert (credentials.get_credential(b"alice-credential") is None) == fail


async def borrowed() -> None:
    with ThreadPoolExecutor(max_workers=1) as owner:

        async def execute[T](operation: Callable[[], T]) -> T:
            return await asyncio.wrap_future(owner.submit(operation))

        connection = await execute(lambda: sqlite3.connect(":memory:"))
        database = SQLiteAuthDatabase(connection)
        try:
            await execute(database.initialize)

            def complete(_request: object, _result: object) -> User:
                with database.transaction() as transaction:
                    return transaction.users.create(User("owner", "owner"))

            result = await build_complete_registration(complete, executor=execute)(
                None, None
            )
            assert result == User("owner", "owner")
            with pytest.raises(sqlite3.ProgrammingError):
                _ = connection.execute("SELECT 1")

            def pending() -> None:
                _ = connection.execute("BEGIN")
                _ = connection.execute("CREATE TABLE host_pending (value TEXT)")
                with (
                    pytest.raises(RuntimeError, match="transaction is pending"),
                    database.transaction(),
                ):
                    pytest.fail("pending host transaction was accepted")
                assert connection.in_transaction
                _ = connection.execute("INSERT INTO host_pending VALUES ('retained')")
                connection.commit()
                assert connection.execute("SELECT * FROM host_pending").fetchall() == [
                    ("retained",)
                ]

            await execute(pending)
        finally:
            await execute(connection.close)


async def main() -> None:
    await callbacks()
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        for cancel in (False, True):
            for fail in (False, True):
                await registration(
                    Path(directory) / f"registration-{cancel}-{fail}.sqlite3",
                    cancel=cancel,
                    fail=fail,
                )
    await borrowed()


if __name__ == "__main__":
    asyncio.run(main())
