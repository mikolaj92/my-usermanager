"""Public package for framework-neutral user management and authorization."""

from typing import Final

from my_usermanager.memory import (
    MemoryAuditStore,
    MemoryGrantStore,
    MemoryRoleStore,
    MemoryUserStore,
)
from my_usermanager.models import (
    AuditEvent,
    ExternalIdentity,
    Grant,
    Permission,
    Role,
    Scope,
    User,
    ValidationError,
    validate_identifier,
    validate_permission_name,
)
from my_usermanager.permissions import (
    ADMIN_ROLE_NAME,
    BUILTIN_PERMISSION_NAMES,
    BUILTIN_PERMISSIONS,
    BUILTIN_ROLES,
    PermissionRegistry,
    UnregisteredPermissionError,
    is_valid_permission_name,
)
from my_usermanager.stores import (
    AuditFilters,
    AuditStore,
    DuplicateAuditEventError,
    DuplicateGrantError,
    DuplicateUserError,
    GrantNotFoundError,
    GrantStore,
    InvalidPageError,
    RoleStore,
    SessionRevoker,
    StoreError,
    UserNotFoundError,
    UserQuery,
    UserStore,
)

__version__: Final = "0.1.0"

__all__: Final = (
    "ADMIN_ROLE_NAME",
    "BUILTIN_PERMISSIONS",
    "BUILTIN_PERMISSION_NAMES",
    "BUILTIN_ROLES",
    "AuditEvent",
    "AuditFilters",
    "AuditStore",
    "DuplicateAuditEventError",
    "DuplicateGrantError",
    "DuplicateUserError",
    "ExternalIdentity",
    "Grant",
    "GrantNotFoundError",
    "GrantStore",
    "InvalidPageError",
    "MemoryAuditStore",
    "MemoryGrantStore",
    "MemoryRoleStore",
    "MemoryUserStore",
    "Permission",
    "PermissionRegistry",
    "Role",
    "RoleStore",
    "Scope",
    "SessionRevoker",
    "StoreError",
    "UnregisteredPermissionError",
    "User",
    "UserNotFoundError",
    "UserQuery",
    "UserStore",
    "ValidationError",
    "__version__",
    "is_valid_permission_name",
    "validate_identifier",
    "validate_permission_name",
)
