"""Dependency-free subject and external-identity seams."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Protocol, TypeVar, override, runtime_checkable

from my_usermanager.models import (
    ExternalIdentity,
    User,
    ValidationError,
    validate_external_subject,
    validate_identifier,
)

__all__: Final[tuple[str, ...]] = (
    "AuthenticatedSubject",
    "ExternalIdentityConflictError",
    "ExternalIdentityNotFoundError",
    "ExternalIdentityUserStore",
    "InvalidSubjectError",
    "SubjectAdapter",
    "derive_local_user_id",
)

_FALLBACK_USER_ID_PREFIX: Final = "external"
_DIGEST_SIZE: Final = 24
_SEPARATOR: Final = "\x1f"

RawSubjectT_contra = TypeVar("RawSubjectT_contra", contravariant=True)


@dataclass(frozen=True, slots=True)
class InvalidSubjectError(ValueError):
    """Raised when an authenticated external subject has malformed values."""

    field_name: str
    reason: str

    @override
    def __str__(self) -> str:
        """Return a stable subject validation message."""
        return f"{self.field_name}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ExternalIdentityConflictError(RuntimeError):
    """Raised when an external identity is linked to another user."""

    identity: ExternalIdentity
    existing_user_id: str
    requested_user_id: str

    @override
    def __str__(self) -> str:
        """Return a stable external-identity conflict message."""
        return (
            "external identity "
            f"{self.identity.provider}:{self.identity.subject} is already linked to "
            f"{self.existing_user_id}, not {self.requested_user_id}"
        )


@dataclass(frozen=True, slots=True)
class ExternalIdentityNotFoundError(LookupError):
    """Raised when a required external identity link is missing."""

    identity: ExternalIdentity

    @override
    def __str__(self) -> str:
        """Return a stable missing-external-identity message."""
        return (
            "external identity not found: "
            f"{self.identity.provider}:{self.identity.subject}"
        )


@dataclass(frozen=True, slots=True)
class AuthenticatedSubject:
    """Provider-authenticated subject plus caller-owned local user mapping."""

    provider: str
    subject: str
    user_id: str
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    email: str | None = None

    def __post_init__(self) -> None:
        """Validate identity and profile fields after dataclass creation."""
        _ = _require_identifier(self.provider, field_name="provider")
        _ = _require_external_subject(self.subject, field_name="subject")
        _ = _require_identifier(self.user_id, field_name="user_id")
        if self.username is not None:
            _ = _require_identifier(self.username, field_name="username")
        _require_optional_profile_text(self.first_name, field_name="first_name")
        _require_optional_profile_text(self.last_name, field_name="last_name")
        _require_optional_profile_text(self.display_name, field_name="display_name")
        _require_optional_profile_text(self.email, field_name="email")

    def external_identity(self) -> ExternalIdentity:
        """Return the original external identity without changing the subject."""
        return ExternalIdentity(provider=self.provider, subject=self.subject)


class SubjectAdapter(Protocol[RawSubjectT_contra]):
    """Adapter contract from a provider-native subject into core values."""

    def to_authenticated_subject(
        self,
        raw_subject: RawSubjectT_contra,
    ) -> AuthenticatedSubject:
        """Map a provider-authenticated subject to a local subject value."""
        ...


@runtime_checkable
class ExternalIdentityUserStore(Protocol):
    """Store seam for resolving and linking external identities directly."""

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        """Return the linked user or None when the identity is unlinked."""
        ...

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        """Link an external identity to an existing user or raise on conflict."""
        ...


def derive_local_user_id(*, provider: str, subject: str) -> str:
    """Derive a stable local user id from an external provider and subject."""
    checked_provider = _require_identifier(provider, field_name="provider")
    checked_subject = _require_external_subject(subject, field_name="subject")
    digest_input = f"{checked_provider}{_SEPARATOR}{checked_subject}".encode()
    digest = sha256(digest_input).hexdigest()[:_DIGEST_SIZE]
    provider_component = _normalize_provider_component(checked_provider)
    user_id = f"{_FALLBACK_USER_ID_PREFIX}_{provider_component}_{digest}"
    return _require_identifier(user_id, field_name="user_id")


def _require_identifier(value: str, *, field_name: str) -> str:
    try:
        return validate_identifier(value, field_name=field_name)
    except ValidationError as exc:
        raise InvalidSubjectError(
            field_name=field_name,
            reason=exc.reason,
        ) from exc


def _require_external_subject(value: str, *, field_name: str) -> str:
    try:
        return validate_external_subject(value, field_name=field_name)
    except ValidationError as exc:
        raise InvalidSubjectError(
            field_name=field_name,
            reason=exc.reason,
        ) from exc


def _require_optional_profile_text(value: str | None, *, field_name: str) -> None:
    if value is None:
        return
    if value == "":
        reason = "must not be empty"
        raise InvalidSubjectError(field_name, reason)
    if value != value.strip():
        reason = "must not have leading or trailing whitespace"
        raise InvalidSubjectError(field_name, reason)
    if any(character in value for character in "\r\n\t"):
        reason = "must not contain control whitespace"
        raise InvalidSubjectError(field_name, reason)


def _normalize_provider_component(provider: str) -> str:
    component_parts: list[str] = []
    previous_was_separator = False
    for character in provider.casefold():
        if character.isalnum():
            component_parts.append(character)
            previous_was_separator = False
        elif not previous_was_separator:
            component_parts.append("_")
            previous_was_separator = True
    component = "".join(component_parts).strip("_")
    if component == "":
        return "provider"
    return component
