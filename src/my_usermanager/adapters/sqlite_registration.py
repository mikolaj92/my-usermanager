"""Atomic SQLite self-registration with host-owned authentication persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from my_usermanager.adapters.sqlite import SQLiteGrantStore, SQLiteUserStore
from my_usermanager.models import Role, Scope, User

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from my_usermanager.stores import RoleStore


class SelfRegistrationError(ValueError):
    """Raised when self-registration policy or transaction state is invalid."""


@dataclass(frozen=True, slots=True)
class SelfRegistrationPolicy:
    """Host role policy for a newly created account."""

    first_user_role: str
    default_user_role: str


@dataclass(frozen=True, slots=True)
class SQLiteSelfRegistrationService:
    """Create one user, authentication identity, and initial grant atomically.

    The caller supplies ``persist_auth`` so authentication credentials can be
    written on this same SQLite connection without making my-usermanager own
    passkeys or another authentication mechanism.
    """

    connection: sqlite3.Connection
    roles: RoleStore
    policy: SelfRegistrationPolicy

    def register(
        self,
        *,
        user: User,
        persist_auth: Callable[[sqlite3.Connection], None],
    ) -> tuple[User, Role]:
        """Register a fresh active user and return its host-approved role."""
        self._validate(user)
        try:
            _ = self.connection.execute("BEGIN IMMEDIATE")
            users = SQLiteUserStore(self.connection, transaction_mode="external")
            grants = SQLiteGrantStore(self.connection, transaction_mode="external")
            role = self._role_for_registration(users)
            created = users.create(user)
            persist_auth(self.connection)
            _ = grants.add_role_grant(
                created.user_id, role.name, Scope.global_()
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        else:
            return created, role

    def _validate(self, user: User) -> None:
        if not user.is_active:
            message = "self-registration requires an active user"
            raise SelfRegistrationError(message)
        if self.connection.in_transaction:
            message = "self-registration owns its SQLite transaction"
            raise SelfRegistrationError(message)

    def _role_for_registration(self, users: SQLiteUserStore) -> Role:
        role_name = (
            self.policy.first_user_role
            if users.count_active() == 0
            else self.policy.default_user_role
        )
        role = self.roles.get(role_name)
        if role is None:
            message = f"unknown self-registration role: {role_name}"
            raise SelfRegistrationError(message)
        return role
