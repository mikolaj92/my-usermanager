# my-auth adapter plan for my-usermanager

## Status

- Status: approved plan
- Target repository: `/Users/mini-m4-1/Developer/my-usermanager`
- Requested outcome: make `my-usermanager` work cleanly with public OSS `my-auth` through a stable adapter/integration seam.
- User constraints:
  - `my-auth` and `my-usermanager` must be public OSS.
  - Both packages must remain MIT.
  - Anyone should be able to pull/install them without private repository access.
  - `my-usermanager` core must remain framework-neutral and dependency-light.

## TODOs

- [x] Phase 0: Verify public OSS/MIT readiness and current quality baseline.
- [x] Phase 1: Add dependency-free subject and external-identity seam.
- [x] Phase 2: Add optional `my-auth` adapter module and public extra metadata.
- [x] Phase 3: Add optional `my-auth` FastAPI hook helpers if the adapter seam supports them cleanly.
- [x] Phase 4: Update public OSS documentation and install examples.
- [ ] Phase 5: Run final verification, commit, push, and make/verify the repository public.

## Decisions locked by this plan

1. Keep `my_usermanager` core dependency-free. Do not import `my_auth`, FastAPI, or Pydantic from `my_usermanager.__init__` or from existing core modules.
2. Add a dependency-free identity/subject seam first, before concrete `my-auth` glue.
3. Add concrete `my-auth` integration under an optional module, tentatively `my_usermanager.adapters.my_auth`.
4. Use stable provider key `MY_AUTH_PROVIDER = "my-auth"` for `ExternalIdentity.provider`.
5. Use `PasskeyUser.user_id` as the default `ExternalIdentity.subject`.
6. Use `PasskeyUser.user_id` as `User.user_id` only when it passes `validate_identifier`; otherwise use a deterministic normalized provider-prefixed fallback and preserve the original subject in `ExternalIdentity`.
7. Do not use `PasskeyUser.user_handle` as the primary `User.user_id` or primary external identity subject. It may be exposed later as adapter metadata if needed.
8. The adapter must never grant roles, permissions, or admin access automatically.
9. Profile create/update is allowed only behind explicit host registration/provisioning policy.
10. Public install docs should use public GitHub coordinates and tags first. PyPI publishing is out of scope for this plan unless requested later.

## Evidence

### my-usermanager

- `src/my_usermanager/manager.py` already exposes `UserManager` for self-profile updates and admin-gated access changes.
- `src/my_usermanager/models.py` already has `ExternalIdentity(provider, subject)` and `User.external_identities`.
- `User` already has profile fields: `username`, `first_name`, `last_name`, `display_name`, `email`.
- `UserStore` currently lacks direct lookup by external identity; this is the main missing seam for a robust auth-provider adapter.
- `pyproject.toml` has no runtime dependencies and only a `fastapi` optional extra.
- License metadata and `LICENSE` are MIT.

### my-auth

- Public repository: `https://github.com/mikolaj92/my-auth`.
- Package: `my-auth`, import: `my_auth`, license metadata: MIT.
- Runtime dependency: `webauthn>=2.2,<3`; optional extra: `fastapi`.
- `my-auth` explicitly does not own sessions, RBAC, middleware, admin panel, app database, or layout.
- `PasskeyUser` shape:
  - `user_id: str`
  - `user_handle: bytes`
  - `name: str`
  - `display_name: str | None`
- `PasskeyRouteHooks` is the correct FastAPI seam: apps provide `get_session_user`, `make_registration_user`, `get_auth_user`, `login`, `logout`, `registration_allowed`, and optional `after_register` / `after_login`.

## Architecture

### Layer 1: dependency-free subject and identity seam

Add a core module such as `src/my_usermanager/subjects.py` or `src/my_usermanager/identity.py`.

It should define:

- `AuthenticatedSubject`: frozen value object for a provider-authenticated user subject.
  - `provider: str`
  - `subject: str`
  - `user_id: str`
  - optional profile fields compatible with `UserProfileUpdate` / `User`
- `SubjectAdapter` protocol: converts a provider-native subject into `AuthenticatedSubject`.
- `ExternalIdentityResolver` or `ExternalIdentityUserStore` protocol:
  - resolve `(provider, subject)` to a `User | None` or `user_id | None`
  - record/link an `ExternalIdentity` to a user
  - define uniqueness and conflict behavior
- typed errors:
  - `ExternalIdentityConflictError`
  - `ExternalIdentityNotFoundError` if needed
  - `InvalidSubjectError` if provider subject cannot be mapped

Rules:

- No framework imports.
- No `my_auth` imports.
- No role/permission grants.
- No automatic user creation except through explicit caller-owned provisioning helpers.

### Layer 2: optional concrete my-auth adapter

Add `src/my_usermanager/adapters/my_auth.py` plus `src/my_usermanager/adapters/__init__.py`.

The module should:

- import `my_auth.PasskeyUser` only inside the optional adapter module.
- provide an actionable import error when `my-auth` is missing.
- define `MY_AUTH_PROVIDER = "my-auth"`.
- provide a mapper from `PasskeyUser` to `AuthenticatedSubject`.
- use `PasskeyUser.user_id` as canonical subject.
- use `PasskeyUser.user_id` as local `User.user_id` when valid.
- fall back to a deterministic normalized id when `PasskeyUser.user_id` is not a valid `my-usermanager` identifier.
- preserve original `PasskeyUser.user_id` in `ExternalIdentity(provider="my-auth", subject=...)`.
- map profile fields:
  - `username`: default to local user id or a caller-provided function.
  - `display_name`: from `PasskeyUser.display_name or PasskeyUser.name`.
  - `first_name` / `last_name`: do not guess by splitting names unless caller opts in.

Optional extra:

```toml
[project.optional-dependencies]
myauth = ["my-auth @ git+https://github.com/mikolaj92/my-auth"]
```

Later, after public release/PyPI:

```toml
myauth = ["my-auth>=0.1"]
```

Do not import this adapter from `my_usermanager.__init__`.

### Layer 3: optional FastAPI hook helpers

Add these only after the core adapter exists.

Possible module: `my_usermanager.adapters.my_auth_fastapi` or keep in `my_usermanager.adapters.my_auth` behind optional FastAPI imports.

Helpers may build or assist `PasskeyRouteHooks` by providing:

- `get_auth_user(user_id: str) -> PasskeyUser | None` backed by `my-usermanager` identity/user resolution.
- `make_registration_user(...) -> PasskeyUser` backed by explicit host registration policy.
- `after_register(...)` to link external identity and create/update profile.
- `after_login(...)` to refresh profile information if desired.

Rules:

- The helper does not own sessions.
- The helper does not own `request.session`.
- The helper does not grant roles/permissions.
- The helper must make denied/missing users return `None` so `my-auth` can respond with 403.

## Conflict behavior

Implement deterministic errors/tests for these cases:

1. External identity already belongs to another user: raise `ExternalIdentityConflictError`.
2. Target user already has a different `my-auth` identity and caller tries to link a second one: either reject by default or require explicit multi-identity policy.
3. `my-auth` user exists but `my-usermanager` user is missing:
   - if auto-provisioning disabled: return `None` / deny.
   - if explicit provisioning enabled: create/update only profile and identity link.
4. Profile fields differ between `my-auth` and `my-usermanager`:
   - default authority remains `my-usermanager` after initial provisioning.
   - optional sync can update `display_name` only when caller opts in.

## Public OSS readiness

Implementation must include a public-readiness phase:

1. Verify `my-auth` is public and MIT.
2. Verify `my-usermanager` license metadata and `LICENSE` are MIT.
3. Make `my-usermanager` GitHub repo public before documenting it as publicly installable.
   - Use `gh repo edit mikolaj92/my-usermanager --visibility public` during execution only after final confirmation in the execution session.
4. Update README install examples to use public GitHub coordinates.
5. Prefer tag-based install examples once tags exist.
6. Keep PyPI publishing out of this plan.

## Test strategy

Use TDD. Add failing tests before implementation.

Core tests:

- core import works without `my-auth`, FastAPI, or Pydantic installed.
- `AuthenticatedSubject` validates provider, subject, and user id.
- external identity resolver enforces uniqueness.
- invalid external subject maps to deterministic fallback user id.
- conflict errors are typed and stable.

Adapter tests:

- missing `my-auth` dependency produces actionable import error.
- `PasskeyUser.user_id` maps to `ExternalIdentity(provider="my-auth", subject=user_id)`.
- valid `PasskeyUser.user_id` remains canonical `User.user_id`.
- invalid `PasskeyUser.user_id` uses deterministic fallback while preserving original subject.
- profile bootstrap does not guess first/last names by splitting display names unless explicitly configured.
- adapter never grants roles or permissions.

FastAPI helper tests, if helper is implemented:

- `get_auth_user` returns `None` for missing/unlinked users.
- `after_register` links identity only when registration policy allowed.
- session behavior stays caller-owned.
- no FastAPI import from core.

Quality gates:

```bash
uv run --no-sync ruff format .
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync basedpyright
uv run --no-sync pytest
```

Measure pure LOC for every modified Python file. Any file over 250 pure LOC must be split before commit.

## Execution phases

### Phase 0: Public OSS and baseline

1. Check `GIT_MASTER=1 git status --short --branch`.
2. Confirm `my-usermanager` remote and current visibility.
3. Confirm MIT metadata/LICENSE.
4. Confirm current gates pass before code changes.
5. Decide exact public visibility command for execution.

### Phase 1: Core identity seam

1. Add tests for dependency-free subject values and identity resolution.
2. Add `subjects.py` or `identity.py` with dependency-free value objects/protocols/errors.
3. Add memory/contract support only if needed and without bloating warning-band modules.
4. Export only dependency-free symbols from core if useful.

### Phase 2: Optional my-auth adapter

1. Add tests for import guard and mapping.
2. Add `adapters/` package and `adapters/my_auth.py`.
3. Add optional `myauth` extra.
4. Ensure `import my_usermanager` does not import `my_auth`.

### Phase 3: Optional hook helpers

1. Add tests around `PasskeyRouteHooks` style behavior.
2. Add helpers only if they can remain optional and caller-policy-driven.
3. Do not own sessions.
4. Do not auto-grant access.

### Phase 4: Documentation

1. README: public OSS/MIT statement.
2. README: public GitHub install examples for both packages.
3. README: my-auth integration example.
4. README: no auto grants; host owns registration/session policy.
5. SECURITY/CONTRIBUTING if needed: public OSS expectations.

### Phase 5: Verification, commit, push, public repo

1. Run all quality gates.
2. Run LOC review.
3. Use `git-master`; every git command prefixed `GIT_MASTER=1`.
4. Commit in plain English style matching history.
5. Push to `origin/main`.
6. Make/verify GitHub repo public.
7. Verify public URL and clean synced branch.

## Residual risks

- Public API naming becomes compatibility-sensitive after documentation.
- Direct Git optional dependencies are less ergonomic than PyPI; tag-based docs reduce but do not eliminate this.
- Host apps can still bypass the adapter and use raw stores incorrectly; documentation must direct apps through the adapter/UserManager path.
- Profile synchronization between `my-auth` and `my-usermanager` can surprise users if automatic; keep sync opt-in.

## Handoff prompt for execution

Implement `.omo/plans/my-auth-adapter.md` in `/Users/mini-m4-1/Developer/my-usermanager`.

Follow TDD. Preserve dependency-free core imports. Add a core external-identity/subject seam, optional `my_usermanager.adapters.my_auth`, optional hook helpers only if they stay dependency-gated, public OSS/MIT docs, quality gates, LOC review, commit/push, and public repo visibility verification.
