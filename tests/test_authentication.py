"""The product sees a linked local principal, not a provider's user id."""

import sqlite3
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from my_usermanager import authentication
from my_usermanager.adapters.my_auth import MyAuthSubjectAdapter, require_my_auth
from my_usermanager.adapters.sqlite import SQLiteUserStore, create_tables
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.sessions import SessionPrincipal
from my_usermanager.subjects import AuthenticatedSubject


def test_authentication_context_preserves_unknown_freshness() -> None:
    """Token receipt time is not evidence of the original login time."""
    context = authentication.AuthenticationContext()
    subject = AuthenticatedSubject(
        provider="example",
        subject="remote-123",
        user_id="local-1",
        authentication=context,
    )
    assert subject.authentication.authenticated_at is None
    assert subject.authentication.methods == frozenset()
    assert subject.authentication.assurance is None


def test_authentication_context_requires_aware_time() -> None:
    """Freshness timestamps cannot silently depend on a machine timezone."""
    with pytest.raises(ValueError, match="timezone"):
        _ = authentication.AuthenticationContext(
            authenticated_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        )
    context = authentication.AuthenticationContext(
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
        methods=frozenset({"webauthn"}),
        assurance="provider-specific",
    )
    assert context.authenticated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert context.assurance == "provider-specific"


def test_my_auth_adapter_preserves_verified_context() -> None:
    """Only the host finishing verification supplies a timestamp and methods."""
    user = require_my_auth()("local-1", b"handle", "alice")
    context = authentication.AuthenticationContext(
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC),
        methods=frozenset({"webauthn"}),
    )
    adapter = MyAuthSubjectAdapter()
    subject = adapter.to_authenticated_subject(user, authentication=context)
    assert subject.authentication == context
    assert (
        adapter.to_authenticated_subject(user).authentication.authenticated_at is None
    )


@pytest.fixture
def store() -> Generator[SQLiteUserStore, None, None]:
    """Use the actual identity-linking store with explicit connection ownership."""
    connection = sqlite3.connect(":memory:")
    try:
        create_tables(connection)
        yield SQLiteUserStore(connection)
    finally:
        connection.close()


def test_completion_uses_linked_local_user_and_host_projection(
    store: SQLiteUserStore,
) -> None:
    """A provider identifier never replaces the owner of product records."""
    identity = ExternalIdentity(provider="example", subject="remote-123")
    _ = store.create(
        User(
            user_id="local-1",
            username="alice",
            external_identities=frozenset({identity}),
        )
    )
    subject = AuthenticatedSubject(
        provider="example",
        subject="remote-123",
        user_id="local-1",
    )
    principal = authentication.resolve_authenticated_principal(
        subject,
        store=store,
        project=lambda user: SessionPrincipal(user_id=user.user_id),
    )
    assert principal.user_id == "local-1"


@pytest.mark.parametrize("failure", ["unlinked", "disabled", "mismatched"])
def test_completion_denies_before_projection(
    store: SQLiteUserStore,
    failure: str,
) -> None:
    """Untrusted identity changes cannot run provisioning or claim projection."""
    identity = ExternalIdentity(provider="example", subject="remote-123")
    user = User(
        user_id="local-1",
        username="alice",
        external_identities=frozenset({identity})
        if failure != "unlinked"
        else frozenset(),
        disabled=failure == "disabled",
    )
    _ = store.create(user)
    subject = AuthenticatedSubject(
        provider="example",
        subject="remote-123",
        user_id="wrong-user" if failure == "mismatched" else "local-1",
    )

    def forbidden_projection(_user: User) -> SessionPrincipal:
        pytest.fail("denied login must not reach host projection")

    with pytest.raises(PermissionError, match="authentication unavailable"):
        _ = authentication.resolve_authenticated_principal(
            subject,
            store=store,
            project=forbidden_projection,
        )


def test_projection_cannot_retarget_the_principal(store: SQLiteUserStore) -> None:
    """A host callback returning another user is an explicit failure."""
    identity = ExternalIdentity(provider="example", subject="remote-123")
    _ = store.create(
        User(
            user_id="local-1",
            username="alice",
            external_identities=frozenset({identity}),
        )
    )
    subject = AuthenticatedSubject(
        provider="example",
        subject="remote-123",
        user_id="local-1",
    )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        _ = authentication.resolve_authenticated_principal(
            subject,
            store=store,
            project=lambda _user: SessionPrincipal(user_id="other"),
        )


def test_completion_rechecks_disabled_status(store: SQLiteUserStore) -> None:
    """An earlier authentication result cannot reactivate a disabled account."""
    identity = ExternalIdentity(provider="example", subject="remote-123")
    user = store.create(
        User(
            user_id="local-1",
            username="alice",
            external_identities=frozenset({identity}),
        )
    )
    subject = AuthenticatedSubject(
        provider="example",
        subject="remote-123",
        user_id="local-1",
    )
    _ = store.update(replace(user, status="disabled"))
    with pytest.raises(PermissionError, match="authentication unavailable"):
        _ = authentication.resolve_authenticated_principal(
            subject,
            store=store,
            project=lambda user: SessionPrincipal(user_id=user.user_id),
        )
