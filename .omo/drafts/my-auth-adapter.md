# my-auth adapter for my-usermanager — draft

status: awaiting-approval
pending_action: write `.omo/plans/my-auth-adapter.md`
requested_by: user

## Original request

> ok zrobmy update. jest duza szansa, ze my-usermanager bedzie uzywany z my-auth. czy mozna to jakos dopracowac? jakis adapter czy cos?

Intent: plan an update so `my-usermanager` integrates cleanly with `my-auth`, likely through an adapter or integration layer.

## Hard constraints

- Do not implement product code in this planning phase.
- Both `my-auth` and `my-usermanager` must be public OSS projects.
- Both packages must use the MIT license.
- Anyone should be able to install/pull the packages without private GitHub access.
- Preserve `my-usermanager` core as framework-neutral and dependency-light.
- Importing `my_usermanager` must not import FastAPI/Pydantic/`my_auth` side effects.
- Any concrete `my-auth` dependency should live behind an optional module/extra or separate adapter package using public package metadata.
- Final implementation must follow strict Python gates: Ruff, basedpyright, pytest, and LOC review.

## Repository facts: my-usermanager

- Local repo: `/Users/mini-m4-1/Developer/my-usermanager`.
- Remote: `https://github.com/mikolaj92/my-usermanager`.
- Visibility target: public OSS. Current planning assumption: make repository public before advertising it as installable.
- License target: MIT. Current repo already contains `LICENSE` and metadata says MIT.
- Package import: `my_usermanager`; distribution: `my-usermanager`.
- `pyproject.toml` currently has no runtime dependencies.
- Existing optional extra: `fastapi = ["fastapi>=0.115", "pydantic>=2.10"]`.
- Current core already accepts host-app/auth-provider authenticated subjects conceptually.

Relevant current code:

- `src/my_usermanager/models.py`
  - `User` fields include `user_id`, `external_identities`, `username`, `first_name`, `last_name`, `display_name`, `email`, `disabled`, `system`, `scope`.
  - `ExternalIdentity(provider, subject)` is already the natural seam for external auth identities.
  - `validate_identifier` rejects empty, leading/trailing whitespace, and any whitespace. Adapter design must handle `my-auth` ids that may not be valid `User.user_id` values.
  - `Scope` supports global/scoped authorization via `Scope.allows`.
- `src/my_usermanager/manager.py`
  - `UserManager` currently owns the safe facade for profile and access operations.
  - It supports `update_own_profile`, `update_profile`, `grant_role`, `revoke_role`, `grant_permission`, `revoke_permission`.
  - It enforces self-only profile updates and admin/permission-gated access management.
- `src/my_usermanager/stores.py`
  - `UserStore` supports `create`, `get`, `update`, `list`, `count_active`.
  - There is no direct lookup by `ExternalIdentity`.
  - Resolving external identities by scanning `UserStore.list` would be acceptable only as a tiny in-memory fallback, not as the long-term protocol.

## Repository facts: my-auth

Source inspected from public clone:

- GitHub repo: `mikolaj92/my-auth`.
- Visibility: public.
- License target: MIT. `pyproject.toml` declares MIT and repo includes `LICENSE`.
- Clone path used for planning: `/var/folders/q2/7_fm0c4j1cv7t04tgk60szr00000gn/T/opencode/my-auth`.
- Package import: `my_auth`; distribution: `my-auth`.
- `pyproject.toml`
  - Python `>=3.11`.
  - Runtime dependency: `webauthn>=2.2,<3`.
  - Optional extra: `fastapi = ["fastapi>=0.115"]`.
- README states `my-auth` is passkey-only auth core.
- README explicitly says `my-auth` does **not** own middleware, admin panel, sessions, RBAC, app DB, or layout.
- That division matches `my-usermanager`: `my-auth` authenticates; `my-usermanager` manages users/authorization.

Public `my-auth` API found:

- `my_auth.PasskeyUser`
  - fields: `user_id: str`, `user_handle: bytes`, `name: str`, `display_name: str | None = None`.
  - property: `user_handle_b64url`.
  - `user_id` is the app-level user id used by hooks/session.
  - `user_handle` is stable WebAuthn bytes, unique per user, but should not be treated as the default app display/user id.
- `my_auth.PasskeyCredential`
  - fields include `credential_id`, `user_id`, `public_key`, `sign_count`, transports/device metadata.
- `my_auth.CredentialStore` protocol
  - stores passkey users and credentials.
  - includes `get_user(user_id: str)`, `get_user_by_handle(user_handle: bytes)`, and credential methods.
- `my_auth.PasskeyService`
  - begins/finishes registration and authentication.
  - returns/uses `PasskeyUser` and `AuthenticationResult`.
- `my_auth.fastapi.PasskeyRouteHooks`
  - `get_session_user(request) -> AuthUser | None`
  - `make_registration_user(request, display_name) -> AuthUser`
  - `get_auth_user(user_id: str) -> AuthUser | None`
  - `login(response, request, user)`
  - `logout(response, request)`
  - `registration_allowed(request)`
  - optional `after_register(request, user, credential)`
  - optional `after_login(request, user, credential)`
- `my_auth.fastapi.PasskeyAuthRouter`
  - FastAPI route factory using the hooks above.
  - Login verification calls `hooks.get_auth_user(result.user.user_id)` and rejects with 403 if absent.
  - Register verification resolves user via session or `hooks.get_auth_user(credential.user_id)` and rejects with 403 if absent.

## Design conclusions

1. `my-auth` is not an RBAC/user-manager. It intentionally delegates app user/session policy to hooks.
2. `my-usermanager` should not wrap or replace `PasskeyAuthRouter`; it should provide typed hook helpers/resolvers that host apps can plug into `PasskeyRouteHooks`.
3. Core `my_usermanager` should stay independent of `my_auth`.
4. A dependency-free adapter seam should be introduced in `my-usermanager` core first, with a concrete `my_auth` adapter behind an optional import/extra.
5. `ExternalIdentity(provider="my-auth", subject=...)` should be used to record the relationship to `my-auth`.
6. Because `UserStore` lacks identity lookup, the plan should add a protocol for external identity resolution/indexing rather than relying on `UserStore.list` scans.
7. Since both packages must be public OSS, adapter dependency examples should use public install coordinates, not private GitHub access. GitHub URL dependencies are acceptable only if the repositories are public; PyPI publication can be a later packaging milestone.

## Recommended approach

### Layer 1 — dependency-free core subject adapter seam

Add a core module such as `my_usermanager.subjects` or `my_usermanager.adapters.core` with no dependency on `my_auth`, FastAPI, or Pydantic.

Planned core concepts:

- `AuthenticatedSubject` or similar frozen dataclass:
  - `provider: str`
  - `subject_id: str`
  - `user_id: str`
  - optional profile fields: `username`, `first_name`, `last_name`, `display_name`, `email`
  - `external_identity: ExternalIdentity`
- `SubjectAdapter` protocol:
  - converts a provider-specific authenticated subject into the core subject value.
- `ExternalIdentityResolver` / `ExternalIdentityUserStore` protocol:
  - resolves `(provider, subject)` to `User | None` or `user_id | None`.
  - avoids long-term scan-based lookup.
- `UserProvisioningPolicy` / helper operations:
  - find existing user by identity or user id.
  - optionally create/update a profile record.
  - never auto-grant roles/permissions.

### Layer 2 — optional concrete my-auth adapter

Add optional module such as `my_usermanager.adapters.my_auth`.

Properties:

- Imported only by users who opt in.
- Not imported from `my_usermanager.__init__`.
- Optional extra in `pyproject.toml`, for example one of:
  - `myauth = ["my-auth @ git+https://github.com/mikolaj92/my-auth"]` while public/pre-release.
  - `myauth = ["my-auth>=0.1"]` if/when published.
- Converts `my_auth.PasskeyUser` into the dependency-free core `AuthenticatedSubject`.
- Default mapping:
  - `PasskeyUser.user_id` is the canonical app user id when it passes `validate_identifier`.
  - Store `ExternalIdentity(provider="my-auth", subject=PasskeyUser.user_id)`.
  - Optionally also expose/store `user_handle_b64url` as a provider handle if a future model supports identity metadata.
  - Map `PasskeyUser.name` / `display_name` into profile bootstrap/update fields conservatively.

### Layer 3 — FastAPI hook helpers, but optional

If FastAPI support is desired, expose helpers that help construct `PasskeyRouteHooks` without importing FastAPI in core.

Possible helper responsibilities:

- `get_auth_user(user_id)` backed by `UserStore` + my-auth credential store.
- `make_registration_user(request, display_name)` using a host-controlled registration policy.
- `after_register` to ensure a matching `my-usermanager.User` exists or to attach `ExternalIdentity`.
- `after_login` to refresh allowed profile fields, never grants.

The adapter should not own sessions. `my-auth` hooks and the host app should continue to own sessions.

## Provisioning default

Recommended default:

- Adapter may create or update a basic `User` profile record only when the host app's `registration_allowed` policy allowed registration.
- Adapter must never auto-grant admin, roles, or permissions.
- Admin access remains explicit through `UserManager.grant_role` / `grant_permission` or direct admin tooling.
- If a returning `my-auth` user has no corresponding `my-usermanager` user and auto-provision is disabled, login hook should return `None` so `my-auth` rejects with 403.

## Test strategy for the eventual implementation

Use TDD:

1. Core subject adapter tests:
   - converts provider subject to `AuthenticatedSubject`.
   - rejects invalid ids or normalizes deterministically.
   - stores `ExternalIdentity(provider="my-auth", subject=...)`.
2. Identity resolver tests:
   - resolves by external identity without scanning in production-style protocols.
   - handles missing identity deterministically.
3. my-auth adapter tests:
   - maps `PasskeyUser.user_id`, `name`, `display_name`, `user_handle_b64url` correctly.
   - does not import `my_auth` from core import path.
4. Hook-helper tests:
   - allowed registration creates/profile-syncs a user without grants.
   - disallowed/missing user returns `None` / denies.
   - login refreshes profile only, preserving disabled/system/scope/roles/grants.
5. Regression gates:
   - `uv run --no-sync ruff format --check .`
   - `uv run --no-sync ruff check .`
   - `uv run --no-sync basedpyright`
   - `uv run --no-sync pytest`
   - pure LOC measurement for modified files.

## Scope in

- Plan core subject/identity adapter seam.
- Plan optional `my-auth` adapter module/extra.
- Plan hook helpers for `PasskeyRouteHooks` if concrete `my-auth` integration is approved.
- Plan tests and documentation updates.

## Scope out

- Do not move session handling into `my-usermanager`.
- Do not implement WebAuthn/passkey crypto in `my-usermanager`.
- Do not make `my_usermanager` core import depend on `my_auth`, FastAPI, Pydantic, or web frameworks.
- Do not automatically grant roles/permissions on passkey registration or login.

## Recommended defaults pending approval

1. Make/keep both repositories public OSS before documenting install instructions.
2. Keep both packages MIT; verify README/package metadata/license files all agree.
3. Adapter lives in this repo as optional module/extra, not a separate package initially.
4. Add a dependency-free core subject/identity seam first.
5. Add concrete `my_usermanager.adapters.my_auth` second.
6. Use `PasskeyUser.user_id` as canonical `User.user_id` when valid; otherwise use deterministic normalized/provider-prefixed id and preserve the original in `ExternalIdentity`.
7. Permit profile record creation/update only under explicit host registration policy; never auto-grant roles/permissions.
8. Document public install paths using public GitHub URLs first; add PyPI-oriented dependency strings later only after packaging/release is ready.

## Open questions for approval brief

These can be resolved by defaults unless the user wants different behavior:

1. Should first-login/registration auto-create a `my-usermanager.User` profile record?
   - Recommended: yes only after host `registration_allowed` allows it; no roles/permissions are granted.
2. Should `my-auth` adapter live in this repo or a separate package?
   - Recommended: in this repo as an optional adapter module/extra while both packages are young.
3. Should `PasskeyUser.user_id` be canonical for `User.user_id`?
   - Recommended: yes when valid; fallback to deterministic normalized id while preserving original as `ExternalIdentity`.
4. Should the implementation plan include repository-visibility work for `my-usermanager`?
   - Recommended: yes. `my-auth` is already public; `my-usermanager` should be made public before public install docs are advertised.
5. Should the implementation plan include PyPI publishing now?
   - Recommended: no. Start with public GitHub install coordinates; add PyPI release automation as a separate packaging plan unless you explicitly want it now.

## Approval gate

status: awaiting-approval

Pending action after approval: write `.omo/plans/my-auth-adapter.md` with one decision-complete execution plan. Approval does not authorize implementation.
