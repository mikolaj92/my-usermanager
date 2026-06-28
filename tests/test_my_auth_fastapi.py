from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, replace
from textwrap import dedent
from typing import ClassVar, NoReturn

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


class FakePasskeyRouteHooks:
    __slots__: ClassVar[tuple[str, ...]] = ()


class FakeMyAuthModule:
    PasskeyUser: ClassVar[type[FakePasskeyUser]] = FakePasskeyUser


class FakeMyAuthFastAPIModule:
    PasskeyRouteHooks: ClassVar[type[FakePasskeyRouteHooks]] = FakePasskeyRouteHooks


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


def import_fake_optional_module(
    name: str,
    _package: str | None = None,
) -> FakeMyAuthModule | FakeMyAuthFastAPIModule:
    if name == "my_auth":
        return FakeMyAuthModule()
    if name == "my_auth.fastapi":
        return FakeMyAuthFastAPIModule()
    raise ModuleNotFoundError(name=name)


def raise_missing_fastapi(_name: str, _package: str | None = None) -> NoReturn:
    raise ModuleNotFoundError(name="fastapi")


def profile_for_linked_user(user: User) -> fastapi_adapter.PasskeyUserProfile | None:
    if user.user_id != "local_user_123":
        return None
    return fastapi_adapter.PasskeyUserProfile(
        user_id="passkey_user_123",
        user_handle=b"linked-handle",
        name=user.username or user.user_id,
        display_name=user.display_name,
    )


def profile_for_any_user(user: User) -> fastapi_adapter.PasskeyUserProfile | None:
    return fastapi_adapter.PasskeyUserProfile(
        user_id="passkey_user_123",
        user_handle=b"linked-handle",
        name=user.username or user.user_id,
        display_name=user.display_name,
    )


def allow_local_user(user: User) -> bool:
    return user.user_id == "local_user_123"


def test_core_and_optional_helper_import_do_not_load_optional_dependencies() -> None:
    # Given: a fresh interpreter imports core and the optional helper module.
    import_check = dedent(
        """
        import sys
        import my_usermanager
        import my_usermanager.adapters.my_auth_fastapi

        assert my_usermanager.__version__ == "0.1.0"
        assert "my_auth" not in sys.modules
        assert "my_auth.fastapi" not in sys.modules
        assert "fastapi" not in sys.modules
        assert "pydantic" not in sys.modules
        """,
    )

    # When: imports execute in isolation.
    completed = subprocess.run(
        [sys.executable, "-c", import_check],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Then: optional dependencies are not imported as side effects.
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_fastapi_dependency_guard_reports_actionable_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: my_auth.fastapi cannot import its FastAPI runtime dependency.
    monkeypatch.setattr(fastapi_adapter, "import_module", raise_missing_fastapi)

    # When / Then: requiring PasskeyRouteHooks raises a typed actionable error.
    with pytest.raises(
        fastapi_adapter.MissingMyAuthFastAPIDependencyError,
        match=r"my-auth\[fastapi\]",
    ) as exc_info:
        _ = fastapi_adapter.require_passkey_route_hooks()
    assert exc_info.value.import_name == "my_auth.fastapi"
    assert exc_info.value.missing_import_name == "fastapi"


def test_fastapi_dependency_guard_returns_passkey_route_hooks_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: my_auth.fastapi exposes PasskeyRouteHooks.
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)

    # When: the FastAPI hook dependency is required explicitly.
    route_hooks_type = fastapi_adapter.require_passkey_route_hooks()

    # Then: callers receive the PasskeyRouteHooks type without constructing routers.
    assert route_hooks_type is FakePasskeyRouteHooks


def test_get_auth_user_returns_passkey_user_for_linked_enabled_user_under_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an enabled local user linked to a my-auth external identity.
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_optional_module)
    user = User(
        user_id="local_user_123",
        username="alice",
        display_name="Alice Example",
    )
    store = FakeExternalIdentityUserStore(users=(user,))
    _ = store.link_external_identity(
        user_id="local_user_123",
        identity=ExternalIdentity(provider="my-auth", subject="passkey_user_123"),
    )
    get_auth_user = fastapi_adapter.build_get_auth_user(
        store,
        profile_for_linked_user,
        access_policy=allow_local_user,
    )

    # When: my-auth asks the hook for the authenticated passkey user id.
    auth_user = get_auth_user("passkey_user_123")

    # Then: the linked, enabled user becomes an AuthUser-compatible passkey user.
    assert auth_user == FakePasskeyUser(
        user_id="passkey_user_123",
        user_handle=b"linked-handle",
        name="alice",
        display_name="Alice Example",
    )


def test_get_auth_user_returns_none_for_missing_unlinked_or_disabled_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one disabled linked user and no link for a missing subject.
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_optional_module)
    disabled_user = User(user_id="local_user_123", disabled=True)
    store = FakeExternalIdentityUserStore(users=(disabled_user,))
    _ = store.link_external_identity(
        user_id="local_user_123",
        identity=ExternalIdentity(provider="my-auth", subject="passkey_user_123"),
    )
    get_auth_user = fastapi_adapter.build_get_auth_user(store, profile_for_any_user)

    # When / Then: denial surfaces as None so my-auth can return 403.
    assert get_auth_user("missing_passkey_user") is None
    assert get_auth_user("passkey_user_123") is None
    assert get_auth_user("") is None


def test_get_auth_user_returns_none_for_mismatched_profile_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a linked local user whose profile resolver returns another subject.
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_optional_module)
    user = User(user_id="local_user_123")
    store = FakeExternalIdentityUserStore(users=(user,))
    _ = store.link_external_identity(
        user_id="local_user_123",
        identity=ExternalIdentity(provider="my-auth", subject="passkey_user_123"),
    )

    def mismatched_profile(
        _user: User,
    ) -> fastapi_adapter.PasskeyUserProfile:
        return fastapi_adapter.PasskeyUserProfile(
            user_id="other_passkey_user",
            user_handle=b"other-handle",
            name="other_passkey_user",
        )

    get_auth_user = fastapi_adapter.build_get_auth_user(store, mismatched_profile)

    # When / Then: profile mismatch denies so my-auth can return 403.
    assert get_auth_user("passkey_user_123") is None


def test_make_registration_user_requires_explicit_policy_and_does_not_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: caller-owned registration policy and authorization stores.
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_optional_module)
    grants = MemoryGrantStore()
    roles = MemoryRoleStore()
    registry = PermissionRegistry()
    before_roles = roles.list()
    before_permissions = registry.permissions()

    def registration_policy(
        request: FakeRequest,
        display_name: str,
    ) -> fastapi_adapter.PasskeyUserProfile:
        assert request.trace_id == "req_123"
        return fastapi_adapter.PasskeyUserProfile(
            user_id="new_passkey_user",
            user_handle=b"new-handle",
            name="new_passkey_user",
            display_name=display_name,
        )

    make_registration_user = fastapi_adapter.build_make_registration_user(
        registration_policy,
    )

    # When: my-auth asks for a registration user.
    passkey_user = make_registration_user(FakeRequest(trace_id="req_123"), "New User")

    # Then: only explicit profile data is returned; access state is untouched.
    assert passkey_user == FakePasskeyUser(
        user_id="new_passkey_user",
        user_handle=b"new-handle",
        name="new_passkey_user",
        display_name="New User",
    )
    assert grants.list_grants_for_user("new_passkey_user") == ()
    assert roles.list() == before_roles
    assert registry.permissions() == before_permissions
