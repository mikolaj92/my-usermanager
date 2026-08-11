"""Optional FastAPI hook helpers for my-auth integrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard, cast, override

from my_usermanager.adapters.my_auth import (
    MY_AUTH_PROVIDER,
    PasskeyUserLike,
    passkey_user_to_authenticated_subject,
    require_my_auth,
)
from my_usermanager.models import (
    ExternalIdentity,
    Permission,
    User,
    ValidationError,
    validate_external_subject,
    validate_identifier,
)
from my_usermanager.sessions import SessionClaimValue, SessionPrincipal
from my_usermanager.subjects import ExternalIdentityNotFoundError

if TYPE_CHECKING:
    from types import ModuleType

    from my_usermanager.subjects import ExternalIdentityUserStore

__all__: Final[tuple[str, ...]] = (
    "InvalidPasskeyUserProfileError",
    "MissingMyAuthFastAPIDependencyError",
    "PasskeyCredentialUserMismatchError",
    "PasskeyRegistrationLink",
    "PasskeyRouteHooksLike",
    "PasskeyUserProfile",
    "build_after_login_identity_linker",
    "build_after_register_identity_linker",
    "build_complete_registration",
    "build_get_auth_user",
    "build_login_session_principal_writer",
    "build_prepare_registration",
    "require_passkey_route_hooks",
    "user_to_session_principal",
)

_FASTAPI_IMPORT_NAME: Final = "my_auth.fastapi"
_MY_AUTH_FASTAPI_INSTALL_TARGET: Final = "my-auth[fastapi]"

type AccessPolicy = Callable[[User], bool]
type PasskeyProfileResolver = Callable[[User], PasskeyUserProfile | None]
type SessionPrincipalBuilder = Callable[[User], SessionPrincipal]
type MaybeAwaitable[T] = T | Awaitable[T]
type SessionPrincipalWriter[ResponseT, RequestT] = Callable[
    [ResponseT, RequestT, SessionPrincipal],
    MaybeAwaitable[None],
]


class PasskeyRouteHooksLike(Protocol):
    """Structural marker for my_auth.fastapi.PasskeyRouteHooks."""


class PasskeyCredentialLike(Protocol):
    """Structural credential shape consumed by post-route hooks."""

    @property
    def user_id(self) -> str:
        """Return the my-auth user id associated with the credential."""
        ...


class _MyAuthFastAPIModule(Protocol):
    PasskeyRouteHooks: type[PasskeyRouteHooksLike]


@dataclass(frozen=True, slots=True)
class MissingMyAuthFastAPIDependencyError(ImportError):
    """Raised when optional my-auth FastAPI helpers are unavailable."""

    import_name: str = _FASTAPI_IMPORT_NAME
    missing_import_name: str = _FASTAPI_IMPORT_NAME

    @override
    def __str__(self) -> str:
        """Return an actionable optional FastAPI installation message."""
        return (
            f"Optional dependency {self.import_name!r} is required for "
            "my_usermanager.adapters.my_auth_fastapi, but "
            f"{self.missing_import_name!r} could not be imported. Install "
            "the `my-usermanager[myauth]` extra from the same package source "
            f"and the public `{_MY_AUTH_FASTAPI_INSTALL_TARGET}` extra."
        )


@dataclass(frozen=True, slots=True)
class InvalidPasskeyUserProfileError(ValueError):
    """Raised when caller-owned PasskeyUser profile data is malformed."""

    field_name: str
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable PasskeyUser profile validation message."""
        return f"{self.field_name}: {self.reason}"


@dataclass(frozen=True, slots=True)
class PasskeyCredentialUserMismatchError(RuntimeError):
    """Raised when a route hook receives mismatched user and credential ids."""

    passkey_user_id: str
    credential_user_id: str

    @override
    def __str__(self) -> str:
        """Return a stable credential mismatch message."""
        return (
            "passkey credential user_id "
            f"{self.credential_user_id!r} does not match AuthUser "
            f"{self.passkey_user_id!r}"
        )


@dataclass(frozen=True, slots=True)
class PasskeyUserProfile:
    """Caller-owned profile data for constructing a my-auth PasskeyUser."""

    user_id: str
    user_handle: bytes
    name: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        """Validate the caller-owned profile before creating PasskeyUser."""
        _require_passkey_subject(self.user_id, field_name="user_id")
        if self.user_handle == b"":
            reason = "must not be empty"
            field_name = "user_handle"
            raise InvalidPasskeyUserProfileError(field_name, reason)
        _require_profile_text(self.name, field_name="name")
        if self.display_name is not None:
            _require_profile_text(self.display_name, field_name="display_name")


@dataclass(frozen=True, slots=True)
class PasskeyRegistrationLink:
    """Caller-owned registration decision with an explicit local link target."""

    local_user_id: str
    profile: PasskeyUserProfile

    def __post_init__(self) -> None:
        """Validate the caller-owned local link target."""
        _require_identifier(self.local_user_id, field_name="local_user_id")


def require_passkey_route_hooks() -> type[PasskeyRouteHooksLike]:
    """Raise an actionable error when my_auth.fastapi is unavailable."""
    try:
        module = import_module(_FASTAPI_IMPORT_NAME)
    except ModuleNotFoundError as exc:
        if exc.name not in {"my_auth", _FASTAPI_IMPORT_NAME}:
            raise
        raise MissingMyAuthFastAPIDependencyError(
            missing_import_name=exc.name,
        ) from exc
    if not _has_passkey_route_hooks(module):
        raise MissingMyAuthFastAPIDependencyError
    return module.PasskeyRouteHooks


def build_get_auth_user(
    store: ExternalIdentityUserStore,
    profile_resolver: PasskeyProfileResolver,
    access_policy: AccessPolicy | None = None,
) -> Callable[[str], PasskeyUserLike | None]:
    """Build a PasskeyRouteHooks.get_auth_user-compatible callable."""
    _ = require_my_auth()

    def get_auth_user(user_id: str) -> PasskeyUserLike | None:
        try:
            identity = ExternalIdentity(provider=MY_AUTH_PROVIDER, subject=user_id)
        except ValidationError:
            return None
        user = store.resolve_external_identity(identity)
        if user is None or not user.is_active:
            return None
        if access_policy is not None and not access_policy(user):
            return None
        profile = profile_resolver(user)
        if profile is None:
            return None
        if profile.user_id != user_id:
            return None
        return _passkey_user_from_profile(profile)

    return get_auth_user


def build_prepare_registration[RequestT](
    registration_policy: Callable[[RequestT, str], PasskeyUserProfile],
) -> Callable[[RequestT, str], PasskeyUserLike]:
    """Build a pure preparation hook; it performs no durable writes."""
    _ = require_my_auth()

    def prepare_registration(request: RequestT, display_name: str) -> PasskeyUserLike:
        return _passkey_user_from_profile(registration_policy(request, display_name))

    return prepare_registration


def build_complete_registration[RequestT, ResultT, UserT](
    completion_backend: Callable[
        [RequestT, ResultT], UserT | Awaitable[UserT | None] | None
    ],
) -> Callable[[RequestT, ResultT], Awaitable[UserT | None]]:
    """Build a sync/async-compatible completion hook around host backend."""

    async def complete_registration(request: RequestT, result: ResultT) -> UserT | None:
        value = completion_backend(request, result)
        if isinstance(value, Awaitable):
            return await cast("Awaitable[UserT | None]", value)
        return value

    return complete_registration


def user_to_session_principal(
    user: User,
    *,
    roles: Iterable[str] = (),
    permissions: Iterable[Permission] = (),
    claims: Mapping[str, SessionClaimValue] | None = None,
) -> SessionPrincipal:
    """Build a typed session principal from a linked local user."""
    return SessionPrincipal(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        external_identities=user.external_identities,
        claims={} if claims is None else claims,
    )


def build_login_session_principal_writer[ResponseT, RequestT](
    store: ExternalIdentityUserStore,
    write_principal: SessionPrincipalWriter[ResponseT, RequestT],
    principal_builder: SessionPrincipalBuilder = user_to_session_principal,
) -> Callable[[ResponseT, RequestT, PasskeyUserLike], MaybeAwaitable[None]]:
    """Build a PasskeyRouteHooks.login callback that writes a session principal."""

    def login(
        response: ResponseT,
        request: RequestT,
        user: PasskeyUserLike,
    ) -> MaybeAwaitable[None]:
        local_user = _require_linked_user(store, user)
        return write_principal(response, request, principal_builder(local_user))

    return login


def build_after_register_identity_linker[RequestT, CredentialT: PasskeyCredentialLike](
    store: ExternalIdentityUserStore,
) -> Callable[[RequestT, PasskeyUserLike, CredentialT], None]:
    """Build an after_register hook that links only the external identity."""
    return _build_identity_linker(store)


def build_after_login_identity_linker[RequestT, CredentialT: PasskeyCredentialLike](
    store: ExternalIdentityUserStore,
) -> Callable[[RequestT, PasskeyUserLike, CredentialT], None]:
    """Build an after_login hook that refreshes only the identity link."""
    return _build_identity_linker(store)


def _require_linked_user(
    store: ExternalIdentityUserStore,
    user: PasskeyUserLike,
) -> User:
    subject = passkey_user_to_authenticated_subject(user)
    identity = subject.external_identity()
    local_user = store.resolve_external_identity(identity)
    if local_user is None:
        raise ExternalIdentityNotFoundError(identity)
    return local_user


def _build_identity_linker[RequestT, CredentialT: PasskeyCredentialLike](
    store: ExternalIdentityUserStore,
) -> Callable[[RequestT, PasskeyUserLike, CredentialT], None]:
    def link_identity(
        _request: RequestT,
        user: PasskeyUserLike,
        credential: CredentialT,
    ) -> None:
        if credential.user_id != user.user_id:
            raise PasskeyCredentialUserMismatchError(
                passkey_user_id=user.user_id,
                credential_user_id=credential.user_id,
            )
        subject = passkey_user_to_authenticated_subject(user)
        identity = subject.external_identity()
        if store.resolve_external_identity(identity) is not None:
            return
        _ = store.link_external_identity(
            user_id=subject.user_id,
            identity=identity,
        )

    return link_identity


def _passkey_user_from_profile(profile: PasskeyUserProfile) -> PasskeyUserLike:
    passkey_user_type = require_my_auth()
    return passkey_user_type(
        profile.user_id,
        profile.user_handle,
        profile.name,
        profile.display_name,
    )


def _require_profile_text(value: str, *, field_name: str) -> None:
    try:
        _validate_profile_text(value, field_name=field_name)
    except ValidationError as exc:
        raise InvalidPasskeyUserProfileError(
            field_name=field_name,
            reason=exc.reason,
        ) from exc


def _require_identifier(value: str, *, field_name: str) -> None:
    try:
        _ = validate_identifier(value, field_name=field_name)
    except ValidationError as exc:
        raise InvalidPasskeyUserProfileError(
            field_name=field_name,
            reason=exc.reason,
        ) from exc


def _require_passkey_subject(value: str, *, field_name: str) -> None:
    try:
        _ = validate_external_subject(value, field_name=field_name)
    except ValidationError as exc:
        raise InvalidPasskeyUserProfileError(
            field_name=field_name,
            reason=exc.reason,
        ) from exc


def _validate_profile_text(value: str, *, field_name: str) -> None:
    if value == "":
        reason = "must not be empty"
        raise ValidationError(field_name, reason)
    if value != value.strip():
        reason = "must not have leading or trailing whitespace"
        raise ValidationError(field_name, reason)
    if any(character in value for character in "\r\n\t"):
        reason = "must not contain control whitespace"
        raise ValidationError(field_name, reason)


def _has_passkey_route_hooks(
    module: ModuleType,
) -> TypeGuard[_MyAuthFastAPIModule]:
    return hasattr(module, "PasskeyRouteHooks")
