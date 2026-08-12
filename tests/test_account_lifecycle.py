# pyright: reportImplicitStringConcatenation=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false

from __future__ import annotations

import sqlite3

import pytest

from my_usermanager import (
    AccountTransitionError,
    ExternalIdentity,
    MemoryGrantStore,
    MemoryRoleStore,
    MemoryUserStore,
    User,
    UserManager,
)
from my_usermanager.adapters.my_auth_fastapi import (
    PasskeyUserProfile,
    build_get_auth_user,
)
from my_usermanager.adapters.sqlite import (
    SQLiteUserStore,
    create_tables,
    inspect_sqlite_schema,
    migrate_sqlite_schema,
)


def _manager(users: MemoryUserStore) -> UserManager:
    return UserManager(users, MemoryRoleStore(), MemoryGrantStore())


def test_pending_activation_disable_and_reenable_are_explicit() -> None:
    users = MemoryUserStore()
    users.create(User("user", "user", status="pending"))
    manager = _manager(users)

    assert users.count_active() == 0
    assert manager.transition_account(user_id="user", status="active").is_active
    assert manager.transition_account(user_id="user", status="disabled").disabled
    assert manager.transition_account(user_id="user", status="active").is_active
    with pytest.raises(AccountTransitionError):
        manager.transition_account(user_id="user", status="pending")


def test_soft_delete_is_terminal_and_hard_delete_requires_it() -> None:
    users = MemoryUserStore()
    users.create(User("user", "user"))
    users.create(User("active", "active"))
    manager = _manager(users)

    with pytest.raises(AccountTransitionError):
        manager.hard_delete_account(user_id="active")
    deleted = manager.soft_delete_account(user_id="user")
    assert deleted.status == "deleted"
    assert deleted.disabled
    with pytest.raises(AccountTransitionError):
        manager.transition_account(user_id="user", status="active")
    manager.hard_delete_account(user_id="user")
    assert users.get("user") is None


def test_sqlite_hard_delete_cascades_account_owned_records() -> None:
    connection = sqlite3.connect(":memory:")
    create_tables(connection)
    users = SQLiteUserStore(connection)
    users.create(User("user", "user", status="deleted"))
    connection.execute(
        "INSERT INTO um_external_identities(provider, subject, user_id) "
        "VALUES ('my-auth', 'subject', 'user')"
    )
    connection.execute(
        "INSERT INTO um_grants(user_id, role_name, permission_name, "
        "scope_type, scope_id) VALUES ('user', 'admin', '', '', '')"
    )
    connection.commit()

    UserManager(users, MemoryRoleStore(), MemoryGrantStore()).hard_delete_account(
        user_id="user"
    )

    assert connection.execute("SELECT COUNT(*) FROM um_users").fetchone() == (0,)
    assert connection.execute(
        "SELECT COUNT(*) FROM um_external_identities"
    ).fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM um_grants").fetchone() == (0,)
    connection.close()


def test_pending_and_disabled_users_cannot_authenticate() -> None:
    identity = ExternalIdentity("my-auth", "subject")

    class IdentityStore(MemoryUserStore):
        def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
            return next(
                (
                    user
                    for user in self._users.values()
                    if identity in user.external_identities
                ),
                None,
            )

        def link_external_identity(
            self, *, user_id: str, identity: ExternalIdentity
        ) -> User:
            user = self.get(user_id)
            if user is None:
                raise AssertionError(user_id)
            return self.update(
                User(
                    user.user_id,
                    user.username,
                    user.external_identities | frozenset({identity}),
                    status=user.status,
                )
            )

    users = IdentityStore()
    users.create(User("user", "user", frozenset({identity}), status="pending"))
    resolver = build_get_auth_user(
        users,
        lambda user: PasskeyUserProfile("subject", b"handle", user.username),
    )
    assert resolver("subject") is None
    users.update(User("user", "user", frozenset({identity}), status="active"))
    assert resolver("subject") is not None
    users.update(
        User("user", "user", frozenset({identity}), status="disabled", disabled=True)
    )
    assert resolver("subject") is None
    users.update(
        User("user", "user", frozenset({identity}), status="deleted", disabled=True)
    )
    assert resolver("subject") is None


def test_v3_migration_preserves_users_identities_and_grants() -> None:
    connection = sqlite3.connect(":memory:")
    create_tables(connection)
    connection.execute("UPDATE um_schema_version SET version=3")
    connection.commit()
    connection.execute("PRAGMA legacy_alter_table=ON")
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("ALTER TABLE um_users RENAME TO um_users_v4")
    connection.execute(
        "CREATE TABLE um_users (user_id TEXT PRIMARY KEY, username TEXT NOT NULL, "
        "first_name TEXT, last_name TEXT, display_name TEXT, email TEXT, "
        "birth_date TEXT, gender TEXT, disabled INTEGER NOT NULL DEFAULT 0, "
        "system INTEGER NOT NULL DEFAULT 0, "
        "scope_type TEXT, scope_id TEXT)"
    )
    connection.execute(
        "INSERT INTO um_users SELECT user_id,username,first_name,last_name,"
        "display_name,email,"
        "birth_date,gender,disabled,system,scope_type,scope_id FROM um_users_v4"
    )
    connection.execute("DROP TABLE um_users_v4")
    connection.execute(
        "CREATE UNIQUE INDEX um_users_username_ci ON um_users(lower(username))"
    )
    connection.execute(
        "INSERT INTO um_users(user_id,username) VALUES('active','active')"
    )
    connection.execute(
        "INSERT INTO um_users(user_id,username,disabled) VALUES('off','off',1)"
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")
    assert inspect_sqlite_schema(connection) == "v3"

    migrate_sqlite_schema(connection)

    store = SQLiteUserStore(connection)
    assert store.get("active").status == "active"
    assert store.get("off").status == "disabled"
    assert inspect_sqlite_schema(connection) == "current"
    connection.close()
