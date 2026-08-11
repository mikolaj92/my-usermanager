# ruff: noqa: ANN201, EM101, PLC0415, TRY003
"""Narrow adapter from my-auth enrollment capability stores."""

from __future__ import annotations

from typing import Protocol

from my_usermanager.invitations import IssuedEnrollment


class _CapabilityRecord(Protocol):
    capability_id: str
    expires_at: object


class _IssuedCapability(Protocol):
    capability: _CapabilityRecord
    token: str


class _CapabilityStore(Protocol):
    def issue(
        self,
        *,
        subject: str,
        purpose: str,
        ttl_seconds: int,
        issued_by: str | None = None,
    ) -> _IssuedCapability: ...

    def revoke(self, capability_id: str) -> bool: ...


def build_enrollment_capability_issuer(store: _CapabilityStore):
    """Adapt a my-auth EnrollmentCapabilityStore without importing it eagerly."""
    from datetime import datetime

    class MyAuthEnrollmentCapabilityIssuer:
        def issue_invitation(
            self, *, subject: str, ttl_seconds: int, issued_by: str
        ) -> IssuedEnrollment:
            issued = store.issue(
                subject=subject,
                purpose="invitation",
                ttl_seconds=ttl_seconds,
                issued_by=issued_by,
            )
            expires_at = issued.capability.expires_at
            if not isinstance(expires_at, datetime):
                raise TypeError("capability expiry must be datetime")
            return IssuedEnrollment(
                capability_id=issued.capability.capability_id,
                expires_at=expires_at,
                token=issued.token,
            )

        def revoke(self, capability_id: str) -> bool:
            return store.revoke(capability_id)

    return MyAuthEnrollmentCapabilityIssuer()
