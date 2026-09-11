from __future__ import annotations

import pytest

from my_usermanager.step_up import MemoryStepUpStore, require_step_up


def test_step_up_is_opt_in_and_does_not_run_without_a_gate() -> None:
    require_step_up(
        actor_id="admin",
        session_id="sess-1",
        operation="users.delete",
        target="anna",
        now=1_000.0,
        store=None,
        token=None,
    )


def test_step_up_proof_is_bound_to_actor_session_operation_and_target() -> None:
    store = MemoryStepUpStore(ttl_seconds=60)
    token = store.issue(
        actor_id="admin",
        session_id="sess-1",
        operation="users.delete",
        target="anna",
        now=1_000.0,
    )

    require_step_up(
        actor_id="admin",
        session_id="sess-1",
        operation="users.delete",
        target="anna",
        now=1_010.0,
        store=store,
        token=token,
    )

    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="admin",
            session_id="sess-1",
            operation="users.delete",
            target="anna",
            now=1_011.0,
            store=store,
            token=token,
        )


def test_step_up_rejects_replay_wrong_binding_expiry_and_missing_proof() -> None:
    store = MemoryStepUpStore(ttl_seconds=30)
    token = store.issue(
        actor_id="admin",
        session_id="sess-1",
        operation="users.delete",
        target="anna",
        now=1_000.0,
    )
    other = store.issue(
        actor_id="admin",
        session_id="sess-1",
        operation="users.delete",
        target="anna",
        now=1_000.0,
    )

    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="other",
            session_id="sess-1",
            operation="users.delete",
            target="anna",
            now=1_010.0,
            store=store,
            token=token,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="admin",
            session_id="sess-2",
            operation="users.delete",
            target="anna",
            now=1_010.0,
            store=store,
            token=token,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="admin",
            session_id="sess-1",
            operation="grants.revoke",
            target="anna",
            now=1_010.0,
            store=store,
            token=token,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="admin",
            session_id="sess-1",
            operation="users.delete",
            target="ewa",
            now=1_010.0,
            store=store,
            token=token,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="admin",
            session_id="sess-1",
            operation="users.delete",
            target="anna",
            now=1_031.0,
            store=store,
            token=other,
        )
    with pytest.raises(PermissionError, match="authentication unavailable"):
        require_step_up(
            actor_id="admin",
            session_id="sess-1",
            operation="users.delete",
            target="anna",
            now=1_010.0,
            store=store,
            token=None,
        )
