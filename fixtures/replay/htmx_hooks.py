import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from my_usermanager.adapters.fastapi_htmx.awaitables import resolve


class OwnerHooks:
    def __init__(self, pool):
        self.pool = pool
        self.connection = pool.submit(sqlite3.connect, ":memory:").result()
        pool.submit(self.connection.execute, "CREATE TABLE events (value)").result()
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.owner = pool.submit(threading.get_ident).result()

    async def executor(self, operation):
        return await asyncio.wrap_future(self.pool.submit(operation))

    def read(self):
        self.calls += 1
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError
        return self.rows()

    def rows(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("lazy result left its owner")
        yield from self.connection.execute("SELECT value FROM events")

    def fail(self):
        self.calls += 1
        with self.connection:
            self.connection.execute("INSERT INTO events VALUES (1)")
            raise TypeError("callback failed")


async def replay():
    with ThreadPoolExecutor(max_workers=1) as pool:
        hooks = OwnerHooks(pool)
        try:
            task = asyncio.create_task(resolve(hooks.read))
            try:
                if not await asyncio.to_thread(hooks.started.wait, 5):
                    raise TimeoutError("store did not start")
                await asyncio.sleep(0)
                if task.done():
                    raise RuntimeError("blocking store did not wait")
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                if task.done():
                    raise RuntimeError("owner cleanup was abandoned")
            finally:
                hooks.release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise RuntimeError("cancellation was lost")
            if hooks.calls != 1:
                raise RuntimeError("callback repeated")
            try:
                await resolve(hooks.fail)
            except TypeError:
                pass
            else:
                raise RuntimeError("callback error was lost")
            if hooks.calls != 2 or await resolve(hooks.read) != ():
                raise RuntimeError("rollback or exactly-once execution failed")
            if hooks.calls != 3:
                raise RuntimeError("read callback repeated")
        finally:
            await hooks.executor(partial(hooks.connection.close))


asyncio.run(replay())
