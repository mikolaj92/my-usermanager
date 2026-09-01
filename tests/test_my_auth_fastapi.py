from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass, replace
from textwrap import dedent
from typing import TYPE_CHECKING, ClassVar, NoReturn, cast

import pytest

from my_usermanager.adapters import my_auth as my_auth_adapter
from my_usermanager.adapters import my_auth_fastapi as fastapi_adapter
from my_usermanager.models import ExternalIdentity, Permission, User
from my_usermanager.subjects import (
    ExternalIdentityConflictError,
    ExternalIdentityNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from my_usermanager.sessions import SessionPrincipal


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


def raise_missing_transitive_fastapi(
    _name: str, _package: str | None = None
) -> NoReturn:
    raise ModuleNotFoundError(name="fastapi")


def raise_missing_my_auth(_name: str, _package: str | None = None) -> NoReturn:
    raise ModuleNotFoundError(name="my_auth")


def raise_missing_my_auth_fastapi(_name: str, _package: str | None = None) -> NoReturn:
    raise ModuleNotFoundError(name="my_auth.fastapi")


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

        assert my_usermanager.__version__ == "0.5.11"
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


def test_fastapi_dependency_guard_preserves_transitive_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: my_auth.fastapi imports successfully enough to report a transitive failure.
    monkeypatch.setattr(
        fastapi_adapter,
        "import_module",
        raise_missing_transitive_fastapi,
    )

    # When / Then: the original transitive import failure is preserved.
    with pytest.raises(ModuleNotFoundError) as exc_info:
        _ = fastapi_adapter.require_passkey_route_hooks()
    assert exc_info.value.name == "fastapi"


@pytest.mark.parametrize(
    ("missing_import", "importer"),
    [
        ("my_auth", raise_missing_my_auth),
        ("my_auth.fastapi", raise_missing_my_auth_fastapi),
    ],
)
def test_fastapi_dependency_guard_translates_missing_my_auth_modules(
    monkeypatch: pytest.MonkeyPatch,
    missing_import: str,
    importer: Callable[[str, str | None], NoReturn],
) -> None:
    # Given: the requested my-auth module itself is unavailable.
    monkeypatch.setattr(fastapi_adapter, "import_module", importer)

    # When / Then: callers receive the actionable public dependency error.
    with pytest.raises(
        fastapi_adapter.MissingMyAuthFastAPIDependencyError,
        match=r"my-auth\[fastapi\]",
    ) as exc_info:
        _ = fastapi_adapter.require_passkey_route_hooks()
    assert exc_info.value.missing_import_name == missing_import


def test_fastapi_dependency_guard_returns_passkey_route_hooks_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: my_auth.fastapi exposes PasskeyRouteHooks.
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)

    # When: the FastAPI hook dependency is required explicitly.
    route_hooks_type = fastapi_adapter.require_passkey_route_hooks()

    # Then: callers receive the PasskeyRouteHooks type without constructing routers.
    assert route_hooks_type is FakePasskeyRouteHooks


@pytest.mark.parametrize("local_user_id", ["", " local_user", "local user"])
def test_registration_link_validates_local_user_id_as_identifier(
    local_user_id: str,
) -> None:
    # Given: a registration decision with a malformed local user identifier.
    profile = fastapi_adapter.PasskeyUserProfile(
        user_id="passkey_user_123",
        user_handle=b"new-handle",
        name="new_passkey_user",
    )

    # When / Then: the public profile validation error is consistent.
    with pytest.raises(
        fastapi_adapter.InvalidPasskeyUserProfileError,
        match=r"local_user_id:",
    ):
        _ = fastapi_adapter.PasskeyRegistrationLink(
            local_user_id=local_user_id,
            profile=profile,
        )


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
    disabled_user = User(
        user_id="local_user_123", username="local_user_123", disabled=True
    )
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
    user = User(user_id="local_user_123", username="local_user_123")
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


def test_prepare_registration_does_not_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fastapi_adapter, "import_module", import_fake_optional_module)
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_optional_module)

    def registration_policy(
        request: FakeRequest, display_name: str
    ) -> fastapi_adapter.PasskeyUserProfile:
        assert request.trace_id == "req_123"
        return fastapi_adapter.PasskeyUserProfile(
            user_id="new_passkey_user",
            user_handle=b"new-handle",
            name="new_passkey_user",
            display_name=display_name,
        )

    passkey_user = fastapi_adapter.build_prepare_registration(registration_policy)(
        FakeRequest(trace_id="req_123"), "New User"
    )
    assert passkey_user == FakePasskeyUser(
        user_id="new_passkey_user",
        user_handle=b"new-handle",
        name="new_passkey_user",
        display_name="New User",
    )


def test_user_to_session_principal_projects_profile_identity_and_claims() -> None:
    # Given: a linked local user and caller-owned authorization projection output.
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    user = User(
        user_id="local_user_123",
        username="alice",
        external_identities=frozenset({identity}),
        display_name="Alice Example",
    )

    # When: the adapter builds a typed session principal.
    principal = fastapi_adapter.user_to_session_principal(
        user,
        roles=("admin",),
        permissions=(Permission("users.read"),),
        claims={"is_member": True},
    )

    # Then: local identity and projected authorization data are preserved.
    assert principal.user_id == "local_user_123"
    assert principal.username == "alice"
    assert principal.display_name == "Alice Example"
    assert principal.external_identities == frozenset({identity})
    assert principal.roles == frozenset({"admin"})
    assert principal.permissions == frozenset({Permission("users.read")})
    assert principal.claims == {"is_member": True}


def test_login_session_principal_writer_resolves_linked_user_and_writes_session() -> (
    None
):
    # Given: a linked local user and a host-owned session writer.
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    user = User(
        user_id="local_user_123",
        username="alice",
        external_identities=frozenset({identity}),
    )
    store = FakeExternalIdentityUserStore(users=(user,))
    _ = store.link_external_identity(user_id="local_user_123", identity=identity)
    written: list[tuple[str, str, str, bool]] = []

    def write_principal(
        response: str,
        request: str,
        principal: SessionPrincipal,
    ) -> None:
        written.append(
            (
                response,
                request,
                principal.user_id,
                bool(principal.claims["is_member"]),
            ),
        )

    login = fastapi_adapter.build_login_session_principal_writer(
        store,
        write_principal,
        principal_builder=(
            lambda linked_user: fastapi_adapter.user_to_session_principal(
                linked_user,
                claims={"is_member": True},
            )
        ),
    )

    # When: my-auth invokes PasskeyRouteHooks.login.
    assert (
        login(
            "response",
            "request",
            FakePasskeyUser(
                user_id="passkey_user_123",
                user_handle=b"linked-handle",
                name="Passkey User",
            ),
        )
        is None
    )

    # Then: only the host writer mutates session state.
    assert written == [("response", "request", "local_user_123", True)]


def test_login_session_principal_writer_awaits_async_session_writer() -> None:
    # Given: a linked local user and an async host-owned session writer.
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    user = User(
        user_id="local_user_123",
        username="alice",
        external_identities=frozenset({identity}),
    )
    store = FakeExternalIdentityUserStore(users=(user,))
    _ = store.link_external_identity(user_id="local_user_123", identity=identity)
    written: list[tuple[str, str, str]] = []

    async def write_principal(
        response: str,
        request: str,
        principal: SessionPrincipal,
    ) -> None:
        written.append((response, request, principal.user_id))

    login = fastapi_adapter.build_login_session_principal_writer(
        store,
        write_principal,
    )

    async def invoke_login() -> None:
        # When: a my-auth-compatible hook invocation awaits the returned value.
        await cast(
            "Awaitable[None]",
            login(
                "response",
                "request",
                FakePasskeyUser(
                    user_id="passkey_user_123",
                    user_handle=b"linked-handle",
                    name="Passkey User",
                ),
            ),
        )

    asyncio.run(invoke_login())

    # Then: the async writer was awaited and received the projected principal.
    assert written == [("response", "request", "local_user_123")]


def test_login_session_principal_writer_requires_existing_identity_link() -> None:
    # Given: no local link for the my-auth passkey subject.
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    user = User(
        user_id="local_user_123",
        username="alice",
        external_identities=frozenset({identity}),
    )
    store = FakeExternalIdentityUserStore(users=(user,))
    login: Callable[[str, str, FakePasskeyUser], None | Awaitable[None]] = (
        fastapi_adapter.build_login_session_principal_writer(
            store,
            lambda _response, _request, _principal: None,
        )
    )

    # When / Then: the helper fails with the existing typed identity error.
    with pytest.raises(ExternalIdentityNotFoundError):
        _ = login(
            "response",
            "request",
            FakePasskeyUser(
                user_id="passkey_user_123",
                user_handle=b"linked-handle",
                name="Passkey User",
            ),
        )
