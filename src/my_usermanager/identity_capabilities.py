"""Dependency-free account UI configuration; not authentication or authorization."""

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from my_usermanager.models import validate_identifier

_CONTROL_LIMIT = 32


@dataclass(frozen=True, slots=True)
class AccountCapability:
    """A GET entry page, not a mutation URL or a provider token.

    Provider links require an explicit trusted HTTPS origin. Queries, fragments,
    encoded paths and userinfo are intentionally unsupported: use a clean entry
    page that starts any protocol flow server-side. Never build this from claims.
    """

    mode: Literal["unavailable", "local", "provider"] = "unavailable"
    url: str | None = None
    trusted_origin: str | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous modes and links outside explicit configuration."""
        if self.mode == "unavailable":
            if self.url is not None or self.trusted_origin is not None:
                msg = "unavailable capability cannot contain a URL"
                raise ValueError(msg)
            return
        url = self.url
        if (
            not url
            or any(c.isspace() or ord(c) < _CONTROL_LIMIT for c in url)
            or any(c in url for c in ("\\", "%", "?", "#"))
        ):
            msg = "capability requires a clean entry-page URL without tokens"
            raise ValueError(msg)
        if self.mode == "local":
            if not url.startswith("/") or url.startswith("//") or self.trusted_origin:
                msg = "local capability requires an application-relative path"
                raise ValueError(msg)
            return
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if (
            self.mode != "provider"
            or parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or origin != self.trusted_origin
        ):
            msg = "provider capability requires its explicitly trusted HTTPS origin"
            raise ValueError(msg)
        # Accessing port also rejects malformed/out-of-range port syntax.
        _ = parts.port


@dataclass(frozen=True, slots=True)
class IdentityProviderCapabilities:
    """Capabilities for one linked provider; defaults make no remote promises.

    Sessions/revocation and logout describe provider pages only when delegated.
    Local application logout is independent and never gated by this descriptor.
    """

    provider: str
    label: str
    credentials: AccountCapability = field(default_factory=AccountCapability)
    recovery: AccountCapability = field(default_factory=AccountCapability)
    profile: AccountCapability = field(default_factory=AccountCapability)
    reauthentication: AccountCapability = field(default_factory=AccountCapability)
    sessions: AccountCapability = field(default_factory=AccountCapability)
    revoke_sessions: AccountCapability = field(default_factory=AccountCapability)
    logout: AccountCapability = field(default_factory=AccountCapability)

    def __post_init__(self) -> None:
        """Validate the provider key and the human-readable heading."""
        _ = validate_identifier(self.provider, field_name="provider")
        if not self.label.strip():
            msg = "provider label must not be empty"
            raise ValueError(msg)
