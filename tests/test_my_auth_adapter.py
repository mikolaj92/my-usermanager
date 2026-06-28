from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from textwrap import dedent
from typing import ClassVar, NoReturn

import pytest

from my_usermanager.adapters import my_auth as my_auth_adapter
from my_usermanager.adapters.my_auth import (
    MY_AUTH_PROVIDER,
    MyAuthSubjectAdapter,
    passkey_user_to_authenticated_subject,
)
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore
from my_usermanager.models import ExternalIdentity
from my_usermanager.permissions import PermissionRegistry
from my_usermanager.subjects import derive_local_user_id


@dataclass(frozen=True, slots=True)
class FakePasskeyUser:
    user_id: str
    user_handle: bytes
    name: str
    display_name: str | None


class FakeMyAuthModule:
    PasskeyUser: ClassVar[type[FakePasskeyUser]] = FakePasskeyUser


def raise_missing_my_auth(name: str, _package: str | None = None) -> NoReturn:
    raise ModuleNotFoundError(name=name)


def raise_missing_my_auth_runtime_dependency(
    _name: str,
    _package: str | None = None,
) -> NoReturn:
    raise ModuleNotFoundError(name="webauthn")


def import_fake_my_auth(name: str, _package: str | None = None) -> FakeMyAuthModule:
    if name != "my_auth":
        raise ModuleNotFoundError(name=name)
    return FakeMyAuthModule()


def test_core_import_and_adapter_package_import_do_not_load_optional_dependencies() -> (
    None
):
    # Given: a fresh interpreter that imports the core package and adapter package.
    import_check = dedent(
        """
        import sys
        import my_usermanager
        import my_usermanager.adapters

        assert my_usermanager.__version__ == "0.1.0"
        assert "my_auth" not in sys.modules
        assert "fastapi" not in sys.modules
        assert "pydantic" not in sys.modules
        """,
    )

    # When: the imports execute in isolation.
    completed = subprocess.run(
        [sys.executable, "-c", import_check],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Then: optional dependencies are not imported as side effects.
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_my_auth_dependency_guard_reports_actionable_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the optional adapter is imported while my_auth import is unavailable.
    monkeypatch.setattr(my_auth_adapter, "import_module", raise_missing_my_auth)

    # When / Then: requiring the concrete dependency raises a typed, actionable error.
    with pytest.raises(
        my_auth_adapter.MissingMyAuthDependencyError,
        match=r"my-usermanager\[myauth\]",
    ) as exc_info:
        _ = my_auth_adapter.require_my_auth()
    assert exc_info.value.import_name == "my_auth"
    assert "github.com/mikolaj92/my-auth" in str(exc_info.value)


def test_my_auth_dependency_guard_reports_actionable_runtime_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: my-auth itself cannot import one of its runtime dependencies.
    monkeypatch.setattr(
        my_auth_adapter,
        "import_module",
        raise_missing_my_auth_runtime_dependency,
    )

    # When / Then: the guard still raises the adapter's actionable typed error.
    with pytest.raises(
        my_auth_adapter.MissingMyAuthDependencyError,
        match=r"my-usermanager\[myauth\]",
    ) as exc_info:
        _ = my_auth_adapter.require_my_auth()
    assert exc_info.value.import_name == "my_auth"


def test_passkey_user_maps_to_my_auth_external_identity() -> None:
    # Given: a PasskeyUser-shaped value from my-auth.
    passkey_user = FakePasskeyUser(
        user_id="passkey_user_123",
        user_handle=b"ignored-primary-identity",
        name="Passkey User",
        display_name="Passkey Display",
    )

    # When: the adapter maps it to the dependency-free subject seam.
    subject = passkey_user_to_authenticated_subject(passkey_user)

    # Then: the passkey user_id is the provider subject and external identity subject.
    assert subject.provider == MY_AUTH_PROVIDER
    assert subject.subject == "passkey_user_123"
    assert subject.external_identity() == ExternalIdentity(
        provider="my-auth",
        subject="passkey_user_123",
    )


def test_valid_passkey_user_id_remains_canonical_local_user_id() -> None:
    # Given: a PasskeyUser-shaped value with a valid my-usermanager identifier.
    passkey_user = FakePasskeyUser(
        user_id="passkey_user_123",
        user_handle=b"opaque-handle",
        name="Passkey User",
        display_name=None,
    )

    # When: the subject is mapped.
    subject = passkey_user_to_authenticated_subject(passkey_user)

    # Then: the local user_id and username stay canonical.
    assert subject.user_id == "passkey_user_123"
    assert subject.username == "passkey_user_123"


def test_invalid_passkey_user_id_falls_back_and_preserves_external_subject() -> None:
    # Given: a provider user_id that is not a valid local identifier.
    external_user_id = "passkey user 123"
    passkey_user = FakePasskeyUser(
        user_id=external_user_id,
        user_handle=b"opaque-handle",
        name="Passkey User",
        display_name=None,
    )

    # When: the subject is mapped.
    subject = passkey_user_to_authenticated_subject(passkey_user)

    # Then: the local id is deterministic and the original external subject remains.
    assert subject.user_id == derive_local_user_id(
        provider=MY_AUTH_PROVIDER,
        subject=external_user_id,
    )
    assert subject.user_id == "external_my_auth_16810a306529ad76c5268210"
    assert subject.subject == external_user_id
    assert subject.external_identity() == ExternalIdentity(
        provider="my-auth",
        subject=external_user_id,
    )


def test_display_name_uses_passkey_display_name_or_name_without_splitting() -> None:
    # Given: passkey users with explicit and fallback display names.
    explicit_display_name_user = FakePasskeyUser(
        user_id="user_123",
        user_handle=b"handle-a",
        name="Ignored Name",
        display_name="Chosen Display",
    )
    fallback_name_user = FakePasskeyUser(
        user_id="user_456",
        user_handle=b"handle-b",
        name="Ada Lovelace",
        display_name=None,
    )

    # When: both users are mapped.
    explicit_subject = passkey_user_to_authenticated_subject(explicit_display_name_user)
    fallback_subject = passkey_user_to_authenticated_subject(fallback_name_user)

    # Then: display names follow my-auth fields and first/last names are not guessed.
    assert explicit_subject.display_name == "Chosen Display"
    assert fallback_subject.display_name == "Ada Lovelace"
    assert fallback_subject.first_name is None
    assert fallback_subject.last_name is None


def test_user_handle_does_not_affect_primary_identity_mapping() -> None:
    # Given: two PasskeyUser-shaped values with identical user_id and different handles.
    first = FakePasskeyUser(
        user_id="user_123",
        user_handle=b"first-handle",
        name="Passkey User",
        display_name=None,
    )
    second = FakePasskeyUser(
        user_id="user_123",
        user_handle=b"second-handle",
        name="Passkey User",
        display_name=None,
    )

    # When: both values are mapped.
    first_subject = passkey_user_to_authenticated_subject(first)
    second_subject = passkey_user_to_authenticated_subject(second)

    # Then: the user_handle is not used as primary identity material.
    assert first_subject == second_subject


def test_mapping_does_not_mutate_grants_roles_or_permissions() -> None:
    # Given: authorization stores that should not be touched by identity mapping.
    grants = MemoryGrantStore()
    roles = MemoryRoleStore()
    registry = PermissionRegistry()
    before_roles = roles.list()
    before_permissions = registry.permissions()
    passkey_user = FakePasskeyUser(
        user_id="user_123",
        user_handle=b"opaque-handle",
        name="Passkey User",
        display_name=None,
    )

    # When: the passkey user is mapped.
    subject = passkey_user_to_authenticated_subject(passkey_user)

    # Then: no roles, grants, permissions, or admin access are created.
    assert grants.list_grants_for_user(subject.user_id) == ()
    assert roles.list() == before_roles
    assert registry.permissions() == before_permissions
    assert subject.first_name is None
    assert subject.last_name is None


def test_concrete_subject_adapter_requires_my_auth_before_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: construction of the concrete adapter while my_auth is unavailable.
    monkeypatch.setattr(my_auth_adapter, "import_module", raise_missing_my_auth)

    # When / Then: creating the concrete adapter fails with the typed import error.
    with pytest.raises(my_auth_adapter.MissingMyAuthDependencyError):
        _ = MyAuthSubjectAdapter()


def test_require_my_auth_accepts_module_with_passkey_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a simulated my_auth module exposing PasskeyUser.
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_my_auth)

    # When: the optional dependency guard is checked.
    passkey_user_type = my_auth_adapter.require_my_auth()

    # Then: the concrete passkey user type is returned for optional adapter code.
    assert passkey_user_type is FakePasskeyUser
