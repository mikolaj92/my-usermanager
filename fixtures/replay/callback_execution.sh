set -eu

uv run python - <<'PY'
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path
import sqlite3
import tempfile
import threading

from fastapi import Request
from my_auth.passkeys import PasskeyUser, PasskeyCredential, VerifiedRegistration
from my_usermanager.adapters._callbacks import dispatch, dispatch_tuple
from my_usermanager.adapters.fastapi_htmx import StandardUserManagerUiHooks
from my_usermanager.adapters.my_auth_fastapi import build_complete_registration
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.manager import UserManager
from my_usermanager.models import User, ExternalIdentity
from my_usermanager.subjects import AuthenticatedSubject


async def main():
    loop_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    trace, calls = [], []
    with tempfile.TemporaryDirectory(dir='.') as directory:
        path = Path(directory) / 'replay.sqlite3'

        def persist(request, fail):
            calls.append(fail)
            owner = threading.get_ident()
            assert owner != loop_thread
            connection = sqlite3.connect(path)
            connection.set_trace_callback(lambda sql: trace.append((owner, threading.get_ident(), sql)))
            try:
                connection.execute('CREATE TABLE IF NOT EXISTS records (value INTEGER)')
                with connection:
                    connection.execute('INSERT INTO records VALUES (1)')
                    entered.set()
                    assert release.wait(5), 'event loop did not progress'
                    if fail:
                        raise ValueError('rollback')
                return 1
            finally:
                connection.close()
                trace.append((owner, threading.get_ident(), 'CLOSE'))

        complete = build_complete_registration(persist)
        task = asyncio.create_task(complete(None, False))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            # Reaching here while the store waits proves event-loop progress.
            task.cancel()
            assert not task.done()
        finally:
            release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('cancellation was lost')
        try:
            await complete(None, True)
        except ValueError:
            pass
        else:
            raise AssertionError('rollback error was lost')
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT COUNT(*) FROM records').fetchone() == (1,)
        assert calls == [False, True]
        assert all(created == consumed != loop_thread for created, consumed, sql in trace)
        assert any(sql == 'ROLLBACK' for _, _, sql in trace)
        assert any(sql == 'COMMIT' for _, _, sql in trace)

        # Existing, thread-affine host stores: construction, hooks and completion
        # all run on the same explicitly supplied owner, never a random worker.
        with ThreadPoolExecutor(max_workers=1) as owner:
            async def execute(operation):
                return await asyncio.get_running_loop().run_in_executor(owner, operation)

            def create():
                database = SQLiteAuthDatabase(sqlite3.connect(Path(directory) / 'host.sqlite3'))
                database.initialize()
                stores = database.stores()
                stores.users.create(User('admin', 'admin'))
                subject = AuthenticatedSubject(provider='test', subject='admin', user_id='admin')
                hooks = StandardUserManagerUiHooks(
                    manager=UserManager(users=stores.users, roles=stores.roles, grants=stores.grants),
                    current_user=lambda request: subject,
                    require_admin=lambda request, user: None,
                    callback_executor=execute,
                )
                return database, hooks, subject, threading.get_ident()

            database, hooks, subject, owner_thread = await execute(create)
            request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []})
            try:
                page = await dispatch(hooks.list_users, request, subject)
                assert page.items[0].user_id == 'admin'

                class Completion:
                    callback_executor = staticmethod(execute)
                    def __call__(self, request, result):
                        assert threading.get_ident() == owner_thread
                        verified = VerifiedRegistration(
                            PasskeyUser('second', b'handle', 'second'),
                            PasskeyCredential(b'credential', 'second', b'key'),
                        )
                        database.complete_registration(
                            request, verified, user=User('second', 'second'),
                            identity=ExternalIdentity('my-auth', 'second'),
                        )
                        return 'second'

                assert await build_complete_registration(Completion())(request, None) == 'second'

                def lazy():
                    with database.transaction() as transaction:
                        yield transaction.users.get('second').user_id

                from my_usermanager.adapters._callbacks import callback_executor
                token = callback_executor.set(execute)
                try:
                    assert await dispatch_tuple(lazy) == ('second',)
                finally:
                    callback_executor.reset(token)
            finally:
                await execute(database.database.close)

    counts = []
    async def asynchronous():
        counts.append('async')
        return (2,)
    class AsyncCallable:
        async def __call__(self):
            counts.append('object')
            return (2,)
    def synchronous():
        counts.append('sync')
        return (2,)
    @wraps(asynchronous)
    def wrapped():
        counts.append('wrapped')
        return asynchronous()
    def returns_awaitable():
        counts.append('awaitable')
        return asynchronous()
    for callback in (asynchronous, AsyncCallable(), synchronous, wrapped, returns_awaitable):
        assert await dispatch_tuple(callback) == (2,)
    assert counts == ['async', 'object', 'sync', 'wrapped', 'async', 'awaitable', 'async']
    print('callback execution replay passed')


asyncio.run(main())
PY
