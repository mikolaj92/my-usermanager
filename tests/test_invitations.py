from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from my_usermanager import (
    ExternalIdentity,
    MemoryAuditStore,
    MemoryGrantStore,
    MemoryRoleStore,
    Permission,
    Scope,
    User,
    UserManager,
)
from my_usermanager.invitations import (
    InvitationActivation,
    InvitationError,
    InvitationGrant,
    InvitationService,
    IssuedEnrollment,
    MemoryInvitationStore,
)
from my_usermanager.stores import AuditFilters

_NOW = datetime(2026, 8, 11, tzinfo=UTC)


class IdentityUserStore:
    def __init__(self) -> None:
        from my_usermanager.memory import MemoryUserStore

        self._users = MemoryUserStore()

    def create(self, user: User) -> User:
        return self._users.create(user)

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_by_username(self, username: str) -> User | None:
        return self._users.get_by_username(username)

    def update(self, user: User) -> User:
        return self._users.update(user)

    def list(self, **kwargs):
        return self._users.list(**kwargs)

    def count_active(self) -> int:
        return self._users.count_active()

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        return next(
            (
                user
                for user in self._users._users.values()
                if identity in user.external_identities
            ),
            None,
        )

    def link_external_identity(
        self, *, user_id: str, identity: ExternalIdentity
    ) -> User:
        existing = self.resolve_external_identity(identity)
        if existing is not None and existing.user_id != user_id:
            raise ValueError("identity conflict")
        user = self.get(user_id)
        if user is None:
            raise ValueError("missing user")
        return self.update(
            replace(
                user,
                external_identities=user.external_identities | frozenset({identity}),
            )
        )


@dataclass
class Enrollment:
    issued: dict[str, IssuedEnrollment]
    revoked: set[str]
    number: int = 0

    def issue_invitation(
        self, *, subject: str, ttl_seconds: int, issued_by: str
    ) -> IssuedEnrollment:
        self.number += 1
        item = IssuedEnrollment(
            f"cap-{self.number}",
            _NOW + timedelta(seconds=ttl_seconds),
            f"token-{self.number}",
        )
        self.issued[subject] = item
        return item

    def revoke(self, capability_id: str) -> bool:
        self.revoked.add(capability_id)
        return True


def _service():
    users = IdentityUserStore()
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    manager = UserManager(users, roles, grants)
    admin = User("admin", "admin")
    users.create(admin)
    grants.add_permission_grant("admin", Permission("users.invite"), Scope.global_())
    audit = MemoryAuditStore()
    enrollment = Enrollment({}, set())
    service = InvitationService(
        manager=manager,
        users=users,
        identities=users,
        invitations=MemoryInvitationStore(),
        enrollment=enrollment,
        audit=audit,
        now=lambda: _NOW,
        new_id=iter(f"id-{n}" for n in range(20)).__next__,
    )
    return service, users, grants, enrollment, audit


def test_invite_and_activate_preserves_subject_and_initial_grants() -> None:
    service, users, grants, _, audit = _service()
    issued = service.invite(
        actor_id="admin",
        user=User("anna", "anna", display_name="Anna", status="pending"),
        grants=(InvitationGrant(role_name="admin"),),
        ttl_seconds=300,
    )

    assert issued.token == "token-1"
    assert users.get("anna").status == "pending"
    assert grants.list_grants_for_user("anna")[0].role_name == "admin"
    active = service.activate(
        InvitationActivation(
            issued.invitation.invitation_id,
            issued.invitation.capability_id,
            ExternalIdentity("my-auth", "anna"),
        )
    )
    assert active.status == "active"
    assert active.external_identities == frozenset(
        {ExternalIdentity("my-auth", "anna")}
    )
    events = audit.list(limit=10, offset=0, filters=AuditFilters())
    assert [event.action for event in events] == [
        "invitation.issue",
        "invitation.activate",
    ]
    assert all("token" not in event.metadata for event in events)


def test_non_admin_cannot_invite() -> None:
    service, users, _, _, _ = _service()
    users.create(User("member", "member"))
    with pytest.raises(PermissionError):
        service.invite(
            actor_id="member",
            user=User("anna", "anna", status="pending"),
            grants=(InvitationGrant(role_name="admin"),),
            ttl_seconds=300,
        )
    assert users.get("anna") is None


def test_activation_cannot_retarget_or_elevate_grants() -> None:
    service, _, grants, _, _ = _service()
    issued = service.invite(
        actor_id="admin",
        user=User("anna", "anna", status="pending"),
        grants=(InvitationGrant(role_name="admin"),),
        ttl_seconds=300,
    )
    with pytest.raises(InvitationError, match="invitation is unavailable"):
        service.activate(
            InvitationActivation(
                issued.invitation.invitation_id,
                issued.invitation.capability_id,
                ExternalIdentity("my-auth", "ewa"),
            )
        )
    assert [grant.role_name for grant in grants.list_grants_for_user("anna")] == [
        "admin"
    ]


def test_reissue_revokes_previous_material_and_replay_fails() -> None:
    service, _, _, enrollment, _ = _service()
    first = service.invite(
        actor_id="admin",
        user=User("anna", "anna", status="pending"),
        grants=(InvitationGrant(role_name="admin"),),
        ttl_seconds=300,
    )
    second = service.reissue(
        actor_id="admin", invitation_id=first.invitation.invitation_id, ttl_seconds=600
    )
    assert first.invitation.capability_id in enrollment.revoked
    assert second.invitation.capability_id != first.invitation.capability_id
    service.activate(
        InvitationActivation(
            second.invitation.invitation_id,
            second.invitation.capability_id,
            ExternalIdentity("my-auth", "anna"),
        )
    )
    with pytest.raises(InvitationError, match="invitation is unavailable"):
        service.activate(
            InvitationActivation(
                second.invitation.invitation_id,
                second.invitation.capability_id,
                ExternalIdentity("my-auth", "anna"),
            )
        )


def test_revoked_and_expired_invitations_fail_closed() -> None:
    service, _, _, enrollment, _ = _service()
    issued = service.invite(
        actor_id="admin",
        user=User("anna", "anna", status="pending"),
        grants=(InvitationGrant(role_name="admin"),),
        ttl_seconds=300,
    )
    revoked = service.revoke(
        actor_id="admin", invitation_id=issued.invitation.invitation_id
    )
    assert revoked.status == "revoked"
    assert issued.invitation.capability_id in enrollment.revoked
    with pytest.raises(InvitationError, match="invitation is unavailable"):
        service.activate(
            InvitationActivation(
                issued.invitation.invitation_id,
                issued.invitation.capability_id,
                ExternalIdentity("my-auth", "anna"),
            )
        )
