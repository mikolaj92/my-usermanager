# ruff: noqa: D107, PLR0913
"""Optional host-owned step-up proof for selected mutations.

This is not authentication ceremony. The host supplies a one-time token after
its provider (my-auth or otherwise) re-verified the current actor. Missing
store means the gate is off.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final, Protocol

__all__: Final[tuple[str, ...]] = (
    "MemoryStepUpStore",
    "StepUpStore",
    "require_step_up",
)

_UNAVAILABLE: Final = "authentication unavailable"
_TTL_ERROR: Final = "ttl_seconds must be positive"


@dataclass(frozen=True, slots=True)
class _StepUpProof:
    actor_id: str
    session_id: str
    operation: str
    target: str


class StepUpStore(Protocol):
    """Host-owned one-time proofs bound to actor, session, operation, and target."""

    def consume(
        self,
        token: str,
        *,
        actor_id: str,
        session_id: str,
        operation: str,
        target: str,
        now: float,
    ) -> None:
        """Consume a still-valid matching proof, or raise PermissionError."""
        ...


class MemoryStepUpStore:
    """Process-local step-up proofs for tests and single-process hosts."""

    def __init__(self, *, ttl_seconds: float = 60.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError(_TTL_ERROR)
        self._ttl_seconds = ttl_seconds
        self._proofs: dict[str, tuple[float, _StepUpProof]] = {}

    def issue(
        self,
        *,
        actor_id: str,
        session_id: str,
        operation: str,
        target: str,
        now: float,
    ) -> str:
        """Record a one-time proof after the host completed reauthentication."""
        token = secrets.token_urlsafe(32)
        self._proofs[token] = (
            now + self._ttl_seconds,
            _StepUpProof(
                actor_id=actor_id,
                session_id=session_id,
                operation=operation,
                target=target,
            ),
        )
        return token

    def consume(
        self,
        token: str,
        *,
        actor_id: str,
        session_id: str,
        operation: str,
        target: str,
        now: float,
    ) -> None:
        """Return only when the token still matches this mutation."""
        record = self._proofs.pop(token, None)
        if record is None:
            raise PermissionError(_UNAVAILABLE)
        expires_at, proof = record
        if now >= expires_at or proof != _StepUpProof(
            actor_id=actor_id,
            session_id=session_id,
            operation=operation,
            target=target,
        ):
            raise PermissionError(_UNAVAILABLE)


def require_step_up(
    *,
    actor_id: str,
    session_id: str,
    operation: str,
    target: str,
    now: float,
    store: StepUpStore | None,
    token: str | None,
) -> None:
    """Refuse the mutation when a gate is on and the proof is missing or stale.

    ``store is None`` leaves host behavior unchanged. RBAC and CSRF stay
    independent of this check.
    """
    if store is None:
        return
    if not token:
        raise PermissionError(_UNAVAILABLE)
    store.consume(
        token,
        actor_id=actor_id,
        session_id=session_id,
        operation=operation,
        target=target,
        now=now,
    )
