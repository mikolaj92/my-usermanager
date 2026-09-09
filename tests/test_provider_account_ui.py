"""One composed account UI works without installing a passkey adapter."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from app_factory.adapters import UserManagerBinding, install_identity_adapters
from app_factory.platform import PlatformConfig, PlatformUser
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from my_usermanager.adapters.fastapi_htmx import StandardUserManagerUiHooks
from my_usermanager.identity_capabilities import (
    AccountCapability,
    IdentityProviderCapabilities,
)
from my_usermanager.manager import UserManager
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore, MemoryUserStore
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.subjects import AuthenticatedSubject

if TYPE_CHECKING:
    from my_usermanager.adapters.fastapi_htmx import PasskeyPanel


@pytest.mark.parametrize("htmx", [False, True])
def test_account_selects_linked_providers_without_passkey_binding(htmx: bool) -> None:
    users = MemoryUserStore()
    user = users.create(
        User(
            "local-1",
            "alice",
            external_identities=frozenset({ExternalIdentity("company", "123")}),
        )
    )
    manager = UserManager(
        users=users, roles=MemoryRoleStore(), grants=MemoryGrantStore()
    )
    local = IdentityProviderCapabilities(
        provider="my-auth",
        label="Local passkeys",
        credentials=AccountCapability(mode="local", url="/auth/credentials"),
    )
    remote = IdentityProviderCapabilities(
        provider="company",
        label="Company account",
        credentials=AccountCapability(
            mode="provider",
            url="https://id.example/account",
            trusted_origin="https://id.example",
        ),
        sessions=AccountCapability(
            mode="provider",
            url="https://id.example/sessions",
            trusted_origin="https://id.example",
        ),
    )
    panel_calls: list[str] = []

    def panel(_request: Request, _subject: AuthenticatedSubject) -> PasskeyPanel | None:
        panel_calls.append("called")
        return None

    hooks = StandardUserManagerUiHooks(
        manager=manager,
        current_user=lambda _: AuthenticatedSubject("company", "123", "local-1"),
        require_admin=lambda _request, _subject: None,
        passkey_panel=panel,
        identity_providers=(local, remote),
    )
    app = FastAPI()
    _ = install_identity_adapters(
        app,
        environments=[],
        config=PlatformConfig(enable_account=True),
        usermanager=UserManagerBinding(hooks=hooks, admin_enabled=False),
        current_user=lambda _: PlatformUser("Alice", user_id="local-1"),
    )
    with TestClient(app) as client:
        response = client.get(
            "/account", headers={"HX-Request": "true"} if htmx else {}
        )
        assert response.status_code == 200
        assert 'href="https://id.example/account"' in response.text
        assert "Managed by provider" in response.text
        assert "Provider sessions" in response.text
        assert "Local passkeys" not in response.text
        assert 'href="/auth/credentials"' not in response.text
        assert 'action="/logout"' in response.text
        assert 'data-identity-feature="logout"' not in response.text
        assert panel_calls == []
        assert response.text.count('id="platform-session-title"') == 1

        _ = users.update(
            replace(
                user,
                external_identities=user.external_identities
                | {ExternalIdentity("my-auth", "local-1")},
            )
        )
        both = client.get("/account")
        assert "Local passkeys" in both.text
        assert 'href="/auth/credentials"' in both.text
        assert "Company account" in both.text
        assert panel_calls == ["called"]

        _ = users.update(user)
        again = client.get("/account")
        assert "Local passkeys" not in again.text
        assert users.get("local-1") == user


def test_no_capabilities_does_not_invent_credentials() -> None:
    descriptor = IdentityProviderCapabilities("company", "Company account")
    assert descriptor.credentials.url is None
    assert descriptor.logout.url is None
