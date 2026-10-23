import asyncio
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from types import SimpleNamespace
from typing import cast

import pytest
from jinja2 import DictLoader, Environment
from starlette.requests import Request

from my_usermanager.adapters.fastapi_htmx.config import (
    AuditPage,
    AuditRow,
    PasskeyPanel,
    UserManagerUiConfig,
    UserManagerUiHooks,
)
from my_usermanager.adapters.fastapi_htmx.page import (
    PageQuery,
    load_audit_page,
    named_action,
)
from my_usermanager.adapters.fastapi_htmx.routes_account import _account_endpoint
from my_usermanager.stores import AuditFilters
from my_usermanager.subjects import AuthenticatedSubject


async def _listing_replay() -> None:
    subject = AuthenticatedSubject(provider="local", subject="admin", user_id="admin")
    request = Request({"type": "http", "method": "GET", "headers": []})
    row = AuditRow("now", "admin", "read", "user", "target", "ok")
    observed, invoked = [], []

    class LazyRows(Sequence):
        def __len__(self) -> int:
            observed.append(threading.get_ident())
            return 1

        def __getitem__(self, index: int) -> AuditRow:
            observed.append(threading.get_ident())
            if index != 0:
                raise IndexError(index)
            return row

        def __iter__(self) -> Iterator[AuditRow]:
            observed.append(threading.get_ident())
            yield row

    def listing(*_args: object, **_kwargs: object) -> AuditPage:
        invoked.append(threading.get_ident())
        return AuditPage(
            LazyRows(), 7, 14, has_previous=True, has_next=True, filtered=True
        )

    async def async_listing(*args: object, **kwargs: object) -> AuditPage:
        await asyncio.sleep(0)
        return listing(*args, **kwargs)

    for provider in (listing, async_listing):
        observed.clear()
        invoked.clear()
        page = await load_audit_page(
            provider,
            request,
            subject,
            page=PageQuery(7, 14, 3, {}),
            filters=AuditFilters(),
        )
        assert page == AuditPage(
            (row,), 7, 14, has_previous=True, has_next=True, filtered=True
        )
        assert observed
        assert all(worker != threading.get_ident() for worker in observed)
        assert len(invoked) == 1
        if provider is listing:
            assert set(observed) == set(invoked)
        else:
            assert invoked == [threading.get_ident()]

    calls = []

    def broken(*_args: object, **_kwargs: object) -> object:
        calls.append("called")
        message = "callback failure"
        raise TypeError(message)

    with pytest.raises(TypeError, match="callback failure"):
        await load_audit_page(
            broken,
            request,
            subject,
            page=PageQuery(None, 0, 1, {}),
            filters=AuditFilters(),
        )
    assert calls == ["called"]


async def _panel_replay() -> None:
    subject = AuthenticatedSubject(provider="local", subject="admin", user_id="admin")
    request = Request(
        {"type": "http", "method": "GET", "headers": [], "query_string": b""}
    )
    observed, invoked = [], []
    started, release = threading.Event(), threading.Event()

    class LazyContext(Mapping[str, object]):
        def __len__(self) -> int:
            observed.append(threading.get_ident())
            return 1

        def __iter__(self) -> Iterator[str]:
            observed.append(threading.get_ident())
            yield "value"

        def __getitem__(self, key: str) -> object:
            observed.append(threading.get_ident())
            if key != "value":
                raise KeyError(key)
            return "ready"

    def panel(*_args: object) -> PasskeyPanel:
        invoked.append(threading.get_ident())
        started.set()
        assert release.wait(5), "panel release timed out"
        return PasskeyPanel("panel.html", LazyContext())

    async def async_panel(*_args: object) -> PasskeyPanel:
        invoked.append(threading.get_ident())
        await asyncio.sleep(0)
        return PasskeyPanel("panel.html", LazyContext())

    templates = Environment(
        loader=DictLoader(
            {
                "account/index.html": "{{ passkey_panel_html }}",
                "panel.html": "{{ value }}",
            }
        ),
        autoescape=True,
    )
    for provider in (panel, async_panel):
        observed.clear()
        invoked.clear()
        hooks = cast(
            "UserManagerUiHooks",
            SimpleNamespace(
                get_current_user=lambda _request: subject,
                page_context=lambda _request: {"platform_paths": {}},
                render_passkey_panel=provider,
            ),
        )
        task = asyncio.ensure_future(
            _account_endpoint(
                templates, UserManagerUiConfig(admin_enabled=False), hooks
            )(request)
        )
        try:
            if provider is panel:
                assert await asyncio.to_thread(started.wait, 5), "panel did not start"
                await asyncio.sleep(0)
                assert not task.done()
        finally:
            release.set()
            response = await asyncio.wait_for(task, 5)
        assert response.body == b"ready"
        assert observed
        assert all(worker != threading.get_ident() for worker in observed)
        assert len(invoked) == 1
        if provider is panel:
            assert set(observed) == set(invoked)
        else:
            assert invoked == [threading.get_ident()]


class Csrf:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def validate(self, _request: Request, submitted: str) -> None:
        assert submitted == "valid"
        self.events.append("csrf")


async def receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"user_id=target&csrf=valid"}


async def _transaction_replay(*, fail: bool) -> None:
    subject = AuthenticatedSubject(provider="local", subject="admin", user_id="admin")
    started, release = threading.Event(), threading.Event()
    events, terminal = [], []
    main_thread = threading.get_ident()
    failure = ValueError("rollback")

    async def authenticate(_request: Request) -> AuthenticatedSubject:
        events.append("auth")
        return subject

    async def authorize(*_args: object) -> None:
        events.append("admin")

    def mutate(*_args: object) -> None:
        assert events == ["auth", "admin", "csrf"]
        worker = threading.get_ident()
        assert worker != main_thread
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE result (value INTEGER)")
            try:
                with connection:
                    connection.execute("INSERT INTO result VALUES (1)")
                    started.set()
                    assert release.wait(5), "worker release timed out"
                    if fail:
                        raise failure
            except ValueError:
                terminal.append("rollback")
            else:
                terminal.append("commit")
            count = connection.execute("SELECT count(*) FROM result").fetchone()[0]
            assert count == (0 if fail else 1)
            if fail:
                raise failure
        finally:
            assert threading.get_ident() == worker
            connection.close()
            terminal.append("cleanup")

    post = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
        receive,
    )
    hooks = cast(
        "UserManagerUiHooks",
        SimpleNamespace(
            get_current_user=authenticate,
            require_admin=authorize,
            soft_delete_user=mutate,
        ),
    )
    config = UserManagerUiConfig(csrf_protection=Csrf(events))
    task = asyncio.create_task(
        named_action(
            post,
            config=config,
            hooks=hooks,
            hook_name="soft_delete_user",
            required=("user_id",),
            redirect_url="/admin/users",
        )
    )
    await _cancel_accepted(task, started, release, terminal)
    assert terminal == ["rollback" if fail else "commit", "cleanup"]


async def _cancel_accepted(
    task: asyncio.Task,
    started: threading.Event,
    release: threading.Event,
    terminal: list[str],
) -> None:
    try:
        assert await asyncio.to_thread(started.wait, 5), "mutation did not start"
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert terminal == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)


with asyncio.Runner() as runner:
    runner.run(_listing_replay())
    runner.run(_panel_replay())
    runner.run(_transaction_replay(fail=False))
    runner.run(_transaction_replay(fail=True))
