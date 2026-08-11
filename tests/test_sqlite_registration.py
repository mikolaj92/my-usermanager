from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from my_usermanager import Role, User
from my_usermanager.adapters.sqlite import (
    SQLiteGrantStore,
    SQLiteUserStore,
    create_tables,
)
from my_usermanager.adapters.sqlite_registration import (
    SelfRegistrationPolicy,
    SQLiteSelfRegistrationService,
)


class Roles:
    def __init__(self) -> None:
        self._roles: dict[str, Role] = {
            name: Role(name) for name in ("admin", "client")
        }

    def get(self, role_name: str) -> Role | None:
        return self._roles.get(role_name)

    def list(self) -> tuple[Role, ...]:
        return tuple(self._roles.values())


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    create_tables(connection)
    _ = connection.execute(
        "CREATE TABLE auth_subjects (subject TEXT PRIMARY KEY)"
    )
    return connection


def _service(connection: sqlite3.Connection) -> SQLiteSelfRegistrationService:
    return SQLiteSelfRegistrationService(
        connection=connection,
        roles=Roles(),
        policy=SelfRegistrationPolicy(
            first_user_role="admin", default_user_role="client"
        ),
    )


def test_first_user_is_admin_and_later_user_gets_default_role(tmp_path: Path) -> None:
    connection = _database(tmp_path / "registration.sqlite3")
    service = _service(connection)

    def persist(subject: str) -> Callable[[sqlite3.Connection], None]:
        def save(conn: sqlite3.Connection) -> None:
            _ = conn.execute(
                "INSERT INTO auth_subjects VALUES (?)", (subject,)
            )

        return save

    first, first_role = service.register(
        user=User(user_id="alice", username="alice"),
        persist_auth=persist("alice"),
    )
    second, second_role = service.register(
        user=User(user_id="bob", username="bob"),
        persist_auth=persist("bob"),
    )

    assert (first.user_id, first_role.name) == ("alice", "admin")
    assert (second.user_id, second_role.name) == ("bob", "client")
    grants = SQLiteGrantStore(connection)
    assert grants.list_grants_for_user("alice")[0].role_name == "admin"
    assert grants.list_grants_for_user("bob")[0].role_name == "client"


def test_auth_failure_rolls_back_user_and_grant(tmp_path: Path) -> None:
    connection = _database(tmp_path / "rollback.sqlite3")
    service = _service(connection)

    def fail(_connection: sqlite3.Connection) -> None:
        message = "credential persistence failed"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="credential persistence failed"):
        _ = service.register(
            user=User(user_id="alice", username="alice"), persist_auth=fail
        )

    assert SQLiteUserStore(connection).get("alice") is None
    assert SQLiteGrantStore(connection).list_grants_for_user("alice") == ()
    assert connection.execute("SELECT * FROM auth_subjects").fetchall() == []
