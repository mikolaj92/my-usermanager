from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from typing import ClassVar

import pytest

from my_usermanager.memory import MemoryGrantStore
from my_usermanager.models import ExternalIdentity, Scope, User
from my_usermanager.subjects import (
    AuthenticatedSubject,
    ExternalIdentityConflictError,
    ExternalIdentityUserStore,
    InvalidSubjectError,
    SubjectAdapter,
    derive_local_user_id,
)


@dataclass(frozen=True, slots=True)
class RawProviderSubject:
    provider: str
    subject: str
    user_id: str


@dataclass(frozen=True, slots=True)
class InvalidSubjectCase:
    field_name: str
    provider: str = "oidc"
    subject: str = "provider_user_123"
    user_id: str = "user_123"
    username: str | None = None
    display_name: str | None = None


class PassthroughSubjectAdapter:
    __slots__: ClassVar[tuple[str, ...]] = ()

    def to_authenticated_subject(
        self,
        raw_subject: RawProviderSubject,
    ) -> AuthenticatedSubject:
        return AuthenticatedSubject(
            provider=raw_subject.provider,
            subject=raw_subject.subject,
            user_id=raw_subject.user_id,
        )


class FakeExternalIdentityUserStore:
    __slots__: ClassVar[tuple[str, ...]] = ("_links", "_users")

    _links: dict[ExternalIdentity, str]
    _users: dict[str, User]

    def __init__(self, users: tuple[User, ...]) -> None:
        self._links = {}
        self._users = {user.user_id: user for user in users}

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        linked_user_id = self._links.get(identity)
        if linked_user_id is None:
            return None
        return self._users[linked_user_id]

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        linked_user_id = self._links.get(identity)
        if linked_user_id is not None and linked_user_id != user_id:
            raise ExternalIdentityConflictError(
                identity=identity,
                existing_user_id=linked_user_id,
                requested_user_id=user_id,
            )
        user = self._users[user_id]
        updated = replace(
            user,
            external_identities=user.external_identities | frozenset({identity}),
        )
        self._users[user_id] = updated
        self._links[identity] = user_id
        return updated


def test_authenticated_subject_creates_external_identity_and_preserves_profile() -> (
    None
):
    # Given: an already-authenticated provider subject with bootstrap profile data.
    subject = AuthenticatedSubject(
        provider="oidc",
        subject="provider_user_123",
        user_id="user_123",
        username="alice",
        first_name="Alice",
        last_name="Example",
        display_name="Alice Example",
        email="alice@example.com",
    )

    # When: the external identity projection is requested.
    identity = subject.external_identity()

    # Then: the original provider subject and profile fields are preserved.
    assert identity == ExternalIdentity(provider="oidc", subject="provider_user_123")
    assert subject.user_id == "user_123"
    assert subject.username == "alice"
    assert subject.first_name == "Alice"
    assert subject.last_name == "Example"
    assert subject.display_name == "Alice Example"
    assert subject.email == "alice@example.com"


@pytest.mark.parametrize(
    "invalid_case",
    [
        InvalidSubjectCase(field_name="provider", provider=""),
        InvalidSubjectCase(field_name="subject", subject=""),
        InvalidSubjectCase(field_name="user_id", user_id=" user_123"),
        InvalidSubjectCase(field_name="username", username="bad username"),
        InvalidSubjectCase(field_name="display_name", display_name="Alice\nExample"),
    ],
)
def test_authenticated_subject_rejects_malformed_values(
    invalid_case: InvalidSubjectCase,
) -> None:
    # Given: a valid subject payload with one malformed field substituted.

    # When / Then: construction fails with the typed subject error.
    with pytest.raises(InvalidSubjectError, match=invalid_case.field_name):
        _ = AuthenticatedSubject(
            provider=invalid_case.provider,
            subject=invalid_case.subject,
            user_id=invalid_case.user_id,
            username=invalid_case.username,
            display_name=invalid_case.display_name,
        )


def test_derive_local_user_id_is_deterministic() -> None:
    # Given: a provider subject that should remain the external identity subject.
    provider = "my-auth"
    subject = "passkey-subject"

    # When: local ids are derived repeatedly for the same external subject.
    first_user_id = derive_local_user_id(provider=provider, subject=subject)
    second_user_id = derive_local_user_id(provider=provider, subject=subject)
    other_user_id = derive_local_user_id(provider=provider, subject="other-subject")
    identity = ExternalIdentity(provider=provider, subject=subject)

    # Then: the helper is stable, provider-prefixed, and does not alter the subject.
    assert first_user_id == second_user_id
    assert first_user_id == "external_my_auth_02d1ec5f43b9ede47f9c3682"
    assert other_user_id == "external_my_auth_741a04f4603be7650536c4ee"
    assert identity.subject == subject


def test_subject_adapter_protocol_maps_provider_native_subjects() -> None:
    # Given: a provider-native subject and a structurally compatible adapter.
    adapter: SubjectAdapter[RawProviderSubject] = PassthroughSubjectAdapter()
    raw_subject = RawProviderSubject(
        provider="oidc",
        subject="provider_user_123",
        user_id="user_123",
    )

    # When: the adapter maps the provider subject to the core value.
    subject = adapter.to_authenticated_subject(raw_subject)

    # Then: callers receive the dependency-free authenticated-subject shape.
    assert subject == AuthenticatedSubject(
        provider="oidc",
        subject="provider_user_123",
        user_id="user_123",
    )


def test_external_identity_user_store_protocol_resolves_and_links_identities() -> None:
    # Given: a small fake that implements the sync external-identity seam.
    user = User(user_id="user_123")
    store = FakeExternalIdentityUserStore(users=(user,))
    identity = ExternalIdentity(provider="oidc", subject="provider_user_123")

    # When: the identity is linked and resolved through the protocol surface.
    seam: ExternalIdentityUserStore = store
    before_link = seam.resolve_external_identity(identity)
    linked = seam.link_external_identity(user_id="user_123", identity=identity)
    after_link = seam.resolve_external_identity(identity)

    # Then: resolution is explicit and no scanning contract is required.
    assert isinstance(store, ExternalIdentityUserStore)
    assert before_link is None
    assert linked.external_identities == frozenset({identity})
    assert after_link == linked


def test_external_identity_link_conflict_raises_typed_error() -> None:
    # Given: one external identity already linked to a user.
    first_user = User(user_id="user_123")
    second_user = User(user_id="user_456")
    store = FakeExternalIdentityUserStore(users=(first_user, second_user))
    identity = ExternalIdentity(provider="oidc", subject="provider_user_123")
    _ = store.link_external_identity(user_id="user_123", identity=identity)

    # When / Then: linking the same identity to a different user is a typed conflict.
    with pytest.raises(ExternalIdentityConflictError, match="user_123") as exc_info:
        _ = store.link_external_identity(user_id="user_456", identity=identity)
    assert exc_info.value.identity == identity
    assert exc_info.value.existing_user_id == "user_123"
    assert exc_info.value.requested_user_id == "user_456"


def test_subject_seam_does_not_mutate_grants_or_import_optionals() -> None:
    # Given: the core subject seam and empty authorization stores.
    grants = MemoryGrantStore()
    user = User(user_id="user_123", scope=Scope.global_())
    store = FakeExternalIdentityUserStore(users=(user,))
    subject = AuthenticatedSubject(
        provider="oidc",
        subject="provider_user_123",
        user_id="user_123",
    )

    # When: the authenticated subject is linked to a local user.
    linked = store.link_external_identity(
        user_id=subject.user_id,
        identity=subject.external_identity(),
    )

    # Then: identity linking does not grant access or load optional integrations.
    assert linked.scope == Scope.global_()
    assert linked.system is False
    assert grants.list_grants_for_user("user_123") == ()
    assert "my_auth" not in sys.modules
    assert "fastapi" not in sys.modules
    assert "pydantic" not in sys.modules
