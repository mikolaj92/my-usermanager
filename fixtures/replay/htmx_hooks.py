import asyncio
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Never

from starlette.requests import Request

from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.fastapi_htmx.config import AuditPage, AuditRow
from my_usermanager.adapters.fastapi_htmx.page import PageQuery, load_audit_page
from my_usermanager.stores import AuditFilters
from my_usermanager.subjects import AuthenticatedSubject


async def replay(database: Path) -> None:
    request = Request({"type": "http"})
    subject = AuthenticatedSubject(provider="local", subject="admin", user_id="admin")
    main_thread = threading.get_ident()
    row = AuditRow("now", "admin", "read", "user", "target", "ok")
    workers = []
    calls = []

    class LazyRows(Sequence):
        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int) -> AuditRow:
            assert threading.get_ident() != main_thread
            workers.append(threading.get_ident())
            if index == 0:
                return row
            raise IndexError(index)

    expected = AuditPage((), 7, 14, has_previous=True, has_next=True, filtered=True)

    def listing(*_args: object, **_kwargs: object) -> AuditPage:
        workers.append(threading.get_ident())
        return replace(expected, items=LazyRows())

    async def async_listing(*args: object, **kwargs: object) -> AuditPage:
        await asyncio.sleep(0)
        calls.append("async")
        return listing(*args, **kwargs)

    for callback in (listing, async_listing):
        workers.clear()
        page = await load_audit_page(
            callback,
            request,
            subject,
            page=PageQuery(7, 14, 3, {}),
            filters=AuditFilters(),
        )
        assert page == replace(expected, items=(row,))
        assert len(set(workers if callback is listing else workers[1:])) == 1
    assert calls == ["async"]

    def broken(*_args: object, **_kwargs: object) -> Never:
        calls.append("TypeError")
        message = "callback failure"
        raise TypeError(message)

    try:
        await load_audit_page(
            broken,
            request,
            subject,
            page=PageQuery(7, 0, 1, {}),
            filters=AuditFilters(),
        )
    except TypeError:
        pass
    else:
        message = "failure was lost"
        raise AssertionError(message)
    assert calls.count("TypeError") == 1

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE changes (value TEXT)")
    await cancellation(database, fail=False)
    await cancellation(database, fail=True)


async def cancellation(database: Path, *, fail: bool) -> None:
    main_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    events = []

    def transaction() -> None:
        connection = sqlite3.connect(database)
        try:
            with connection:
                connection.execute("INSERT INTO changes VALUES ('accepted')")
                events.append(("accepted", threading.get_ident()))
                started.set()
                assert release.wait(5), "worker release timed out"
                if fail:
                    rollback(connection)
            events.append(("commit", threading.get_ident()))
        except ValueError:
            events.append(("rollback", threading.get_ident()))
            raise
        finally:
            connection.close()
            events.append(("cleanup", threading.get_ident()))

    async def progress() -> None:
        assert await asyncio.to_thread(started.wait, 5), "worker did not start"
        await asyncio.sleep(0)
        assert not release.is_set()

    task = asyncio.create_task(resolve(transaction))
    try:
        await asyncio.wait_for(asyncio.create_task(progress()), 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "cleanup overtook accepted work"
        assert [name for name, _ in events] == ["accepted"]
    finally:
        release.set()
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.CancelledError:
            assert not fail
        except ValueError:
            assert fail
    assert [name for name, _ in events] == [
        "accepted",
        "rollback" if fail else "commit",
        "cleanup",
    ]
    assert len({worker for _, worker in events}) == 1
    assert events[0][1] != main_thread
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM changes").fetchone() == (1,)


def rollback(connection: sqlite3.Connection) -> Never:
    assert connection.in_transaction
    message = "rollback"
    raise ValueError(message)


with TemporaryDirectory() as directory, asyncio.Runner() as runner:
    runner.run(replay(Path(directory) / "hooks.db"))
