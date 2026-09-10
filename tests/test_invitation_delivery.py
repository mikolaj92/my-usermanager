# pyright: reportMissingParameterType=false, reportOptionalMemberAccess=false, reportPrivateUsage=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportUnusedParameter=false
# ruff: noqa: EM101, TRY003

from __future__ import annotations

from dataclasses import dataclass

from my_usermanager import (
    InvitationRecipient,
    InvitationTransport,
    InvitationTransportError,
    User,
    deliver_issued_invitation,
)
from my_usermanager.invitations import (
    InvitationGrant,
    InvitationService,
    IssuedInvitation,
)
from my_usermanager.stores import AuditFilters
from test_invitations import _service


@dataclass
class RecordingTransport:
    deliveries: list[tuple[IssuedInvitation, InvitationRecipient]]
    fail: bool = False

    def deliver(self, issued: IssuedInvitation, recipient: InvitationRecipient) -> None:
        if self.fail:
            raise InvitationTransportError("smtp rejected")
        self.deliveries.append((issued, recipient))


def _invite(
    service: InvitationService, *, email: str | None = "anna@example.test"
) -> IssuedInvitation:
    return service.invite(
        actor_id="admin",
        user=User("anna", "anna", email=email, status="pending"),
        grants=(InvitationGrant(role_name="admin"),),
        ttl_seconds=300,
    )


def test_missing_transport_keeps_manual_one_time_link() -> None:
    service, _, _, _, audit = _service()
    issued = _invite(service)

    result = deliver_issued_invitation(issued)

    assert result.status == "manual"
    assert result.invitation is issued.invitation
    assert result.activation_url == "/activate?capability=token-1"
    assert result.reveal_activation_url is True
    events = audit.list(limit=10, offset=0, filters=AuditFilters())
    assert [event.action for event in events] == ["invitation.issue"]
    assert all("token" not in event.metadata for event in events)
    assert all(issued.token not in str(event.metadata) for event in events)


def test_successful_delivery_happens_after_commit_and_hides_raw_url() -> None:
    service, users, _, _, audit = _service()
    issued = _invite(service)
    transport = RecordingTransport([])

    result = deliver_issued_invitation(
        issued,
        transport=transport,
        recipient=InvitationRecipient(address="anna@example.test"),
    )

    assert result.status == "delivered"
    assert result.reveal_activation_url is False
    assert result.activation_url is None
    assert users.get("anna").status == "pending"
    assert len(transport.deliveries) == 1
    delivered, recipient = transport.deliveries[0]
    assert delivered.token == issued.token
    assert recipient.address == "anna@example.test"
    events = audit.list(limit=10, offset=0, filters=AuditFilters())
    assert [event.action for event in events] == ["invitation.issue"]
    assert all(issued.token not in str(event.metadata) for event in events)


def test_host_may_reveal_raw_url_after_automatic_delivery() -> None:
    service, _, _, _, _ = _service()
    issued = _invite(service)

    delivered = deliver_issued_invitation(
        issued,
        transport=RecordingTransport([]),
        recipient=InvitationRecipient(address="anna@example.test"),
        reveal_activation_url=True,
    )
    failed = deliver_issued_invitation(
        issued,
        transport=RecordingTransport([], fail=True),
        recipient=InvitationRecipient(address="anna@example.test"),
        reveal_activation_url=True,
    )

    assert delivered.status == "delivered"
    assert delivered.reveal_activation_url is True
    assert delivered.activation_url == issued.activation_url()
    assert failed.status == "delivery_failed"
    assert failed.reveal_activation_url is True
    assert failed.activation_url == issued.activation_url()


def test_transport_failure_leaves_pending_invitation_and_is_explicit() -> None:
    service, users, _, enrollment, audit = _service()
    issued = _invite(service)
    invitations = service._invitations

    result = deliver_issued_invitation(
        issued,
        transport=RecordingTransport([], fail=True),
        recipient=InvitationRecipient(address="anna@example.test"),
    )

    stored = invitations.get(issued.invitation.invitation_id)
    assert result.status == "delivery_failed"
    assert result.reveal_activation_url is False
    assert result.activation_url is None
    assert stored is not None
    assert stored.status == "pending"
    assert users.get("anna").status == "pending"
    assert issued.invitation.capability_id not in enrollment.revoked
    events = audit.list(limit=10, offset=0, filters=AuditFilters())
    assert [event.action for event in events] == ["invitation.issue"]
    assert all(issued.token not in str(event.metadata) for event in events)


def test_lost_token_requires_reissue_and_revokes_previous_capability() -> None:
    service, _, _, enrollment, _ = _service()
    first = _invite(service)
    failed = deliver_issued_invitation(
        first,
        transport=RecordingTransport([], fail=True),
        recipient=InvitationRecipient(address="anna@example.test"),
    )
    assert failed.status == "delivery_failed"

    second = service.reissue(
        actor_id="admin", invitation_id=first.invitation.invitation_id, ttl_seconds=600
    )
    delivered = deliver_issued_invitation(
        second,
        transport=RecordingTransport([]),
        recipient=InvitationRecipient(address="anna@example.test"),
    )

    assert first.invitation.capability_id in enrollment.revoked
    assert second.token != first.token
    assert delivered.status == "delivered"
    stored = service._invitations.get(first.invitation.invitation_id)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.capability_id == second.invitation.capability_id


def test_delivery_does_not_enumerate_missing_recipients() -> None:
    service, _, _, _, _ = _service()
    issued = _invite(service, email=None)

    result = deliver_issued_invitation(
        issued,
        transport=RecordingTransport([]),
    )

    assert result.status == "delivery_failed"
    assert result.reveal_activation_url is False
    assert result.activation_url is None


def test_fake_transport_protocol_is_usable_without_logging_tokens() -> None:
    class SilentTransport:
        def __init__(self) -> None:
            self.seen_addresses: list[str] = []

        def deliver(
            self, issued: IssuedInvitation, recipient: InvitationRecipient
        ) -> None:
            del issued
            self.seen_addresses.append(recipient.address)

    assert isinstance(SilentTransport(), InvitationTransport)
    service, _, _, _, _ = _service()
    issued = _invite(service)
    transport = SilentTransport()

    result = deliver_issued_invitation(
        issued,
        transport=transport,
        recipient=InvitationRecipient(address="anna@example.test"),
    )

    assert result.status == "delivered"
    assert transport.seen_addresses == ["anna@example.test"]
