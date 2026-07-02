"""Optional FastAPI dependencies for session principals and authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, NoReturn, cast

from fastapi import HTTPException, Request, status

from my_usermanager.claims import ADMIN_ACCESS_PERMISSION, GrantClaimsProjector
from my_usermanager.models import Permission, Scope, validate_identifier
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.sessions import (
    SESSION_PRINCIPAL_KEY,
    SessionClaimValue,
    SessionPrincipal,
    clear_session_principal,
    read_session_principal,
    write_session_principal,
)

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

__all__: Final[tuple[str, ...]] = (
    "API_AUTHORIZATION_RESPONSES",
    "AuthorizationResponses",
    "clear_current_user",
    "current_user",
    "current_user_dependency",
    "require_authenticated",
    "require_claim",
    "require_owner_or_admin",
    "require_permission",
    "require_role",
    "require_scoped_permission",
    "require_user",
    "require_user_dependency",
    "write_current_user",
)

type PrincipalDependency = Callable[[Request], SessionPrincipal | None]
type OwnerIdGetter = Callable[[Request], str]


@dataclass(frozen=True, slots=True)
class AuthorizationResponses:
    """Configurable API or browser response behavior for auth dependencies."""

    login_url: str | None = None
    forbidden_url: str | None = None
    unauthenticated_detail: str = "authentication required"
    forbidden_detail: str = "forbidden"

    @classmethod
    def redirects(
        cls,
        *,
        login_url: str = "/login",
        forbidden_url: str = "/request-access",
    ) -> AuthorizationResponses:
        """Return browser redirect behavior for unauthenticated/forbidden users."""
        return cls(login_url=login_url, forbidden_url=forbidden_url)

    def unauthenticated(self) -> NoReturn:
        """Raise the configured unauthenticated response."""
        self._raise(
            redirect_url=self.login_url,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=self.unauthenticated_detail,
        )

    def forbidden(self) -> NoReturn:
        """Raise the configured forbidden response."""
        self._raise(
            redirect_url=self.forbidden_url,
            status_code=status.HTTP_403_FORBIDDEN,
            detail=self.forbidden_detail,
        )

    def _raise(
        self,
        *,
        redirect_url: str | None,
        status_code: int,
        detail: str,
    ) -> NoReturn:
        if redirect_url is not None:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail=detail,
                headers={"Location": redirect_url},
            )
        raise HTTPException(status_code=status_code, detail=detail)


API_AUTHORIZATION_RESPONSES: Final = AuthorizationResponses()


def current_user(request: Request) -> SessionPrincipal | None:
    """FastAPI dependency returning the current session principal if present."""
    return read_session_principal(_session(request))


def require_user(request: Request) -> SessionPrincipal:
    """FastAPI dependency requiring an authenticated session principal."""
    principal = current_user(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return principal


def require_authenticated(request: Request) -> SessionPrincipal:
    """Alias dependency for requiring an authenticated session principal."""
    return require_user(request)


def current_user_dependency(
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> Callable[[Request], SessionPrincipal | None]:
    """Return a current-user dependency for a custom session key."""

    def dependency(request: Request) -> SessionPrincipal | None:
        return read_session_principal(_session(request), key=key)

    return dependency


def require_user_dependency(
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> Callable[[Request], SessionPrincipal]:
    """Return a require-user dependency for a custom session key."""

    def dependency(request: Request) -> SessionPrincipal:
        principal = read_session_principal(_session(request), key=key)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        return principal

    return dependency


def require_role(
    role_name: str,
    *,
    user_dependency: PrincipalDependency = current_user,
    responses: AuthorizationResponses = API_AUTHORIZATION_RESPONSES,
    projector: GrantClaimsProjector | None = None,
) -> Callable[[Request], SessionPrincipal]:
    """Return a dependency requiring a role on the current principal."""
    checked_role = validate_identifier(role_name, field_name="role_name")

    def dependency(request: Request) -> SessionPrincipal:
        principal = _required_principal(request, user_dependency, responses)
        if _principal_has_role(principal, checked_role, projector=projector):
            return principal
        return responses.forbidden()

    return dependency


def require_permission(
    permission: str | Permission,
    *,
    scope: Scope | None = None,
    user_dependency: PrincipalDependency = current_user,
    responses: AuthorizationResponses = API_AUTHORIZATION_RESPONSES,
    projector: GrantClaimsProjector | None = None,
) -> Callable[[Request], SessionPrincipal]:
    """Return a dependency requiring a permission on the current principal."""
    checked_permission = _coerce_permission(permission)

    def dependency(request: Request) -> SessionPrincipal:
        principal = _required_principal(request, user_dependency, responses)
        if _principal_has_permission(
            principal,
            checked_permission,
            scope=scope,
            projector=projector,
        ):
            return principal
        return responses.forbidden()

    return dependency


def require_scoped_permission(
    permission: str | Permission,
    scope: Scope,
    *,
    user_dependency: PrincipalDependency = current_user,
    responses: AuthorizationResponses = API_AUTHORIZATION_RESPONSES,
    projector: GrantClaimsProjector | None = None,
) -> Callable[[Request], SessionPrincipal]:
    """Return a dependency requiring a permission at a scope."""
    return require_permission(
        permission,
        scope=scope,
        user_dependency=user_dependency,
        responses=responses,
        projector=projector,
    )


def require_claim(
    name: str,
    value: SessionClaimValue = True,
    *,
    user_dependency: PrincipalDependency = current_user,
    responses: AuthorizationResponses = API_AUTHORIZATION_RESPONSES,
) -> Callable[[Request], SessionPrincipal]:
    """Return a dependency requiring a projected session claim value."""
    checked_name = validate_identifier(name, field_name="claim")

    def dependency(request: Request) -> SessionPrincipal:
        principal = _required_principal(request, user_dependency, responses)
        if principal.claims.get(checked_name) == value:
            return principal
        return responses.forbidden()

    return dependency


def require_owner_or_admin(
    owner_id_getter: OwnerIdGetter,
    *,
    user_dependency: PrincipalDependency = current_user,
    responses: AuthorizationResponses = API_AUTHORIZATION_RESPONSES,
    admin_role: str = ADMIN_ROLE_NAME,
    admin_claim: str = "is_admin",
) -> Callable[[Request], SessionPrincipal]:
    """Return a dependency requiring resource ownership or admin access."""
    checked_admin_role = validate_identifier(admin_role, field_name="role_name")
    checked_admin_claim = validate_identifier(admin_claim, field_name="claim")

    def dependency(request: Request) -> SessionPrincipal:
        principal = _required_principal(request, user_dependency, responses)
        owner_id = validate_identifier(owner_id_getter(request), field_name="owner_id")
        if principal.user_id == owner_id:
            return principal
        if principal.has_role(checked_admin_role):
            return principal
        if principal.claims.get(checked_admin_claim) is True:
            return principal
        return responses.forbidden()

    return dependency


def write_current_user(
    request: Request,
    principal: SessionPrincipal,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> SessionPrincipal:
    """Write a principal into request.session."""
    return write_session_principal(_session(request), principal, key=key)


def clear_current_user(
    request: Request,
    *,
    key: str = SESSION_PRINCIPAL_KEY,
) -> None:
    """Clear the principal from request.session."""
    clear_session_principal(_session(request), key=key)


def _required_principal(
    request: Request,
    user_dependency: PrincipalDependency,
    responses: AuthorizationResponses,
) -> SessionPrincipal:
    principal = user_dependency(request)
    if principal is None:
        responses.unauthenticated()
    return principal


def _principal_has_role(
    principal: SessionPrincipal,
    role_name: str,
    *,
    projector: GrantClaimsProjector | None,
) -> bool:
    if principal.has_role(role_name):
        return True
    if projector is None:
        return False
    return role_name in projector.project(principal.user_id).roles


def _principal_has_permission(
    principal: SessionPrincipal,
    permission: Permission,
    *,
    scope: Scope | None,
    projector: GrantClaimsProjector | None,
) -> bool:
    if principal.has_permission(permission) or principal.has_permission(
        ADMIN_ACCESS_PERMISSION,
    ):
        return True
    if principal.claims.get("is_admin") is True:
        return True
    if projector is None:
        return False
    projection = projector.project(principal.user_id, scope=scope)
    return (
        permission in projection.permissions
        or projection.claims.get("is_admin") is True
    )


def _coerce_permission(permission: str | Permission) -> Permission:
    if isinstance(permission, Permission):
        return permission
    return Permission(permission)


def _session(request: Request) -> MutableMapping[str, object]:
    try:
        session = request.session
    except AssertionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SessionMiddleware is required",
        ) from exc
    return cast("MutableMapping[str, object]", session)
