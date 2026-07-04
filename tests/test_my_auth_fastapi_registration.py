from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar

import pytest

from my_usermanager.adapters import my_auth as my_auth_adapter
from my_usermanager.adapters import my_auth_fastapi as fastapi_adapter
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.permissions import PermissionRegistry
from my_usermanager.subjects import ExternalIdentityConflictError


@dataclass(frozen=True, slots=True)
class FakePasskeyUser:
    user_id: str
    user_handle: bytes
    name: str
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class FakePasskeyCredential:
    user_id: str


@dataclass(frozen=True, slots=True)
class FakeRequest:
    trace_id: str


class FakeMyAuthModule:
    PasskeyUser: ClassVar[type[FakePasskeyUser]] = FakePasskeyUser


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
        linked = replace(
            user,
            external_identities=user.external_identities | frozenset({identity}),
        )
        self._links[identity] = user_id
        self._users[user_id] = linked
        return linked


def import_fake_my_auth(
    name: str,
    _package: str | None = None,
) -> FakeMyAuthModule:
    if name != "my_auth":
        raise ModuleNotFoundError(name=name)
    return FakeMyAuthModule()


def test_make_registration_user_with_identity_link_requires_explicit_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: caller policy provisions a local user and explicit passkey link target.
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_my_auth)
    store = FakeExternalIdentityUserStore(users=(User(user_id="local_user_123"),))
    grants = MemoryGrantStore()

    def registration_policy(
        request: FakeRequest,
        display_name: str,
    ) -> fastapi_adapter.PasskeyRegistrationLink:
        assert request.trace_id == "req_123"
        return fastapi_adapter.PasskeyRegistrationLink(
            local_user_id="local_user_123",
            profile=fastapi_adapter.PasskeyUserProfile(
                user_id="new_passkey_user",
                user_handle=b"new-handle",
                name="new_passkey_user",
                display_name=display_name,
            ),
        )

    make_registration_user = (
        fastapi_adapter.build_make_registration_user_with_identity_link(
            store,
            registration_policy,
        )
    )

    # When: registration policy explicitly returns profile and link target.
    passkey_user = make_registration_user(FakeRequest(trace_id="req_123"), "New User")

    # Then: identity is linked for later get_auth_user without granting access.
    identity = ExternalIdentity(provider="my-auth", subject="new_passkey_user")
    linked = store.resolve_external_identity(identity)
    assert passkey_user == FakePasskeyUser(
        user_id="new_passkey_user",
        user_handle=b"new-handle",
        name="new_passkey_user",
        display_name="New User",
    )
    assert linked is not None
    assert linked.user_id == "local_user_123"
    assert linked.external_identities == frozenset({identity})
    assert grants.list_grants_for_user("local_user_123") == ()


def test_after_register_and_after_login_link_identity_without_grants() -> None:
    # Given: explicit identity-linking hooks and empty grant state.
    store = FakeExternalIdentityUserStore(users=(User(user_id="passkey_user_123"),))
    grants = MemoryGrantStore()
    after_register = fastapi_adapter.build_after_register_identity_linker(store)
    after_login = fastapi_adapter.build_after_login_identity_linker(store)
    passkey_user = FakePasskeyUser(
        user_id="passkey_user_123",
        user_handle=b"linked-handle",
        name="Passkey User",
    )
    credential = FakePasskeyCredential(user_id="passkey_user_123")
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")

    # When: my-auth invokes the post-registration and post-login hooks.
    after_register(FakeRequest(trace_id="register"), passkey_user, credential)
    after_login(FakeRequest(trace_id="login"), passkey_user, credential)

    # Then: identity linking goes through the explicit store and never grants access.
    linked = store.resolve_external_identity(identity)
    assert linked is not None
    assert linked.external_identities == frozenset({identity})
    assert grants.list_grants_for_user("passkey_user_123") == ()


def test_after_login_uses_existing_external_identity_link_for_local_user() -> None:
    # Given: a my-auth identity already linked to a distinct local user id.
    local_user = User(user_id="local_user_123")
    store = FakeExternalIdentityUserStore(users=(local_user,))
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    _ = store.link_external_identity(user_id="local_user_123", identity=identity)
    after_login = fastapi_adapter.build_after_login_identity_linker(store)
    passkey_user = FakePasskeyUser(
        user_id="passkey_user_123",
        user_handle=b"linked-handle",
        name="Passkey User",
    )

    # When: my-auth reports a login for the already-linked passkey user.
    after_login(
        FakeRequest(trace_id="login"),
        passkey_user,
        FakePasskeyCredential(user_id="passkey_user_123"),
    )

    # Then: the helper preserves the existing local mapping instead of relinking.
    linked = store.resolve_external_identity(identity)
    assert linked is not None
    assert linked.user_id == "local_user_123"
    assert linked.external_identities == frozenset({identity})


def test_registration_link_is_idempotent_without_implicit_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_my_auth)
    store = FakeExternalIdentityUserStore(users=(User(user_id="local_user_123"),))
    grants = MemoryGrantStore()
    roles = MemoryRoleStore()
    registry = PermissionRegistry()
    before_roles = roles.list()
    before_permissions = registry.permissions()

    def registration_policy(
        _request: FakeRequest,
        display_name: str,
    ) -> fastapi_adapter.PasskeyRegistrationLink:
        return fastapi_adapter.PasskeyRegistrationLink(
            local_user_id="local_user_123",
            profile=fastapi_adapter.PasskeyUserProfile(
                user_id="passkey_user_123",
                user_handle=b"linked-handle",
                name="passkey_user_123",
                display_name=display_name,
            ),
        )

    make_registration_user = (
        fastapi_adapter.build_make_registration_user_with_identity_link(
            store,
            registration_policy,
        )
    )

    first = make_registration_user(FakeRequest(trace_id="first"), "Passkey User")
    second = make_registration_user(FakeRequest(trace_id="second"), "Passkey User")

    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    linked = store.resolve_external_identity(identity)
    assert first == second
    assert linked is not None
    assert linked.user_id == "local_user_123"
    assert grants.list_grants_for_user("local_user_123") == ()
    assert roles.list() == before_roles
    assert registry.permissions() == before_permissions


def test_registration_link_reports_existing_identity_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_my_auth)
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    store = FakeExternalIdentityUserStore(
        users=(User(user_id="existing_user"), User(user_id="new_user")),
    )
    _ = store.link_external_identity(user_id="existing_user", identity=identity)

    def registration_policy(
        _request: FakeRequest,
        _display_name: str,
    ) -> fastapi_adapter.PasskeyRegistrationLink:
        return fastapi_adapter.PasskeyRegistrationLink(
            local_user_id="new_user",
            profile=fastapi_adapter.PasskeyUserProfile(
                user_id="passkey_user_123",
                user_handle=b"linked-handle",
                name="passkey_user_123",
            ),
        )

    make_registration_user = (
        fastapi_adapter.build_make_registration_user_with_identity_link(
            store,
            registration_policy,
        )
    )

    with pytest.raises(ExternalIdentityConflictError):
        _ = make_registration_user(FakeRequest(trace_id="register"), "Passkey User")

    linked = store.resolve_external_identity(identity)
    assert linked is not None
    assert linked.user_id == "existing_user"


def test_after_register_rejects_credential_user_mismatch_without_linking() -> None:
    store = FakeExternalIdentityUserStore(users=(User(user_id="passkey_user_123"),))
    grants = MemoryGrantStore()
    after_register = fastapi_adapter.build_after_register_identity_linker(store)

    with pytest.raises(fastapi_adapter.PasskeyCredentialUserMismatchError):
        after_register(
            FakeRequest(trace_id="register"),
            FakePasskeyUser(
                user_id="passkey_user_123",
                user_handle=b"linked-handle",
                name="Passkey User",
            ),
            FakePasskeyCredential(user_id="other_passkey_user"),
        )

    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    assert store.resolve_external_identity(identity) is None
    assert grants.list_grants_for_user("passkey_user_123") == ()
