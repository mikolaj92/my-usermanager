from __future__ import annotations

import pytest

from my_usermanager.claims import (
    GrantClaims,
    GrantClaimsProjector,
    max_permission_level_claim,
    permission_claim,
    role_claim,
)
from my_usermanager.memory import MemoryGrantStore, MemoryRoleStore
from my_usermanager.models import ExternalIdentity, Permission, Scope, User
from my_usermanager.sessions import InvalidSessionPrincipalError


def test_projector_expands_admin_role_into_roles_permissions_and_claims() -> None:
    # Given: a user with the built-in admin role at global scope.
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    _ = grants.add_role_grant("user_123", "admin", Scope.global_())
    projector = GrantClaimsProjector(roles=roles, grants=grants)

    # When: grant claims are projected.
    projection = projector.project("user_123")

    # Then: common admin, role, and permission projections are present.
    assert projection.roles == frozenset({"admin"})
    assert Permission("admin.access") in projection.permissions
    assert projection.claims == {"is_admin": True}


def test_projector_applies_scope_rules_to_direct_permissions() -> None:
    # Given: a workflow-scoped permission grant.
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    permission = Permission("workflows.run")
    _ = grants.add_permission_grant(
        "user_123",
        permission,
        Scope.scoped("workflow", "wf_1"),
    )
    projector = GrantClaimsProjector(roles=roles, grants=grants)

    # When: projections are requested for global, matching, and other scopes.
    global_projection = projector.project("user_123")
    matching_projection = projector.project(
        "user_123",
        scope=Scope.scoped("workflow", "wf_1"),
    )
    other_projection = projector.project(
        "user_123",
        scope=Scope.scoped("workflow", "wf_2"),
    )

    # Then: scoped grants only appear where the grant scope allows them.
    assert permission not in global_projection.permissions
    assert permission in matching_projection.permissions
    assert permission not in other_projection.permissions


def test_app_defined_claim_mappers_reuse_grant_queries_and_scope_checks() -> None:
    # Given: role, scoped permission, and cumulative level grants.
    roles = MemoryRoleStore()
    grants = MemoryGrantStore()
    workflow_scope = Scope.scoped("workflow", "wf_1")
    _ = grants.add_role_grant("user_123", "member", Scope.global_())
    _ = grants.add_permission_grant(
        "user_123",
        Permission("workflows.run"),
        workflow_scope,
    )
    _ = grants.add_permission_grant(
        "user_123",
        Permission("svg.level1"),
        Scope.global_(),
    )
    _ = grants.add_permission_grant(
        "user_123",
        Permission("svg.level3"),
        Scope.global_(),
    )
    projector = GrantClaimsProjector(
        roles=roles,
        grants=grants,
        claim_mappers=(
            role_claim("is_member", "member"),
            permission_claim(
                "can_run_workflow",
                Permission("workflows.run"),
                scope=workflow_scope,
            ),
            max_permission_level_claim(
                "svg_level",
                {
                    Permission("svg.level1"): 1,
                    Permission("svg.level2"): 2,
                    Permission("svg.level3"): 3,
                },
            ),
            lambda context: {
                "has_app_access": context.has_role("member")
                or context.has_permission("admin.access"),
            },
        ),
    )

    # When: app-specific claims are projected.
    projection = projector.project("user_123", scope=workflow_scope)

    # Then: apps define names and values without re-querying grants.
    assert projection.claims == {
        "can_run_workflow": True,
        "has_app_access": True,
        "is_admin": False,
        "is_member": True,
        "svg_level": 3,
    }


def test_projected_claims_merge_into_session_principal() -> None:
    # Given: a projection and matching local user profile.
    identity = ExternalIdentity(provider="my-auth", subject="passkey_user_123")
    user = User(
        user_id="user_123",
        username="alice",
        external_identities=frozenset({identity}),
        display_name="Alice Example",
    )
    projection = GrantClaims(
        user_id="user_123",
        scope=Scope.global_(),
        roles=frozenset({"member"}),
        permissions=frozenset({Permission("reports.read")}),
        claims={"is_member": True},
    )

    # When: the projection is merged into a session principal.
    principal = projection.to_session_principal(
        user,
        extra_claims={"report": "full"},
    )

    # Then: profile, identity, roles, permissions, and claims share one value.
    assert principal.user_id == "user_123"
    assert principal.username == "alice"
    assert principal.display_name == "Alice Example"
    assert principal.external_identities == frozenset({identity})
    assert principal.roles == frozenset({"member"})
    assert principal.permissions == frozenset({Permission("reports.read")})
    assert principal.claims == {"is_member": True, "report": "full"}


def test_projection_rejects_merging_into_a_different_user() -> None:
    # Given: claims projected for one user.
    projection = GrantClaims(
        user_id="user_123",
        scope=Scope.global_(),
        roles=frozenset(),
        permissions=frozenset(),
        claims={},
    )

    # When / Then: merging into a different user is rejected.
    with pytest.raises(InvalidSessionPrincipalError, match="projected grant claims"):
        _ = projection.to_session_principal(User(user_id="other_user", username="other_user"))
