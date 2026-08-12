# ruff: noqa: D101, D102, D105, D107, EM101, EM102, PLR0913, TRY003
"""Administrator-authorized user invitation lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import (
    TYPE_CHECKING,
    Final,
    Literal,
    Protocol,
    final,
    override,
    runtime_checkable,
)
from urllib.parse import urlencode
from uuid import uuid4

from my_usermanager.models import (
    AuditEvent,
    ExternalIdentity,
    Permission,
    Scope,
    User,
    validate_identifier,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from my_usermanager.manager import UserManager
    from my_usermanager.stores import AuditStore, UserStore
    from my_usermanager.subjects import ExternalIdentityUserStore

__all__: Final = (
    "EnrollmentCapabilityIssuer",
    "Invitation",
    "InvitationActivation",
    "InvitationError",
    "InvitationGrant",
    "InvitationService",
    "InvitationStore",
    "IssuedEnrollment",
    "IssuedInvitation",
    "MemoryInvitationStore",
)

InvitationStatus = Literal["pending", "used", "revoked"]
_INVITE_PERMISSION = Permission("users.invite")
_INVITATION_UNAVAILABLE = "invitation is unavailable"


@dataclass(frozen=True, slots=True)
class InvitationGrant:
    """One administrator-approved initial role or permission."""

    role_name: str | None = None
    permission: Permission | None = None
    scope: Scope = field(default_factory=Scope.global_)

    def __post_init__(self) -> None:
        if (self.role_name is None) == (self.permission is None):
            raise ValueError("exactly one initial role or permission is required")
        if self.role_name is not None:
            _ = validate_identifier(self.role_name, field_name="role_name")


@dataclass(frozen=True, slots=True)
class Invitation:
    """Durable invitation metadata; raw activation material is never retained."""

    invitation_id: str
    user_id: str
    capability_id: str
    expires_at: datetime
    issued_by: str
    grants: tuple[InvitationGrant, ...]
    status: InvitationStatus = "pending"
    created_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IssuedEnrollment:
    """Capability metadata and one-time delivery token returned by an auth adapter."""

    capability_id: str
    expires_at: datetime
    token: str


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation: Invitation
    token: str

    def activation_url(self, activation_path: str = "/activate") -> str:
        """Link the one-time token to my-auth's existing activation page."""
        if not activation_path.startswith("/"):
            raise ValueError("activation_path must be absolute")
        return f"{activation_path}?{urlencode({'capability': self.token})}"


@dataclass(frozen=True, slots=True)
class InvitationActivation:
    invitation_id: str
    capability_id: str
    identity: ExternalIdentity


@dataclass(frozen=True, slots=True)
class InvitationError(ValueError):
    """Non-enumerating invitation lifecycle failure."""

    @override
    def __str__(self) -> str:
        return _INVITATION_UNAVAILABLE


@runtime_checkable
class EnrollmentCapabilityIssuer(Protocol):
    """Narrow adapter implemented with my-auth enrollment capabilities."""

    def issue_invitation(
        self, *, subject: str, ttl_seconds: int, issued_by: str
    ) -> IssuedEnrollment: ...

    def revoke(self, capability_id: str) -> bool: ...


@runtime_checkable
class InvitationStore(Protocol):
    def create(self, invitation: Invitation) -> Invitation: ...
    def get(self, invitation_id: str) -> Invitation | None: ...
    def get_pending_for_user(self, user_id: str) -> Invitation | None: ...
    def update(self, invitation: Invitation) -> Invitation: ...


class MemoryInvitationStore:
    """Process-local invitation metadata store for tests and development."""

    def __init__(self) -> None:
        self._invitations: dict[str, Invitation] = {}

    def create(self, invitation: Invitation) -> Invitation:
        if invitation.invitation_id in self._invitations:
            raise InvitationError
        if self.get_pending_for_user(invitation.user_id) is not None:
            raise InvitationError
        self._invitations[invitation.invitation_id] = invitation
        return invitation

    def get(self, invitation_id: str) -> Invitation | None:
        return self._invitations.get(invitation_id)

    def get_pending_for_user(self, user_id: str) -> Invitation | None:
        return next(
            (
                item
                for item in self._invitations.values()
                if item.user_id == user_id and item.status == "pending"
            ),
            None,
        )

    def update(self, invitation: Invitation) -> Invitation:
        if invitation.invitation_id not in self._invitations:
            raise InvitationError
        self._invitations[invitation.invitation_id] = invitation
        return invitation


@final
class InvitationService:
    """Coordinates account, grants, capability, invitation, and audit stores."""

    def __init__(
        self,
        *,
        manager: UserManager,
        users: UserStore,
        identities: ExternalIdentityUserStore,
        invitations: InvitationStore,
        enrollment: EnrollmentCapabilityIssuer,
        audit: AuditStore,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._manager = manager
        self._users = users
        self._identities = identities
        self._invitations = invitations
        self._enrollment = enrollment
        self._audit = audit
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: uuid4().hex)

    def invite(
        self,
        *,
        actor_id: str,
        user: User,
        grants: tuple[InvitationGrant, ...],
        ttl_seconds: int,
    ) -> IssuedInvitation:
        """Create one pending user and issue identity-bound activation material."""
        self._manager.require_permission(
            actor_id=actor_id,
            permission=_INVITE_PERMISSION,
            target_user_id=user.user_id,
            scope=user.scope,
        )
        if user.status != "pending" or self._users.get(user.user_id) is not None:
            raise InvitationError
        self._validate_grants(grants)
        enrollment = self._enrollment.issue_invitation(
            subject=user.user_id,
            ttl_seconds=ttl_seconds,
            issued_by=actor_id,
        )
        invitation = Invitation(
            invitation_id=self._new_id(),
            user_id=user.user_id,
            capability_id=enrollment.capability_id,
            expires_at=enrollment.expires_at,
            issued_by=actor_id,
            grants=grants,
            created_at=self._utc_now(),
        )
        try:
            _ = self._users.create(user)
            for grant in grants:
                self._add_grant(user.user_id, grant)
            _ = self._invitations.create(invitation)
        except Exception:
            _ = self._enrollment.revoke(enrollment.capability_id)
            raise
        self._audit_event(actor_id, "invitation.issue", invitation)
        return IssuedInvitation(invitation, enrollment.token)

    def reissue(
        self, *, actor_id: str, invitation_id: str, ttl_seconds: int
    ) -> IssuedInvitation:
        invitation = self._available(invitation_id)
        self._manager.require_permission(
            actor_id=actor_id,
            permission=_INVITE_PERMISSION,
            target_user_id=invitation.user_id,
            scope=Scope.global_(),
        )
        enrollment = self._enrollment.issue_invitation(
            subject=invitation.user_id,
            ttl_seconds=ttl_seconds,
            issued_by=actor_id,
        )
        previous_capability = invitation.capability_id
        updated = replace(
            invitation,
            capability_id=enrollment.capability_id,
            expires_at=enrollment.expires_at,
            issued_by=actor_id,
        )
        _ = self._invitations.update(updated)
        _ = self._enrollment.revoke(previous_capability)
        self._audit_event(actor_id, "invitation.reissue", updated)
        return IssuedInvitation(updated, enrollment.token)

    def revoke(self, *, actor_id: str, invitation_id: str) -> Invitation:
        invitation = self._available(invitation_id)
        self._manager.require_permission(
            actor_id=actor_id,
            permission=_INVITE_PERMISSION,
            target_user_id=invitation.user_id,
            scope=Scope.global_(),
        )
        _ = self._enrollment.revoke(invitation.capability_id)
        revoked = replace(invitation, status="revoked", completed_at=self._utc_now())
        _ = self._invitations.update(revoked)
        self._audit_event(actor_id, "invitation.revoke", revoked)
        return revoked

    def activate(self, activation: InvitationActivation) -> User:
        """Activate exactly the invited user after verified credential enrollment."""
        invitation = self._available(activation.invitation_id)
        user = self._users.get(invitation.user_id)
        if (
            user is None
            or user.status != "pending"
            or invitation.capability_id != activation.capability_id
            or activation.identity.subject != invitation.user_id
            or self._identities.resolve_external_identity(activation.identity)
            is not None
        ):
            raise InvitationError
        linked = self._identities.link_external_identity(
            user_id=user.user_id, identity=activation.identity
        )
        active = self._manager.transition_account(
            user_id=linked.user_id, status="active"
        )
        used = replace(invitation, status="used", completed_at=self._utc_now())
        _ = self._invitations.update(used)
        self._audit_event(active.user_id, "invitation.activate", used)
        return active

    def _available(self, invitation_id: str) -> Invitation:
        invitation = self._invitations.get(invitation_id)
        if (
            invitation is None
            or invitation.status != "pending"
            or invitation.expires_at <= self._utc_now()
        ):
            raise InvitationError
        user = self._users.get(invitation.user_id)
        if user is None or user.status != "pending":
            raise InvitationError
        return invitation

    def _validate_grants(self, grants: tuple[InvitationGrant, ...]) -> None:
        if not grants:
            raise ValueError("at least one initial grant is required")
        if len(set(grants)) != len(grants):
            raise ValueError("initial grants must be unique")
        for grant in grants:
            if (
                grant.role_name is not None
                and self._manager.roles.get(grant.role_name) is None
            ):
                raise ValueError(f"unknown initial role: {grant.role_name}")

    def _add_grant(self, user_id: str, grant: InvitationGrant) -> None:
        if grant.role_name is not None:
            _ = self._manager.grants.add_role_grant(
                user_id, grant.role_name, grant.scope
            )
        elif grant.permission is not None:
            _ = self._manager.grants.add_permission_grant(
                user_id, grant.permission, grant.scope
            )

    def _audit_event(self, actor_id: str, action: str, invitation: Invitation) -> None:
        _ = self._audit.append(
            AuditEvent(
                event_id=self._new_id(),
                timestamp=self._utc_now(),
                actor_id=actor_id,
                action=action,
                target_type="invitation",
                target_id=invitation.invitation_id,
                scope=Scope.global_(),
                result="success",
                metadata={"status": invitation.status, "user_id": invitation.user_id},
            )
        )

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must return a timezone-aware datetime")
        return value.astimezone(UTC)
