"""Resolve a provider-verified subject without provisioning or session effects.

Calling this function is not token verification. Only trusted adapter code may
supply subjects, after completing the provider's authentication protocol.
"""

from collections.abc import Callable

from my_usermanager.auth_context import AuthenticationContext
from my_usermanager.models import User
from my_usermanager.sessions import SessionPrincipal
from my_usermanager.subjects import AuthenticatedSubject, ExternalIdentityUserStore

__all__ = ("AuthenticationContext", "resolve_authenticated_principal")

_UNAVAILABLE = "authentication unavailable"


def resolve_authenticated_principal(
    subject: AuthenticatedSubject,
    *,
    store: ExternalIdentityUserStore,
    project: Callable[[User], SessionPrincipal],
) -> SessionPrincipal:
    """Resolve the existing local account and project host-owned permissions."""
    user = store.resolve_external_identity(subject.external_identity())
    if user is None or not user.is_active or user.user_id != subject.user_id:
        raise PermissionError(_UNAVAILABLE)
    principal = project(user)
    if principal.user_id != user.user_id:
        raise PermissionError(_UNAVAILABLE)
    return principal
