"""Optional adapter for my-auth passkey users."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard, override

from my_usermanager.models import ValidationError, validate_identifier
from my_usermanager.subjects import (
    AuthenticatedSubject,
    SubjectAdapter,
    derive_local_user_id,
)

if TYPE_CHECKING:
    from types import ModuleType

__all__: Final[tuple[str, ...]] = (
    "MY_AUTH_PROVIDER",
    "MissingMyAuthDependencyError",
    "MyAuthSubjectAdapter",
    "PasskeyUserFactory",
    "PasskeyUserLike",
    "passkey_user_to_authenticated_subject",
    "require_my_auth",
)

MY_AUTH_PROVIDER: Final = "my-auth"
_EXTRA_NAME: Final = "myauth"
_IMPORT_NAME: Final = "my_auth"
_MY_AUTH_INSTALL_TARGET: Final = "my-auth @ git+https://github.com/mikolaj92/my-auth"


class PasskeyUserLike(Protocol):
    """Structural shape consumed from my_auth.PasskeyUser."""

    @property
    def user_id(self) -> str:
        """Return the stable my-auth user id."""
        ...

    @property
    def user_handle(self) -> bytes:
        """Return the opaque my-auth passkey user handle."""
        ...

    @property
    def name(self) -> str:
        """Return the my-auth user name."""
        ...

    @property
    def display_name(self) -> str | None:
        """Return the optional my-auth display name."""
        ...


class PasskeyUserFactory(Protocol):
    """Callable constructor shape exposed by my_auth.PasskeyUser."""

    def __call__(
        self,
        user_id: str,
        user_handle: bytes,
        name: str,
        display_name: str | None = None,
    ) -> PasskeyUserLike:
        """Create a my-auth PasskeyUser-compatible value."""
        ...


class _MyAuthModule(Protocol):
    PasskeyUser: PasskeyUserFactory


@dataclass(frozen=True, slots=True)
class MissingMyAuthDependencyError(ImportError):
    """Raised when the optional my-auth dependency is unavailable."""

    import_name: str = _IMPORT_NAME
    extra_name: str = _EXTRA_NAME

    @override
    def __str__(self) -> str:
        """Return an actionable optional-dependency installation message."""
        return (
            f"Optional dependency {self.import_name!r} is required for "
            "my_usermanager.adapters.my_auth. Install the "
            f"`my-usermanager[{self.extra_name}]` extra from the same package "
            f"source, or install it directly with `uv add '{_MY_AUTH_INSTALL_TARGET}'`."
        )


@dataclass(frozen=True, slots=True)
class MyAuthSubjectAdapter(SubjectAdapter[PasskeyUserLike]):
    """Concrete SubjectAdapter for installed my-auth applications."""

    def __post_init__(self) -> None:
        """Verify that the optional my-auth package is importable."""
        _ = require_my_auth()

    @override
    def to_authenticated_subject(
        self,
        raw_subject: PasskeyUserLike,
    ) -> AuthenticatedSubject:
        """Map a my-auth PasskeyUser-like value to an authenticated subject."""
        return passkey_user_to_authenticated_subject(raw_subject)


def require_my_auth() -> PasskeyUserFactory:
    """Raise an actionable error when my-auth is not installed."""
    try:
        module = import_module(_IMPORT_NAME)
    except ModuleNotFoundError as exc:
        if exc.name is not None:
            raise MissingMyAuthDependencyError from exc
        raise
    if not _has_passkey_user(module):
        raise MissingMyAuthDependencyError
    return module.PasskeyUser


def _has_passkey_user(module: ModuleType) -> TypeGuard[_MyAuthModule]:
    return hasattr(module, "PasskeyUser")


def passkey_user_to_authenticated_subject(
    passkey_user: PasskeyUserLike,
) -> AuthenticatedSubject:
    """Map a my-auth PasskeyUser-like value to the core subject seam."""
    try:
        local_user_id = validate_identifier(passkey_user.user_id, field_name="user_id")
    except ValidationError:
        local_user_id = derive_local_user_id(
            provider=MY_AUTH_PROVIDER,
            subject=passkey_user.user_id,
        )
    return AuthenticatedSubject(
        provider=MY_AUTH_PROVIDER,
        subject=passkey_user.user_id,
        user_id=local_user_id,
        username=local_user_id,
        display_name=passkey_user.display_name or passkey_user.name,
    )
