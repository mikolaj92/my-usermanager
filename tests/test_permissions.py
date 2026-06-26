import subprocess
import sys
from textwrap import dedent

import pytest

from ny_usermanager.models import Permission, ValidationError
from ny_usermanager.permissions import (
    ADMIN_ROLE_NAME,
    BUILTIN_PERMISSION_NAMES,
    BUILTIN_ROLES,
    PermissionRegistry,
    UnregisteredPermissionError,
    is_valid_permission_name,
)

REQUIRED_BUILTIN_PERMISSIONS = (
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


def _run_registry_runtime_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_builtin_permission_names_are_exactly_the_wave_one_catalogue() -> None:
    # Given: the Wave 1 built-in permission catalogue.

    # When: the public names are inspected.
    names = BUILTIN_PERMISSION_NAMES

    # Then: the catalogue is exact, ordered, and has no extras.
    assert names == REQUIRED_BUILTIN_PERMISSIONS
    assert len(names) == 12
    assert len(set(names)) == len(names)


def test_default_registry_contains_only_registered_builtins_when_created() -> None:
    # Given: a default permission registry.
    registry = PermissionRegistry()

    # When: registered permissions are listed.
    registered_names = {permission.name for permission in registry.permissions()}

    # Then: every built-in is registered by default and unknown names are absent.
    assert registered_names == set(REQUIRED_BUILTIN_PERMISSIONS)
    assert registry.contains("users.create") is True
    assert registry.contains(Permission("admin.access")) is True
    assert registry.contains("reports.read") is False


def test_admin_role_contains_all_builtins_and_no_user_role_exists() -> None:
    # Given: the built-in role catalogue.
    admin_role = BUILTIN_ROLES[ADMIN_ROLE_NAME]

    # When: role permissions and role names are inspected.
    admin_permission_names = {permission.name for permission in admin_role.permissions}

    # Then: admin has every built-in and v1 has no built-in user role.
    assert ADMIN_ROLE_NAME == "admin"
    assert admin_permission_names == set(REQUIRED_BUILTIN_PERMISSIONS)
    assert "user" not in BUILTIN_ROLES
    assert set(BUILTIN_ROLES) == {"admin"}


@pytest.mark.parametrize(
    "permission_name",
    ["reports.read", "billing.invoices_list", "tenant_123.reports.read"],
)
def test_permission_name_validation_accepts_namespaced_lowercase_values(
    permission_name: str,
) -> None:
    # Given: a valid custom permission name.

    # When: the name is validated and wrapped.
    is_valid = is_valid_permission_name(permission_name)
    permission = Permission(permission_name)

    # Then: valid custom permission strings are representable.
    assert is_valid is True
    assert permission.name == permission_name


@pytest.mark.parametrize(
    "permission_name",
    [
        "",
        "users",
        "Users.Read",
        "users..read",
        ".users.read",
        "users.read ",
        "users read",
    ],
)
def test_permission_name_validation_rejects_malformed_values(
    permission_name: str,
) -> None:
    # Given: a malformed permission name.

    # When / Then: validation reports it as malformed.
    assert is_valid_permission_name(permission_name) is False
    with pytest.raises(ValidationError, match="permission"):
        _ = Permission(permission_name)


def test_registry_registers_custom_permissions_without_mutating_original() -> None:
    # Given: a default immutable registry and a valid custom permission string.
    registry = PermissionRegistry()
    custom = Permission("reports.read")

    # When: the custom permission is registered.
    updated = registry.register("reports.read")

    # Then: only the returned registry contains the custom permission.
    assert registry.contains(custom) is False
    assert updated is not registry
    assert updated.contains(custom) is True
    assert updated.validate_registered("reports.read") == custom
    assert registry.contains(custom) is False


def test_registry_validation_marks_unregistered_permission_grants_invalid() -> None:
    # Given: a default registry and an app-specific permission not yet registered.
    registry = PermissionRegistry()
    custom = Permission("reports.export")

    # When / Then: registry validation can reject an attempted grant.
    with pytest.raises(UnregisteredPermissionError, match=r"reports\.export"):
        _ = registry.validate_registered(custom)


def test_registry_rejects_malformed_custom_permission_when_registering() -> None:
    # Given: a default registry.
    registry = PermissionRegistry()

    # When / Then: malformed custom strings are rejected before registration.
    with pytest.raises(ValidationError, match="permission"):
        _ = registry.register("reports export")


def test_registry_register_rejects_unsupported_runtime_type_when_int_passed() -> None:
    # Given: a runtime caller bypassing static typing with register(123).
    script = dedent(
        """
        from ny_usermanager.models import ValidationError
        from ny_usermanager.permissions import PermissionRegistry

        registry = PermissionRegistry()
        before = registry.permissions()

        try:
            registry.register(123)
        except ValidationError as exc:
            assert str(exc) == "permission: must be str or Permission"
        else:
            raise AssertionError("register accepted unsupported int input")

        assert registry.permissions() == before
        """,
    )

    # When: the unsupported input is executed at runtime.
    completed = _run_registry_runtime_script(script)

    # Then: it is rejected without mutating the original registry.
    assert completed.returncode == 0, completed.stderr


def test_registry_validate_rejects_unsupported_runtime_type_when_int_passed() -> None:
    # Given: a runtime caller bypassing static typing with validate_registered(123).
    script = dedent(
        """
        from ny_usermanager.models import ValidationError
        from ny_usermanager.permissions import PermissionRegistry

        registry = PermissionRegistry()

        try:
            registry.validate_registered(123)
        except ValidationError as exc:
            assert str(exc) == "permission: must be str or Permission"
        else:
            raise AssertionError("validate_registered accepted unsupported int input")
        """,
    )

    # When: the unsupported input is executed at runtime.
    completed = _run_registry_runtime_script(script)

    # Then: it is rejected before an unsafe UnregisteredPermissionError is built.
    assert completed.returncode == 0, completed.stderr


def test_registry_contains_rejects_unsupported_runtime_type_when_int_passed() -> None:
    # Given: a runtime caller bypassing static typing with contains(123).
    script = dedent(
        """
        from ny_usermanager.models import ValidationError
        from ny_usermanager.permissions import PermissionRegistry

        registry = PermissionRegistry()

        try:
            registry.contains(123)
        except ValidationError as exc:
            assert str(exc) == "permission: must be str or Permission"
        else:
            raise AssertionError("contains accepted unsupported int input")
        """,
    )

    # When: the unsupported input is executed at runtime.
    completed = _run_registry_runtime_script(script)

    # Then: membership checks use the same deterministic runtime rejection.
    assert completed.returncode == 0, completed.stderr
