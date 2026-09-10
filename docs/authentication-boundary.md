# Authentication boundary (implementation in progress)

Related: #127 and the portability tracker #131. This document describes the
UM side of a swappable identity stack: the host is a generic relying party,
my-auth is a minimal OpenID Provider, and local `user_id` / grants stay here.
It is not a claim that issuer swap or Keycloak migration already works.

## Decision

Keep `SubjectAdapter` and `AuthenticatedSubject` as the provider-to-local seam.
Do not introduce a second user directory or a universal login workflow.

1. A trusted adapter completes WebAuthn or another provider's protocol.
2. The host resolves the provider identity to an **existing** local user. Linking
   and provisioning require separate, explicit authorization.
3. `resolve_authenticated_principal` re-resolves the link, denies unlinked,
   inactive or mismatched accounts, and runs the host's grant projection.
4. The host rotates/establishes its session and applies its cookie/CSRF policy.
5. Product routes consume the local principal, not provider tokens or SDK types.

`AuthenticatedSubject.user_id` is the host's explicit local mapping. An adapter
must not put an unrelated remote `sub` there or derive a new local id during a
provider switch. The current my-auth adapter retains its historical id mapping;
a host linking an existing account must supply the matching local subject.

## Evidence is not verification

`AuthenticationContext` carries a timezone-aware authentication timestamp,
provider-specific methods and assurance, when known. It does not verify anything.
Construct it only on the trusted completion path. In particular:

- ID token issuance/receipt time is not a substitute for `auth_time`.
- Missing authentication time stays `None`.
- A passkey does not automatically satisfy arbitrary provider MFA levels.
- `amr`/`acr` do not grant roles; the host owns step-up/assurance policy.
- MyAuthSubjectAdapter accepts optional evidence; it never invents freshness from
  a stored PasskeyUser alone.

The resolution helper has no token/session writes, provisioning, activation,
external I/O, or observer effects. Denials have the same public PermissionError.
Unexpected store/projector failures propagate; they never establish a session.
The host maps errors to its HTTP response without leaking provider details.

## Session and authorization freshness

The helper checks the current account at login completion. It is **not** a
substitute for reloading account status and current grants on protected requests,
or for host-owned revocation. Hosts needing an atomic completion must invoke it
inside their store transaction and coordinate session creation. Calling a helper
cannot eliminate a concurrent deactivation after its read.

## Still required before portability is complete

- Provider capabilities and account UI: [implemented configuration/rendering](account-capabilities.md), with actual provider/session integration still pending (#129).
- Canonical issuer/sub mapping and migration/rollback (#128).
- A real OIDC code-flow relying party outside core (#124 / #149).
- One host tested against two issuers: my-auth as a minimal OP, then Keycloak
  (#130). Domain routes stay identical; only the issuer URL changes.
- Same-route adapter contract coverage and integration of the completion helper
  into those host examples (#127).

This UM boundary does not implement an OpenID Provider, mint tokens, or add
app-factory domain routes. The OP lives in my-auth. Product hosts must not import
passkey SDK types onto domain routes if they want the issuer to remain swappable.
