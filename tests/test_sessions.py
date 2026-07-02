from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, cast

import pytest

from my_usermanager import (
    ExternalIdentity,
    InvalidSessionPrincipalError,
    Permission,
    SessionClaimValue,
    SessionPrincipal,
    clear_session_principal,
    clear_token_principal,
    principal_template_context,
    read_session_principal,
    read_token_principal,
    refresh_session_principal,
    write_session_principal,
    write_token_principal,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class MemorySessionTokenStore:
    __slots__: ClassVar[tuple[str, ...]] = ("sessions",)

    sessions: dict[str, SessionPrincipal]

    def __init__(self) -> None:
        self.sessions = {}

    def get(self, token: str) -> SessionPrincipal | None:
        return self.sessions.get(token)

    def save(self, token: str, principal: SessionPrincipal) -> SessionPrincipal:
        self.sessions[token] = principal
        return principal

    def delete(self, token: str) -> None:
        _ = self.sessions.pop(token, None)


def principal() -> SessionPrincipal:
    return SessionPrincipal(
        user_id="user_123",
        username="alice",
        display_name="Alice Example",
        roles=frozenset({"admin"}),
        permissions=frozenset({Permission("users.read")}),
        external_identities=frozenset(
            {ExternalIdentity(provider="my-auth", subject="passkey_user_123")},
        ),
        claims={"is_member": True, "svg_level": 2, "report": "full"},
    )


def test_session_principal_serializes_to_json_safe_payload() -> None:
    current = principal()

    payload = current.to_session()

    assert payload == {
        "user_id": "user_123",
        "username": "alice",
        "display_name": "Alice Example",
        "roles": ["admin"],
        "permissions": ["users.read"],
        "external_identities": [
            {"provider": "my-auth", "subject": "passkey_user_123"},
        ],
        "claims": {"is_member": True, "report": "full", "svg_level": 2},
    }
    assert SessionPrincipal.from_session(payload) == current
    assert current.has_role("admin") is True
    assert current.has_permission("users.read") is True


def test_session_helpers_read_write_refresh_and_clear_dict_backed_sessions() -> None:
    session: dict[str, object] = {}
    current = principal()

    assert write_session_principal(session, current) == current
    assert read_session_principal(session) == current

    refreshed = refresh_session_principal(
        session,
        lambda old: replace(old, roles=frozenset({"member"})),
    )

    assert refreshed == replace(current, roles=frozenset({"member"}))
    updated = read_session_principal(session)
    assert updated is not None
    assert updated.has_role("member") is True

    cleared = refresh_session_principal(session, lambda _old: None)

    assert cleared is None
    assert read_session_principal(session) is None
    _ = write_session_principal(session, current)
    clear_session_principal(session)
    assert session == {}


def test_malformed_session_payload_is_not_trusted() -> None:
    malformed_session = {"my_usermanager.principal": {"user_id": 123}}
    assert read_session_principal(malformed_session) is None
    assert read_session_principal({"my_usermanager.principal": "user_123"}) is None
    bad_claims = cast("Mapping[str, SessionClaimValue]", {"bad": object()})
    with pytest.raises(InvalidSessionPrincipalError, match=r"claims\.bad"):
        _ = SessionPrincipal(user_id="user_123", claims=bad_claims)


def test_token_store_helpers_support_opaque_db_backed_sessions() -> None:
    store = MemorySessionTokenStore()
    current = principal()

    assert read_token_principal(None, store) is None
    assert write_token_principal("session_123", current, store) == current
    assert read_token_principal("session_123", store) == current

    clear_token_principal("session_123", store)

    assert read_token_principal("session_123", store) is None


def test_principal_template_context_is_template_friendly() -> None:
    current = principal()

    assert principal_template_context(None) == {
        "current_user": None,
        "is_authenticated": False,
    }
    assert principal_template_context(current) == {
        "current_user": current,
        "is_authenticated": True,
    }
