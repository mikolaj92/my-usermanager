set -eu

uv run python - <<'PY'
import asyncio
import sqlite3
import threading

from my_usermanager.adapters._callbacks import dispatch_tuple


async def main():
    loop_thread = threading.get_ident()
    release = threading.Event()
    owners = []
    connections = []
    calls = []

    def hook(fail=False):
        calls.append(fail)
        owner = threading.get_ident()
        connection = sqlite3.connect(":memory:")
        connections.append(connection)
        connection.execute("CREATE TABLE records (value INTEGER)")
        cursor = connection.execute("SELECT 1")

        def rows():
            try:
                owners.append((owner, threading.get_ident()))
                assert owner != loop_thread
                assert release.wait(1), "event loop did not progress"
                with connection:
                    connection.execute("INSERT INTO records VALUES (1)")
                    yield cursor.fetchone()[0]
                    if fail:
                        raise ValueError("rollback")
                count = connection.execute("SELECT COUNT(*) FROM records").fetchone()
                assert count == (1,)
            except ValueError:
                count = connection.execute("SELECT COUNT(*) FROM records").fetchone()
                assert count == (0,)
                raise
            finally:
                connection.close()
                try:
                    connection.execute("SELECT 1")
                except sqlite3.ProgrammingError as error:
                    assert "closed" in str(error)
                else:
                    raise AssertionError("connection was not closed")

        return rows()

    asyncio.get_running_loop().call_later(0.05, release.set)
    assert await asyncio.wait_for(dispatch_tuple(hook), 2) == (1,)
    try:
        await dispatch_tuple(hook, True)
    except ValueError as error:
        assert str(error) == "rollback"
    else:
        raise AssertionError("missing rollback failure")
    assert calls == [False, True]
    assert len(owners) == 2
    assert all(created == consumed != loop_thread for created, consumed in owners)
    assert len(connections) == 2

    async def async_hook():
        assert threading.get_ident() == loop_thread
        return (2,)

    class AsyncHook:
        async def __call__(self):
            return await async_hook()

    assert await dispatch_tuple(async_hook) == (2,)
    assert await dispatch_tuple(AsyncHook()) == (2,)
    assert await dispatch_tuple(lambda: async_hook()) == (2,)


asyncio.run(main())
PY
