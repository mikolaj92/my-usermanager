from datetime import UTC, datetime

import pytest

from my_usermanager.models import (
    AuditEvent,
    ExternalIdentity,
    Grant,
    Permission,
    Role,
    Scope,
    User,
    ValidationError,
)


def test_user_model_has_value_semantics_when_same_values() -> None:
    # Given: two user models with equal immutable value fields.
    identity = ExternalIdentity(provider="my-auth", subject="passkey-subject")
    first = User(
        user_id="user_123",
        username="alice",
        first_name="Alice",
        last_name="Example",
        external_identities=frozenset({identity}),
        display_name="Alice",
        email="alice@example.com",
    )
    second = User(
        user_id="user_123",
        username="alice",
        first_name="Alice",
        last_name="Example",
        external_identities=frozenset({identity}),
        display_name="Alice",
        email="alice@example.com",
    )

    # When: the values are compared and represented.
    same_user = first == second
    representation = repr(first)

    # Then: dataclass value semantics and useful repr are available.
    assert same_user is True
    assert "user_123" in representation
    assert first.username == "alice"
    assert first.first_name == "Alice"
    assert first.last_name == "Example"
    assert first.disabled is False
    assert hash(first) == hash(second)


@pytest.mark.parametrize("bad_user_id", ["", " user_123", "user_123 ", "user\n123"])
def test_user_model_rejects_invalid_ids_when_constructed(bad_user_id: str) -> None:
    # Given: malformed user identifiers from a boundary.

    # When / Then: constructing a typed user rejects them.
    with pytest.raises(ValidationError, match="user_id"):
        _ = User(user_id=bad_user_id)


def test_user_model_rejects_invalid_profile_fields_when_constructed() -> None:
    # Given / When / Then: profile fields reject malformed boundary text.
    with pytest.raises(ValidationError, match="username"):
        _ = User(user_id="user_123", username="bad username")

    with pytest.raises(ValidationError, match="first_name"):
        _ = User(user_id="user_123", first_name=" Alice")

    with pytest.raises(ValidationError, match="last_name"):
        _ = User(user_id="user_123", last_name="Example\n")


def test_scope_global_representation_when_created_by_factory() -> None:
    # Given: the public global scope factory.
    scope = Scope.global_()

    # When: the scope is inspected.
    is_global = scope.is_global()

    # Then: global scope is exactly None/None.
    assert scope.scope_type is None
    assert scope.scope_id is None
    assert is_global is True
    assert repr(scope) == "Scope(scope_type=None, scope_id=None)"


def test_scope_rejects_half_populated_values_when_constructed() -> None:
    # Given: scopes with only one non-global component.

    # When / Then: scope construction rejects both malformed halves.
    with pytest.raises(ValidationError, match="scope"):
        _ = Scope(scope_type="tenant", scope_id=None)
    with pytest.raises(ValidationError, match="scope"):
        _ = Scope(scope_type=None, scope_id="tenant_123")


def test_scope_allows_global_inheritance_and_exact_scoped_matches() -> None:
    # Given: global and concrete scoped grants/checks.
    global_scope = Scope.global_()
    tenant_scope = Scope.scoped("tenant", "tenant_123")
    same_tenant_scope = Scope.scoped("tenant", "tenant_123")
    other_tenant_scope = Scope.scoped("tenant", "tenant_456")
    project_scope = Scope.scoped("project", "tenant_123")

    # When: grant scopes are matched against requested scopes.
    global_allows_global = global_scope.allows(global_scope)
    global_allows_scoped = global_scope.allows(tenant_scope)
    scoped_allows_same_scope = tenant_scope.allows(same_tenant_scope)
    scoped_allows_other_id = tenant_scope.allows(other_tenant_scope)
    scoped_allows_other_type = tenant_scope.allows(project_scope)
    scoped_allows_global = tenant_scope.allows(global_scope)

    # Then: global grants inherit downward and scoped grants match exactly only.
    assert global_allows_global is True
    assert global_allows_scoped is True
    assert scoped_allows_same_scope is True
    assert scoped_allows_other_id is False
    assert scoped_allows_other_type is False
    assert scoped_allows_global is False


def test_permission_role_and_grant_models_are_typed_values() -> None:
    # Given: a permission, role, scope, and user grant values.
    permission = Permission("users.read")
    role = Role(name="reader", permissions=frozenset({permission}))
    scope = Scope.scoped("tenant", "tenant_123")

    # When: role and direct permission grants are created.
    role_grant = Grant.for_role(
        user_id="user_123",
        role_name=role.name,
        scope=scope,
    )
    permission_grant = Grant.for_permission(
        user_id="user_123",
        permission=permission,
        scope=scope,
    )

    # Then: grants preserve their target kind and value semantics.
    assert role.permissions == frozenset({permission})
    assert role_grant.role_name == "reader"
    assert role_grant.permission is None
    assert permission_grant.permission == permission
    assert permission_grant.role_name is None
    assert role_grant.scope == scope
    assert "users.read" in repr(permission_grant)


def test_grant_rejects_missing_or_ambiguous_target_when_constructed() -> None:
    # Given: grant values without exactly one target kind.
    permission = Permission("users.read")

    # When / Then: construction rejects invalid grant shapes.
    with pytest.raises(ValidationError, match="grant"):
        _ = Grant(user_id="user_123")
    with pytest.raises(ValidationError, match="grant"):
        _ = Grant(user_id="user_123", role_name="reader", permission=permission)


def test_audit_event_model_preserves_typed_metadata_when_constructed() -> None:
    # Given: audit event metadata supplied from caller-owned data.
    metadata = {"reason": "seed", "request_id": "req_123"}

    # When: an audit event is constructed.
    event = AuditEvent(
        event_id="evt_123",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="admin_123",
        action="user.created",
        target_type="user",
        target_id="user_123",
        scope=Scope.global_(),
        result="success",
        reason="initial admin setup",
        metadata=metadata,
    )
    metadata["reason"] = "mutated outside"

    # Then: audit event values are immutable snapshots with useful equality/repr.
    assert event.metadata["reason"] == "seed"
    assert event.metadata["request_id"] == "req_123"
    assert "evt_123" in repr(event)
    assert event == AuditEvent(
        event_id="evt_123",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        actor_id="admin_123",
        action="user.created",
        target_type="user",
        target_id="user_123",
        scope=Scope.global_(),
        result="success",
        reason="initial admin setup",
        metadata={"reason": "seed", "request_id": "req_123"},
    )


def test_audit_event_rejects_naive_timestamp_when_constructed() -> None:
    # Given: an audit event timestamp without timezone information.
    naive_timestamp = datetime(2025, 1, 1, tzinfo=UTC).replace(tzinfo=None)

    # When / Then: construction rejects the ambiguous timestamp.
    with pytest.raises(ValidationError, match="timestamp"):
        _ = AuditEvent(
            event_id="evt_123",
            timestamp=naive_timestamp,
            actor_id="admin_123",
            action="user.created",
            target_type="user",
            target_id="user_123",
            scope=Scope.global_(),
            result="success",
        )
