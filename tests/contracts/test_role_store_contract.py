from __future__ import annotations

from ny_usermanager.memory import MemoryRoleStore
from ny_usermanager.permissions import ADMIN_ROLE_NAME, BUILTIN_PERMISSION_NAMES
from ny_usermanager.stores import RoleStore


def test_role_store_protocol_is_satisfied_by_memory_store() -> None:
    # Given: the Wave 2 in-memory role store.
    stores: list[object] = []
    stores.append(MemoryRoleStore())

    # When: the store is checked against the sync protocol.
    conforms = isinstance(stores[0], RoleStore)

    # Then: callers can depend on the protocol surface only.
    assert conforms is True


def test_role_store_lists_exact_wave_one_builtins() -> None:
    # Given: a default role store backed by the Wave 1 registry.
    store = MemoryRoleStore()

    # When: roles are listed and the admin role is fetched.
    roles = store.list()
    admin = store.get(ADMIN_ROLE_NAME)
    missing = store.get("user")

    # Then: v1 exposes exactly the admin built-in and no user role.
    assert len(roles) == 1
    assert admin is not None
    assert roles == (admin,)
    assert admin.name == "admin"
    assert {permission.name for permission in admin.permissions} == set(
        BUILTIN_PERMISSION_NAMES,
    )
    assert missing is None
