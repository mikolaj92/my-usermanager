"""Reusable contract checks for my-auth FastAPI identity linking."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final, Never, override

from my_usermanager.adapters.my_auth_fastapi import (
    PasskeyCredentialUserMismatchError,
    PasskeyRegistrationLink,
    PasskeyUserProfile,
    build_after_login_identity_linker,
    build_after_register_identity_linker,
    build_make_registration_user_with_identity_link,
)
from my_usermanager.memory import MemoryGrantStore
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.subjects import ExternalIdentityConflictError

if TYPE_CHECKING:
    from my_usermanager.adapters.my_auth import PasskeyUserLike

__all__: Final[tuple[str, ...]] = ("assert_my_auth_fastapi_identity_contract",)

_LOCAL_USER_ID: Final = "local_user_123"
_EXISTING_LOCAL_USER_ID: Final = "existing_local_user_123"
_PASSKEY_USER_ID: Final = "passkey_user_123"
_SECOND_PASSKEY_USER_ID: Final = "second_passkey_user_123"
_TRACE_ID: Final = "trace-123"
_PROVIDER: Final = "my-auth"


def assert_my_auth_fastapi_identity_contract() -> None:
    """Assert identity-linking hooks stay explicit, idempotent, and grant-free."""
    _assert_registration_linking_is_explicit()
    _assert_after_register_links_without_grants()
    _assert_after_login_links_without_grants()
    _assert_existing_identity_link_is_preserved()
    _assert_credential_user_mismatch_is_rejected()


@dataclass(frozen=True, slots=True)
class _ContractCredential:
    user_id: str


@dataclass(frozen=True, slots=True)
class _ContractRequest:
    trace_id: str


@dataclass(frozen=True, slots=True)
class _IdentityContractError(AssertionError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


class _ContractExternalIdentityStore:
    __slots__: ClassVar[tuple[str, str]] = ("_links", "_users")

    _links: dict[ExternalIdentity, str]
    _users: dict[str, User]

    def __init__(self, users: tuple[User, ...]) -> None:
        self._users = {user.user_id: user for user in users}
        self._links = {
            identity: user.user_id
            for user in users
            for identity in user.external_identities
        }

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        user_id = self._links.get(identity)
        if user_id is None:
            return None
        return self._users[user_id]

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        existing_user_id = self._links.get(identity)
        if existing_user_id is not None and existing_user_id != user_id:
            raise ExternalIdentityConflictError(
                identity=identity,
                existing_user_id=existing_user_id,
                requested_user_id=user_id,
            )
        user = self._users[user_id]
        linked_user = replace(
            user,
            external_identities=user.external_identities | frozenset((identity,)),
        )
        self._links[identity] = user_id
        self._users[user_id] = linked_user
        return linked_user


def _assert_registration_linking_is_explicit() -> None:
    store = _ContractExternalIdentityStore((User(user_id=_LOCAL_USER_ID),))
    grant_store = MemoryGrantStore()

    def registration_policy(
        _request: _ContractRequest,
        display_name: str,
    ) -> PasskeyRegistrationLink:
        return PasskeyRegistrationLink(
            local_user_id=_LOCAL_USER_ID,
            profile=_profile(_PASSKEY_USER_ID, display_name=display_name),
        )

    make_registration_user = build_make_registration_user_with_identity_link(
        store,
        registration_policy,
    )
    passkey_user = make_registration_user(
        _ContractRequest(trace_id=_TRACE_ID),
        "Contract User",
    )
    identity = ExternalIdentity(provider=_PROVIDER, subject=_PASSKEY_USER_ID)

    _require_equal(passkey_user.user_id, _PASSKEY_USER_ID, "unexpected passkey user")
    _require_linked_user(store, identity=identity, user_id=_LOCAL_USER_ID)
    _require_grant_free(grant_store, user_id=_LOCAL_USER_ID)


def _assert_after_register_links_without_grants() -> None:
    store = _ContractExternalIdentityStore((User(user_id=_PASSKEY_USER_ID),))
    grant_store = MemoryGrantStore()
    passkey_user = _passkey_user(_PASSKEY_USER_ID)
    credential = _ContractCredential(user_id=_PASSKEY_USER_ID)
    identity = ExternalIdentity(provider=_PROVIDER, subject=_PASSKEY_USER_ID)

    after_register = build_after_register_identity_linker(store)

    after_register(_ContractRequest(trace_id=_TRACE_ID), passkey_user, credential)

    _require_linked_user(store, identity=identity, user_id=_PASSKEY_USER_ID)
    _require_grant_free(grant_store, user_id=_PASSKEY_USER_ID)


def _assert_after_login_links_without_grants() -> None:
    store = _ContractExternalIdentityStore((User(user_id=_PASSKEY_USER_ID),))
    grant_store = MemoryGrantStore()
    passkey_user = _passkey_user(_PASSKEY_USER_ID)
    credential = _ContractCredential(user_id=_PASSKEY_USER_ID)
    identity = ExternalIdentity(provider=_PROVIDER, subject=_PASSKEY_USER_ID)

    after_login = build_after_login_identity_linker(store)

    after_login(_ContractRequest(trace_id=_TRACE_ID), passkey_user, credential)

    _require_linked_user(store, identity=identity, user_id=_PASSKEY_USER_ID)
    _require_grant_free(grant_store, user_id=_PASSKEY_USER_ID)


def _assert_existing_identity_link_is_preserved() -> None:
    identity = ExternalIdentity(provider=_PROVIDER, subject=_SECOND_PASSKEY_USER_ID)
    existing_user = User(
        user_id=_EXISTING_LOCAL_USER_ID,
        external_identities=frozenset((identity,)),
    )
    store = _ContractExternalIdentityStore(
        (
            existing_user,
            User(user_id=_SECOND_PASSKEY_USER_ID),
        ),
    )
    after_login = build_after_login_identity_linker(store)

    after_login(
        _ContractRequest(trace_id=_TRACE_ID),
        _passkey_user(_SECOND_PASSKEY_USER_ID),
        _ContractCredential(user_id=_SECOND_PASSKEY_USER_ID),
    )

    _require_linked_user(store, identity=identity, user_id=_EXISTING_LOCAL_USER_ID)


def _assert_credential_user_mismatch_is_rejected() -> None:
    store = _ContractExternalIdentityStore((User(user_id=_PASSKEY_USER_ID),))
    after_login = build_after_login_identity_linker(store)

    try:
        after_login(
            _ContractRequest(trace_id=_TRACE_ID),
            _passkey_user(_PASSKEY_USER_ID),
            _ContractCredential(user_id=_SECOND_PASSKEY_USER_ID),
        )
    except PasskeyCredentialUserMismatchError as exc:
        _require_equal(exc.passkey_user_id, _PASSKEY_USER_ID, "unexpected passkey id")
        _require_equal(
            exc.credential_user_id,
            _SECOND_PASSKEY_USER_ID,
            "unexpected credential id",
        )
        return
    _fail("credential/user mismatch was accepted")


def _profile(user_id: str, *, display_name: str) -> PasskeyUserProfile:
    return PasskeyUserProfile(
        user_id=user_id,
        user_handle=f"handle:{user_id}".encode(),
        name=user_id,
        display_name=display_name,
    )


def _passkey_user(user_id: str) -> PasskeyUserLike:
    def registration_policy(
        _request: _ContractRequest,
        display_name: str,
    ) -> PasskeyRegistrationLink:
        return PasskeyRegistrationLink(
            local_user_id=user_id,
            profile=_profile(user_id, display_name=display_name),
        )

    make_registration_user = build_make_registration_user_with_identity_link(
        _ContractExternalIdentityStore((User(user_id=user_id),)),
        registration_policy,
    )
    return make_registration_user(
        _ContractRequest(trace_id=_TRACE_ID),
        "Contract User",
    )


def _require_linked_user(
    store: _ContractExternalIdentityStore,
    *,
    identity: ExternalIdentity,
    user_id: str,
) -> None:
    linked_user = store.resolve_external_identity(identity)
    if linked_user is None:
        _fail("identity was not linked")
    _require_equal(linked_user.user_id, user_id, "identity linked to unexpected user")
    if identity not in linked_user.external_identities:
        _fail("linked user does not expose the external identity")


def _require_grant_free(grant_store: MemoryGrantStore, *, user_id: str) -> None:
    grants = grant_store.list_grants_for_user(user_id)
    if grants != ():
        _fail("identity hook added an implicit grant")


def _require_equal(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        _fail(reason)


def _fail(reason: str) -> Never:
    raise _IdentityContractError(reason)
