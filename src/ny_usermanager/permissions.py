"""Built-in permission catalogue and immutable permission registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, override

from ny_usermanager.models import (
    Permission,
    Role,
    ValidationError,
    is_valid_permission_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__: Final[tuple[str, ...]] = (
    "ADMIN_ROLE_NAME",
    "BUILTIN_PERMISSIONS",
    "BUILTIN_PERMISSION_NAMES",
    "BUILTIN_ROLES",
    "PermissionRegistry",
    "UnregisteredPermissionError",
    "is_valid_permission_name",
)

ADMIN_ROLE_NAME: Final = "admin"
BUILTIN_PERMISSION_NAMES: Final[tuple[str, ...]] = (
    "users.create",
    "users.list",
    "users.read",
    "users.update",
    "users.deactivate",
    "roles.list",
    "roles.assign",
    "permissions.grant",
    "permissions.revoke",
    "sessions.revoke",
    "audit.read",
    "admin.access",
)
BUILTIN_PERMISSIONS: Final[tuple[Permission, ...]] = tuple(
    Permission(name) for name in BUILTIN_PERMISSION_NAMES
)
BUILTIN_ROLES: Final[Mapping[str, Role]] = MappingProxyType(
    {
        ADMIN_ROLE_NAME: Role(
            name=ADMIN_ROLE_NAME,
            permissions=frozenset(BUILTIN_PERMISSIONS),
        ),
    },
)


@dataclass(frozen=True, slots=True)
class UnregisteredPermissionError(LookupError):
    """Raised when registry validation receives an unknown permission."""

    permission: Permission

    @override
    def __str__(self) -> str:
        """Return a stable human-readable registry validation message."""
        return f"unregistered permission: {self.permission.name}"


PermissionInput = str | Permission
_PermissionRuntimeInput = PermissionInput | int


def _coerce_permission(permission: _PermissionRuntimeInput) -> Permission:
    match permission:
        case str() as name:
            return Permission(name)
        case Permission() as value:
            return value
        case _:
            field_name = "permission"
            reason = "must be str or Permission"
            raise ValidationError(field_name, reason)


def _default_permissions() -> frozenset[Permission]:
    return frozenset(BUILTIN_PERMISSIONS)


@dataclass(frozen=True, slots=True)
class PermissionRegistry:
    """Immutable registry of built-in and host-app custom permissions."""

    _permissions: frozenset[Permission] = field(default_factory=_default_permissions)

    def __init__(self, custom_permissions: Iterable[PermissionInput] = ()) -> None:
        """Create a registry that always starts with all built-in permissions."""
        permissions = set(BUILTIN_PERMISSIONS)
        permissions.update(
            _coerce_permission(permission) for permission in custom_permissions
        )
        object.__setattr__(self, "_permissions", frozenset(permissions))

    def permissions(self) -> tuple[Permission, ...]:
        """Return registered permissions sorted by name for deterministic display."""
        return tuple(sorted(self._permissions, key=lambda permission: permission.name))

    def contains(self, permission: PermissionInput) -> bool:
        """Return whether the permission is registered."""
        return _coerce_permission(permission) in self._permissions

    def register(self, permission: PermissionInput) -> PermissionRegistry:
        """Return a new registry with one additional custom permission."""
        parsed = _coerce_permission(permission)
        return PermissionRegistry((*self._permissions, parsed))

    def validate_registered(self, permission: PermissionInput) -> Permission:
        """Return the permission when registered or raise a typed registry error."""
        parsed = _coerce_permission(permission)
        if parsed not in self._permissions:
            raise UnregisteredPermissionError(parsed)
        return parsed
