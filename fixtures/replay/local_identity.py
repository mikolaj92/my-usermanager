import asyncio
import secrets
import threading
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from my_usermanager.adapters.fastapi_htmx.awaitables import resolve
from my_usermanager.adapters.local_identity import install_local_identity
from my_usermanager.models import Scope, User
from my_usermanager.subjects import AuthenticatedSubject


async def _replay(database: Path) -> None:
    app = FastAPI()
    identity = install_local_identity(
        app,
        database=database,
        rp_id="localhost",
        origin="http://localhost",
        session_secret=secrets.token_urlsafe(),
    )
    identity.manager.users.create(User("admin", "admin"))
    identity.manager.users.create(User("target", "target"))
    identity.manager.grants.add_role_grant("admin", "admin", Scope.global_())
    hooks = app.state.usermanager_ui.hooks._hooks
    request = Request({"type": "http", "session": {}})
    subject = AuthenticatedSubject(provider="local", subject="admin", user_id="admin")
    started, release = threading.Event(), threading.Event()
    observed = []
    grants = hooks.manager.grants

    class LazyGrants:
        def __getattr__(self, name: str) -> object:
            return getattr(grants, name)

        def list_grants_for_user(self, user_id: str) -> object:
            observed.append(threading.get_ident())
            started.set()
            assert release.wait(5), "replay release timed out"
            for grant in grants.list_grants_for_user(user_id):
                observed.append(threading.get_ident())
                yield grant

    hooks.manager = replace(hooks.manager, grants=LazyGrants())
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(hooks.list_users(request, subject))
        try:
            assert await asyncio.to_thread(started.wait, 5), "store did not start"
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "cancelled operation was abandoned"
        finally:
            release.set()
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.CancelledError:
                pass
            else:
                message = "cancellation was lost"
                raise RuntimeError(message)
        assert observed
        assert len(set(observed)) == 1
        assert all(worker != threading.get_ident() for worker in observed)
        callbacks = []

        def after_disabled(*_args: object) -> None:
            callbacks.append(threading.get_ident())
            message = "rollback"
            raise ValueError(message)

        hooks.after_disabled = after_disabled
        with pytest.raises(ValueError, match="rollback"):
            await hooks.set_user_disabled(request, subject, "target", disabled=True)
        assert not identity.manager.users.get("target").disabled
        callbacks.clear()
        hooks.after_disabled = lambda *_args: callbacks.append(threading.get_ident())
        row = await hooks.set_user_disabled(request, subject, "target", disabled=True)
        await hooks.after_user_disabled_changed(request, subject, row)
        assert callbacks == [observed[0]]
        await _async_callbacks(identity, hooks, request, subject, observed[0])


async def _async_callbacks(
    identity: object,
    hooks: object,
    request: Request,
    subject: AuthenticatedSubject,
    worker: int,
) -> None:
    started, release = threading.Event(), threading.Event()
    callbacks = []
    for fail in (False, True):
        started.clear()
        release.clear()
        callbacks.clear()

        async def after_async(*_args: object, fail: bool = fail) -> None:
            callbacks.append(threading.get_ident())
            started.set()
            assert release.wait(5), "callback release timed out"
            await asyncio.sleep(0)
            callbacks.append(threading.get_ident())
            if fail:
                message = "async rollback"
                raise ValueError(message)

        hooks.after_disabled = after_async
        task = asyncio.create_task(
            resolve(
                lambda: hooks.set_user_disabled(
                    request, subject, "target", disabled=False
                )
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 5), "callback did not start"
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "transaction was abandoned"
            assert identity.manager.users.get("target").disabled
        finally:
            release.set()
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.CancelledError:
                pass
            else:
                message = "cancellation was lost"
                raise RuntimeError(message)
        assert callbacks == [worker, worker]
        assert identity.manager.users.get("target").disabled == fail
        hooks.after_disabled = None
        await hooks.set_user_disabled(request, subject, "target", disabled=True)


with TemporaryDirectory() as directory, asyncio.Runner() as runner:
    runner.run(_replay(Path(directory) / "identity.db"))
