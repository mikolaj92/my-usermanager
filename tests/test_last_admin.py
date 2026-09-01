# pyright: reportUnusedCallResult=false
from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING

import pytest

from my_usermanager import (
    AdminAccessPredicate,
    GrantAdminService,
    LastAdministratorError,
    MemoryGrantStore,
    MemoryUserStore,
    Permission,
    Scope,
    UnsafeGrantMutationError,
    User,
    UserManager,
    count_active_administrators,
)
from my_usermanager.adapters.sqlite import (
    ImmediateTransaction,
    SQLiteGrantStore,
    SQLiteUserStore,
    create_tables,
    immediate_transaction,
)
from my_usermanager.memory import MemoryRoleStore

if TYPE_CHECKING:
    from pathlib import Path


def _memory() -> tuple[
    UserManager,
    GrantAdminService,
    MemoryUserStore,
    MemoryGrantStore,
]:
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    manager = UserManager(users=users, roles=roles, grants=grants)
    admin = GrantAdminService(users=users, roles=roles, grants=grants)
    return manager, admin, users, grants


def test_disable_rejects_final_active_administrator() -> None:
    manager, admin, users, _grants = _memory()
    users.create(User(user_id="admin", username="admin"))
    admin.grant_role(actor_id="admin", target_user_id="admin", role_name="admin")

    with pytest.raises(LastAdministratorError, match="last active admin"):
        manager.transition_account(user_id="admin", status="disabled")

    stored = users.get("admin")
    assert stored is not None
    assert stored.is_active


def test_soft_delete_rejects_final_active_administrator() -> None:
    manager, admin, users, _grants = _memory()
    users.create(User(user_id="admin", username="admin"))
    admin.grant_role(actor_id="admin", target_user_id="admin", role_name="admin")

    with pytest.raises(LastAdministratorError, match="action=delete"):
        manager.soft_delete_account(user_id="admin")


def test_non_final_administrator_can_be_disabled() -> None:
    manager, admin, users, grants = _memory()
    users.create(User(user_id="admin-a", username="admin-a"))
    users.create(User(user_id="admin-b", username="admin-b"))
    admin.grant_role(actor_id="admin-a", target_user_id="admin-a", role_name="admin")
    admin.grant_role(actor_id="admin-a", target_user_id="admin-b", role_name="admin")

    disabled = manager.transition_account(user_id="admin-b", status="disabled")

    assert disabled.disabled
    assert (
        count_active_administrators(
            users=users,
            grants=grants,
            roles=MemoryRoleStore(),
        )
        == 1
    )


def test_pending_and_disabled_admins_do_not_count() -> None:
    manager, admin, users, grants = _memory()
    users.create(User(user_id="active", username="active"))
    users.create(User(user_id="pending", username="pending", status="pending"))
    users.create(
        User(user_id="disabled", username="disabled", status="disabled", disabled=True)
    )
    admin.grant_role(actor_id="active", target_user_id="active", role_name="admin")
    admin.grant_role(actor_id="active", target_user_id="pending", role_name="admin")
    admin.grant_permission(
        actor_id="active",
        target_user_id="disabled",
        permission=Permission("admin.access"),
    )

    assert (
        count_active_administrators(users=users, grants=grants, roles=MemoryRoleStore())
        == 1
    )
    with pytest.raises(LastAdministratorError):
        manager.transition_account(user_id="active", status="disabled")
    # Pending holders of admin grants may still be disabled: they are not active.
    assert manager.transition_account(user_id="pending", status="disabled").disabled


def test_scoped_admin_grants_do_not_qualify() -> None:
    manager, admin, users, grants = _memory()
    users.create(User(user_id="scoped", username="scoped"))
    users.create(User(user_id="global", username="global"))
    admin.grant_permission(
        actor_id="global",
        target_user_id="scoped",
        permission=Permission("admin.access"),
        scope=Scope.scoped("org", "acme"),
    )
    admin.grant_role(actor_id="global", target_user_id="global", role_name="admin")

    assert (
        count_active_administrators(users=users, grants=grants, roles=MemoryRoleStore())
        == 1
    )
    assert manager.transition_account(user_id="scoped", status="disabled").disabled


def test_host_admin_predicate_customizes_qualifying_role() -> None:
    users = MemoryUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    predicate = AdminAccessPredicate(role_names=frozenset({"operator"}))
    manager = UserManager(
        users=users,
        roles=roles,
        grants=grants,
        admin_predicate=predicate,
    )
    admin = GrantAdminService(
        users=users,
        roles=roles,
        grants=grants,
        admin_predicate=predicate,
    )
    users.create(User(user_id="ops", username="ops"))
    users.create(User(user_id="classic", username="classic"))
    # Built-in admin role is not administrative under the host predicate.
    grants.add_role_grant("classic", "admin", Scope.global_())
    grants.add_role_grant("ops", "operator", Scope.global_())

    assert manager.transition_account(user_id="classic", status="disabled").disabled
    with pytest.raises(LastAdministratorError):
        manager.transition_account(user_id="ops", status="disabled")
    with pytest.raises(UnsafeGrantMutationError, match="last active admin"):
        admin.revoke_role(
            actor_id="classic",
            target_user_id="ops",
            role_name="operator",
        )


def test_role_and_permission_revoke_still_protect_last_admin() -> None:
    _manager, admin, users, grants = _memory()
    users.create(User(user_id="admin", username="admin"))
    admin.grant_role(actor_id="admin", target_user_id="admin", role_name="admin")

    with pytest.raises(UnsafeGrantMutationError, match="last active admin"):
        admin.revoke_role(
            actor_id="operator",
            target_user_id="admin",
            role_name="admin",
        )

    users.create(User(user_id="perm-admin", username="perm-admin"))
    admin.grant_permission(
        actor_id="admin",
        target_user_id="perm-admin",
        permission=Permission("admin.access"),
    )
    # A non-final role revoke succeeds while another active admin remains.
    admin.revoke_role(
        actor_id="perm-admin",
        target_user_id="admin",
        role_name="admin",
    )
    with pytest.raises(UnsafeGrantMutationError, match="last active admin"):
        admin.revoke_permission(
            actor_id="operator",
            target_user_id="perm-admin",
            permission=Permission("admin.access"),
        )
    assert grants.list_grants_for_user("perm-admin")


def _sqlite_atomic(connection: sqlite3.Connection) -> ImmediateTransaction:
    return immediate_transaction(connection)


def _sqlite_bundle(
    path: Path,
) -> tuple[sqlite3.Connection, GrantAdminService]:
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    create_tables(connection)
    users = SQLiteUserStore(connection, transaction_mode="external")
    grants = SQLiteGrantStore(connection, transaction_mode="external")
    roles = MemoryRoleStore()
    admin = GrantAdminService(
        users=users,
        roles=roles,
        grants=grants,
        atomic=lambda: _sqlite_atomic(connection),
    )
    return connection, admin


def _seed_two_admins(path: Path) -> None:
    setup, admin = _sqlite_bundle(path)
    try:
        with immediate_transaction(setup):
            users = SQLiteUserStore(setup, transaction_mode="external")
            users.create(User(user_id="admin-a", username="admin-a"))
            users.create(User(user_id="admin-b", username="admin-b"))
            admin.grant_role(
                actor_id="admin-a", target_user_id="admin-a", role_name="admin"
            )
            admin.grant_role(
                actor_id="admin-a", target_user_id="admin-b", role_name="admin"
            )
    finally:
        setup.close()


def _assert_one_active_admin(database: Path) -> None:
    check = sqlite3.connect(database, isolation_level=None)
    try:
        assert (
            count_active_administrators(
                users=SQLiteUserStore(check),
                grants=SQLiteGrantStore(check),
                roles=MemoryRoleStore(),
            )
            == 1
        )
    finally:
        check.close()


def test_concurrent_disable_leaves_one_active_admin(tmp_path: Path) -> None:
    database = tmp_path / "last-admin-disable.sqlite3"
    _seed_two_admins(database)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def disable(user_id: str) -> None:
        connection = sqlite3.connect(database, timeout=5, isolation_level=None)
        try:
            local = UserManager(
                users=SQLiteUserStore(connection, transaction_mode="external"),
                roles=MemoryRoleStore(),
                grants=SQLiteGrantStore(connection, transaction_mode="external"),
                atomic=lambda: _sqlite_atomic(connection),
            )
            barrier.wait()
            try:
                local.transition_account(user_id=user_id, status="disabled")
                with lock:
                    outcomes.append("ok")
            except LastAdministratorError:
                with lock:
                    outcomes.append("rejected")
        finally:
            connection.close()

    threads = [
        threading.Thread(target=disable, args=("admin-a",)),
        threading.Thread(target=disable, args=("admin-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["ok", "rejected"]
    _assert_one_active_admin(database)


def test_concurrent_role_revoke_leaves_one_active_admin(tmp_path: Path) -> None:
    database = tmp_path / "last-admin-revoke.sqlite3"
    _seed_two_admins(database)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def revoke(target_user_id: str) -> None:
        connection = sqlite3.connect(database, timeout=5, isolation_level=None)
        try:
            service = GrantAdminService(
                users=SQLiteUserStore(connection, transaction_mode="external"),
                roles=MemoryRoleStore(),
                grants=SQLiteGrantStore(connection, transaction_mode="external"),
                atomic=lambda: _sqlite_atomic(connection),
            )
            barrier.wait()
            try:
                service.revoke_role(
                    actor_id="operator",
                    target_user_id=target_user_id,
                    role_name="admin",
                )
                with lock:
                    outcomes.append("ok")
            except UnsafeGrantMutationError:
                with lock:
                    outcomes.append("rejected")
        finally:
            connection.close()

    threads = [
        threading.Thread(target=revoke, args=("admin-a",)),
        threading.Thread(target=revoke, args=("admin-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["ok", "rejected"]
    _assert_one_active_admin(database)


def test_migrated_active_admin_remains_protected(tmp_path: Path) -> None:
    # Migration edge: active admin stays protected; disabled admins do not count.
    database = tmp_path / "migrated-admin.sqlite3"
    connection = sqlite3.connect(database, isolation_level=None)
    create_tables(connection)
    try:
        users = SQLiteUserStore(connection, transaction_mode="external")
        grants = SQLiteGrantStore(connection, transaction_mode="external")
        with immediate_transaction(connection):
            users.create(User(user_id="legacy-admin", username="legacy-admin"))
            grants.add_role_grant("legacy-admin", "admin", Scope.global_())
            users.create(
                User(
                    user_id="legacy-off",
                    username="legacy-off",
                    status="disabled",
                    disabled=True,
                )
            )
            grants.add_role_grant("legacy-off", "admin", Scope.global_())

        manager = UserManager(
            users=users,
            roles=MemoryRoleStore(),
            grants=grants,
            atomic=lambda: _sqlite_atomic(connection),
        )
        with pytest.raises(LastAdministratorError):
            manager.transition_account(user_id="legacy-admin", status="disabled")
        stored = users.get("legacy-admin")
        assert stored is not None
        assert stored.is_active
    finally:
        connection.close()
