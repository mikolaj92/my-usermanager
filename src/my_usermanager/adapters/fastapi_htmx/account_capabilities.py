"""Optional provider descriptor hook, independent of provider SDKs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from my_usermanager.adapters.fastapi_htmx.awaitables import resolve

if TYPE_CHECKING:
    from fastapi import Request

    from my_usermanager.adapters.fastapi_htmx.awaitables import MaybeAwaitable
    from my_usermanager.identity_capabilities import IdentityProviderCapabilities
    from my_usermanager.subjects import AuthenticatedSubject


class _ProviderHook(Protocol):
    def account_identity_providers(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> MaybeAwaitable[tuple[IdentityProviderCapabilities, ...] | None]: ...


async def account_providers(
    hooks: object, request: Request, subject: AuthenticatedSubject
) -> tuple[IdentityProviderCapabilities, ...] | None:
    """Return trusted configured descriptors; None keeps legacy hooks working."""
    if not callable(getattr(hooks, "account_identity_providers", None)):
        return None
    return await resolve(
        cast("_ProviderHook", hooks).account_identity_providers(request, subject)
    )
