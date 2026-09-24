# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportPrivateUsage=false, reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAny=false, reportUnusedCallResult=false
# ruff: noqa: ANN001, ANN002, ANN201, BLE001

from __future__ import annotations

import socket
import threading
import time
from typing import cast

import httpx2
import pytest
import uvicorn
from app_factory.fastapi import AppFactoryUi, install_app_factory_ui
from app_factory.platform import PlatformPaths
from fastapi import FastAPI, Request
from playwright.sync_api import sync_playwright

import my_usermanager.adapters.fastapi_htmx as adapter
from my_usermanager.subjects import AuthenticatedSubject

pytest.importorskip("playwright.sync_api")


class LayoutHooks:
    def page_context(self, _request: Request):
        return {"platform_paths": PlatformPaths()}

    def get_current_user(self, _request: Request):
        return AuthenticatedSubject(provider="test", subject="admin", user_id="admin")

    def require_admin(self, _request: Request, _current_user):
        return None

    def list_users(self, _request: Request, _current_user):
        return (
            adapter.UserRow(
                user_id="user_anp9kFICII0m3C2E",
                row_key="pending",
                username="mobile_test",
                display_name="mobile_test",
                email="mobile@example.test",
                disabled=False,
                is_admin=False,
                account_status="pending",
            ),
        )

    def role_options(self, _request: Request, _current_user):
        return ("user",)

    def capability_options(self, _request: Request, _current_user):
        return ()

    def set_user_disabled(self, *_args):
        message = "layout test must not mutate users"
        raise AssertionError(message)

    def grant_role(self, *_args):
        message = "layout test must not mutate grants"
        raise AssertionError(message)

    def revoke_role(self, *_args):
        message = "layout test must not mutate grants"
        raise AssertionError(message)

    def grant_permission(self, *_args):
        message = "layout test must not mutate grants"
        raise AssertionError(message)

    def revoke_permission(self, *_args):
        message = "layout test must not mutate grants"
        raise AssertionError(message)

    def csrf_context(self, _request: Request):
        return adapter.CsrfContext(hidden_inputs=(("csrf", "token"),), headers={})

    def after_user_disabled_changed(self, *_args):
        return None

    def invite_user(self, *_args):
        message = "layout test renders an existing invitation"
        raise AssertionError(message)


class LayoutCsrf:
    def token(self, _request: Request):
        return "layout-token"

    def validate(self, _request: Request, _submitted_token: str):
        return None


def _app() -> FastAPI:
    platform = AppFactoryUi(
        static_path="/static/platform",
        mount_name="platform",
        asset_prefix="/static/platform",
    )
    app = FastAPI()
    install_app_factory_ui(
        app,
        environments=[],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    adapter.install_usermanager_ui(
        app,
        platform=platform,
        hooks=cast("adapter.UserManagerUiHooks", cast("object", LayoutHooks())),
        config=adapter.UserManagerUiConfig(
            csrf_protection=cast("adapter.CsrfProtection", cast("object", LayoutCsrf()))
        ),
    )
    return app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_users_page():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with httpx2.Client(timeout=0.2) as client:
                if client.get(f"{base}/admin/users").status_code == 200:
                    break
        except httpx2.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        message = "users layout server did not start"
        raise RuntimeError(message)
    try:
        yield (f"{base}/admin/users?invitation_url=/activate%3Fcapability%3D{'A' * 48}")
    finally:
        server.should_exit = True
        thread.join(timeout=3)


def test_revealed_activation_link_stays_inside_mobile_viewport(
    live_users_page: str,
) -> None:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(live_users_page, wait_until="networkidle")
        measurements = page.evaluate(
            """() => ({
              viewport: innerWidth,
              document: document.documentElement.scrollWidth,
              activation: document.querySelector('.um-card code')
                ?.getBoundingClientRect().right ?? 0,
              table: document.querySelector('#users-table')
                ?.getBoundingClientRect().width ?? 0
            })"""
        )
        browser.close()

    assert measurements["document"] <= measurements["viewport"]
    assert measurements["activation"] <= measurements["viewport"]
    assert measurements["table"] <= measurements["viewport"]
