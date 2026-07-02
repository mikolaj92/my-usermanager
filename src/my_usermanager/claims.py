"""Project stored grants into session principal claims."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from my_usermanager.models import Grant, Permission, Scope, User, validate_identifier
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.sessions import (
    InvalidSessionPrincipalError,
    SessionClaimValue,
    SessionPrincipal,
)

if TYPE_CHECKING:
    from my_usermanager.stores import GrantStore, RoleStore

__all__: Final[tuple[str, ...]] = (
    "ClaimMapper",
    "GrantClaims",
    "GrantClaimsContext",
    "GrantClaimsProjector",
    "max_permission_level_claim",
    "permission_claim",
    "role_claim",
)

ADMIN_ACCESS_PERMISSION: Final = Permission("admin.access")

type ClaimMapper = Callable[[GrantClaimsContext], Mapping[str, SessionClaimValue]]
type PermissionInput = str | Permission


@dataclass(frozen=True, slots=True)
class GrantClaims:
    """Projected authorization data that can be stored on a session principal."""

    user_id: str
    scope: Scope
    roles: frozenset[str]
    permissions: frozenset[Permission]
    claims: Mapping[str, SessionClaimValue]

    def __post_init__(self) -> None:
        """Validate and freeze projected data."""
        _ = validate_identifier(self.user_id, field_name="user_id")
        object.__setattr__(
            self,
            "roles",
            frozenset(
                validate_identifier(role, field_name="role_name") for role in self.roles
            ),
        )
        object.__setattr__(self, "permissions", frozenset(self.permissions))
        object.__setattr__(self, "claims", _freeze_claims(self.claims))

    def to_session_principal(
        self,
        user: User,
        *,
        extra_claims: Mapping[str, SessionClaimValue] | None = None,
    ) -> SessionPrincipal:
        """Merge the projection into a typed session principal for a local user."""
        if user.user_id != self.user_id:
            field_name = "user_id"
            reason = "must match projected grant claims"
            raise InvalidSessionPrincipalError(
                field_name,
                reason,
            )
        claims = dict(self.claims)
        if extra_claims is not None:
            claims.update(extra_claims)
        return SessionPrincipal(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            roles=self.roles,
            permissions=self.permissions,
            external_identities=user.external_identities,
            claims=claims,
        )


@dataclass(frozen=True, slots=True)
class GrantClaimsContext:
    """Inputs available to app-defined session claim mappers."""

    user_id: str
    scope: Scope
    grants: tuple[Grant, ...]
    roles: frozenset[str]
    permissions: frozenset[Permission]
    role_store: RoleStore = field(repr=False, compare=False)

    def has_role(self, role_name: str, *, scope: Scope | None = None) -> bool:
        """Return whether the user has a role grant for the requested scope."""
        checked = validate_identifier(role_name, field_name="role_name")
        target_scope = self.scope if scope is None else scope
        return any(
            grant.role_name == checked and grant.scope.allows(target_scope)
            for grant in self.grants
        )

    def has_permission(
        self,
        permission: PermissionInput,
        *,
        scope: Scope | None = None,
    ) -> bool:
        """Return whether the user's grants allow a permission at a scope."""
        checked = _coerce_permission(permission)
        if scope is None:
            return checked in self.permissions or self.has_permission(
                ADMIN_ACCESS_PERMISSION,
                scope=self.scope,
            )
        return any(
            grant.scope.allows(scope)
            and _grant_allows(grant, checked, role_store=self.role_store)
            for grant in self.grants
        )


@dataclass(frozen=True, slots=True)
class GrantClaimsProjector:
    """Project grants and roles into session-ready authorization data."""

    roles: RoleStore
    grants: GrantStore
    claim_mappers: tuple[ClaimMapper, ...] = ()

    def project(self, user_id: str, *, scope: Scope | None = None) -> GrantClaims:
        """Project stored grants for a user at a target scope."""
        checked_user_id = validate_identifier(user_id, field_name="user_id")
        target_scope = Scope.global_() if scope is None else scope
        grants = self.grants.list_grants_for_user(checked_user_id)
        role_names, permissions = _project_roles_permissions(
            grants,
            role_store=self.roles,
            scope=target_scope,
        )
        context = GrantClaimsContext(
            user_id=checked_user_id,
            scope=target_scope,
            grants=grants,
            roles=role_names,
            permissions=permissions,
            role_store=self.roles,
        )
        claims = dict(_default_claims(context))
        for mapper in self.claim_mappers:
            claims.update(mapper(context))
        return GrantClaims(
            user_id=checked_user_id,
            scope=target_scope,
            roles=role_names,
            permissions=permissions,
            claims=claims,
        )


def role_claim(claim_name: str, role_name: str) -> ClaimMapper:
    """Build a boolean claim mapper backed by a role grant."""
    checked_claim = validate_identifier(claim_name, field_name="claim")
    checked_role = validate_identifier(role_name, field_name="role_name")

    def mapper(context: GrantClaimsContext) -> Mapping[str, SessionClaimValue]:
        return {checked_claim: context.has_role(checked_role)}

    return mapper


def permission_claim(
    claim_name: str,
    permission: PermissionInput,
    *,
    scope: Scope | None = None,
) -> ClaimMapper:
    """Build a boolean claim mapper backed by a permission check."""
    checked_claim = validate_identifier(claim_name, field_name="claim")
    checked_permission = _coerce_permission(permission)

    def mapper(context: GrantClaimsContext) -> Mapping[str, SessionClaimValue]:
        return {checked_claim: context.has_permission(checked_permission, scope=scope)}

    return mapper


def max_permission_level_claim(
    claim_name: str,
    levels: Mapping[PermissionInput, int],
    *,
    scope: Scope | None = None,
) -> ClaimMapper:
    """Build an integer claim from the highest allowed permission level."""
    checked_claim = validate_identifier(claim_name, field_name="claim")
    checked_levels = tuple(
        (_coerce_permission(permission), level) for permission, level in levels.items()
    )

    def mapper(context: GrantClaimsContext) -> Mapping[str, SessionClaimValue]:
        level = 0
        for permission, permission_level in checked_levels:
            if context.has_permission(permission, scope=scope):
                level = max(level, permission_level)
        return {checked_claim: level}

    return mapper


def _default_claims(
    context: GrantClaimsContext,
) -> Mapping[str, SessionClaimValue]:
    return {"is_admin": context.has_permission(ADMIN_ACCESS_PERMISSION)}


def _project_roles_permissions(
    grants: tuple[Grant, ...],
    *,
    role_store: RoleStore,
    scope: Scope,
) -> tuple[frozenset[str], frozenset[Permission]]:
    roles: set[str] = set()
    permissions: set[Permission] = set()
    for grant in grants:
        if not grant.scope.allows(scope):
            continue
        if grant.permission is not None:
            permissions.add(grant.permission)
            continue
        role_name = grant.role_name
        if role_name is None:
            continue
        roles.add(role_name)
        role = role_store.get(role_name)
        if role is not None:
            permissions.update(role.permissions)
    return frozenset(roles), frozenset(permissions)


def _grant_allows(
    grant: Grant,
    permission: Permission,
    *,
    role_store: RoleStore,
) -> bool:
    if grant.permission is not None:
        return grant.permission in {permission, ADMIN_ACCESS_PERMISSION}
    role_name = grant.role_name
    if role_name is None:
        return False
    if role_name == ADMIN_ROLE_NAME:
        return True
    role = role_store.get(role_name)
    if role is None:
        return False
    return permission in role.permissions or ADMIN_ACCESS_PERMISSION in role.permissions


def _coerce_permission(permission: PermissionInput) -> Permission:
    if isinstance(permission, Permission):
        return permission
    return Permission(permission)


def _freeze_claims(
    claims: Mapping[str, SessionClaimValue],
) -> Mapping[str, SessionClaimValue]:
    frozen: dict[str, SessionClaimValue] = {}
    for key, value in claims.items():
        checked_key = validate_identifier(key, field_name="claim")
        frozen[checked_key] = _require_claim_value(
            value,
            field_name=f"claims.{checked_key}",
        )
    return MappingProxyType(frozen)


def _require_claim_value(value: object, *, field_name: str) -> SessionClaimValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise InvalidSessionPrincipalError(
        field_name,
        "must be a string, number, boolean, or null",
    )
