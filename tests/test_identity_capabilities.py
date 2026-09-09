"""Provider UI capabilities are configuration, never claims or permissions."""

import pytest

from my_usermanager import identity_capabilities as caps


def test_features_are_unavailable_unless_explicitly_configured() -> None:
    provider = caps.IdentityProviderCapabilities(provider="oidc", label="Company login")
    assert provider.credentials.mode == "unavailable"
    assert provider.recovery.mode == "unavailable"
    assert provider.profile.mode == "unavailable"
    assert provider.reauthentication.mode == "unavailable"
    assert provider.sessions.mode == "unavailable"
    assert provider.revoke_sessions.mode == "unavailable"
    assert provider.logout.mode == "unavailable"


def test_local_and_delegated_entry_pages_are_distinct() -> None:
    local = caps.AccountCapability(mode="local", url="/portal/account/sessions")
    remote = caps.AccountCapability(
        mode="provider",
        url="https://id.example/account",
        trusted_origin="https://id.example",
    )
    assert local.mode == "local"
    assert remote.mode == "provider"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "//id.example/account",
        "http://id.example/account",
        "https://evil.example/account",
        "https://id.example:8443/account",
        "https://id.example/account?token=secret",
        "https://id.example/account#token",
        "https://user:password@id.example/account",
        "https://id.example/\\evil",
        "https://id.example/account\n",
        "https://id.example/%3ftoken=secret",
    ],
)
def test_delegated_urls_reject_untrusted_or_token_bearing_links(url: str) -> None:
    with pytest.raises(ValueError, match="capability"):
        _ = caps.AccountCapability(
            mode="provider", url=url, trusted_origin="https://id.example"
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "unavailable", "url": "/credentials"},
        {"mode": "local", "url": "//evil.example"},
        {"mode": "local", "url": "/\\evil.example"},
        {"mode": "local", "url": "/login?token=secret"},
        {"mode": "local"},
        {"mode": "provider", "url": "https://id.example/account"},
        {"mode": "unknown"},
    ],
)
def test_contradictory_capabilities_fail_closed(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="capability"):
        _ = caps.AccountCapability(**kwargs)  # pyright: ignore[reportArgumentType]
