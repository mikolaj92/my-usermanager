"""Provider authentication evidence, without inventing a shared MFA level."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuthenticationContext:
    """Adapter-verified evidence; unknown freshness remains unknown.

    Methods and assurance are provider-specific facts, not permission grants or
    an automatic guarantee of equivalence between WebAuthn and an IdP's MFA.
    Constructing this value does not authenticate a user.
    """

    authenticated_at: datetime | None = None
    methods: frozenset[str] = field(default_factory=frozenset)
    assurance: str | None = None

    def __post_init__(self) -> None:
        """Freeze method names and require an explicit timestamp timezone."""
        if self.authenticated_at is not None and (
            self.authenticated_at.tzinfo is None
            or self.authenticated_at.utcoffset() is None
        ):
            reason = "authenticated_at requires a timezone"
            raise ValueError(reason)
        object.__setattr__(self, "methods", frozenset(self.methods))
