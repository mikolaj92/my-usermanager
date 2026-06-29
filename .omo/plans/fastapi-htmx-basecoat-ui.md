# FastAPI HTMX Basecoat UI proof plan

Status: approved-for-planning
Slug: `fastapi-htmx-basecoat-ui`
Draft: `.omo/drafts/fastapi-htmx-basecoat-ui.md`

## TL;DR

Build a small, verified FastAPI + Jinja + HTMX + Basecoat UI proof inside
`my-usermanager` without turning either `my-auth` or `my-usermanager` into a
full application.

The worker must first add a root `DESIGN.md`, then build
`examples/fastapi_htmx/` as a no-build example app that uses:

- FastAPI routes and thin host-owned policy hooks
- Jinja full pages and partial fragments
- HTMX for requests, fragment swaps, and `hx-indicator` loaders
- Basecoat UI through CDN assets
- tiny vanilla `passkey.js` only for browser WebAuthn/passkey calls

Do **not** extract reusable package/plugin modules until the example proves the
route/template contract in tests and browser QA.

## Non-negotiable constraints

1. `my_usermanager` core remains framework-neutral and dependency-light.
   `import my_usermanager` must not import `my_auth`, FastAPI, Pydantic, Jinja,
   HTMX, Basecoat, or browser/static UI code.
2. Core runtime dependencies in `pyproject.toml` stay empty unless explicitly
   re-approved.
3. FastAPI/Jinja/HTMX/Basecoat are allowed only in the example and optional
   integration surface, not in root/core imports.
4. Host applications own sessions, cookies, redirects, registration policy,
   admin checks, second-passkey/identity-conflict policy, and template override
   decisions.
5. The UI is server-rendered HTML. No React, SPA, client-side JSON templating,
   npm, bundler, Tailwind build step, or custom QR-login/pairing protocol.
6. Use Basecoat via CDN and HTMX via CDN in the example template. The proof must
   show loaders with official `hx-indicator` / `.htmx-indicator` behavior.
7. Do not expand `src/my_usermanager/adapters/my_auth_fastapi.py`; it is already
   near the pure-LOC warning band. Put example app code under `examples/`.
8. All git commands must be prefixed with `GIT_MASTER=1`.

## Current grounded facts

- Repository: `/Users/mini-m4-1/Developer/my-usermanager`.
- Existing adapter release is complete: `.omo/plans/my-auth-adapter.md` has
  Phase 0 through Phase 5 checked.
- Top level currently has no `DESIGN.md`, no `examples/`, no `templates/`, and
  no `static/`.
- `pyproject.toml` has `dependencies = []` and optional extras:
  - `fastapi = ["fastapi>=0.115", "pydantic>=2.10"]`
  - `myauth = ["my-auth @ git+https://github.com/mikolaj92/my-auth"]`
- Existing integration files:
  - `src/my_usermanager/subjects.py`
  - `src/my_usermanager/adapters/my_auth.py`
  - `src/my_usermanager/adapters/my_auth_fastapi.py`
- Existing tests cover core behavior, subjects, optional `my-auth` adapter,
  optional FastAPI helper hooks, and import cleanliness.

## Target file tree

Create the following new UI-proof files:

```text
DESIGN.md
examples/fastapi_htmx/
  README.md
  app.py
  static/
    passkey.js
  templates/
    base.html
    auth/
      login.html
      register.html
      _login_panel.html
      _register_panel.html
    account/
      index.html
    users/
      list.html
      _row.html
tests/
  test_fastapi_htmx_example.py
```

If `tests/test_fastapi_htmx_example.py` grows too large, split it by concern:

- `tests/test_fastapi_htmx_imports.py`
- `tests/test_fastapi_htmx_templates.py`
- `tests/test_fastapi_htmx_routes.py`

## TODOs

- [x] Phase 0: Add UI design system and baseline evidence before template implementation.
- [x] Phase 1: Add failing FastAPI HTMX example contract tests before implementation.
- [x] Phase 2: Implement the FastAPI HTMX Basecoat example app and templates.
- [x] Phase 3: Document the optional FastAPI HTMX example and host-owned policy boundaries.

## Final Verification Wave

- [x] Phase 4: Browser QA verifies Basecoat CDN, HTMX swaps, loaders, focus, responsive, empty, and error states.
- [x] Phase 5: Run final gates, import-cleanliness checks, and pure LOC review.

## Design system requirements

Create root `DESIGN.md` before any template implementation.

It must document the example UI design system in these seven sections:

1. Atmosphere & Identity
2. Color
3. Typography
4. Spacing & Layout
5. Components
6. Motion & Interaction
7. Depth & Surface

Recommended direction:

- Minimal, utilitarian OSS admin/auth UI.
- Warm off-white page surface, neutral foregrounds, subtle borders.
- Use Basecoat classes/tokens where possible; do not invent a custom CSS
  framework.
- Cards are crisp operational surfaces, not decorative shells.
- Motion is limited to opacity/transform and HTMX loader visibility.
- No emoji icons; if icons become necessary, use inline SVG or an explicit icon
  set later.

## Example app behavior

The example app must be a host application that wires the existing library and
adapter concepts together. It is allowed to use in-memory stores and fake/demo
host hooks so the UI can be tested without external services.

### Routes

Implement these routes in `examples/fastapi_htmx/app.py`:

```text
GET  /                         -> redirect or link to /auth/login
GET  /auth/login               -> full login page
POST /auth/login               -> login result fragment
GET  /auth/register            -> full registration page
POST /auth/register            -> registration result fragment
GET  /account                  -> account/passkey page
GET  /admin/users              -> users table page
POST /admin/users/{id}/disable -> single user row fragment
POST /admin/users/{id}/enable  -> single user row fragment
GET  /health                   -> plain health response for QA
```

Use `APIRouter`, typed route params, and `Request`/`Jinja2Templates` in the
standard FastAPI style. Keep handlers thin.

### Templates

`templates/base.html` must contain the shared CDN contract:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/basecoat.cdn.min.css">
<script src="https://unpkg.com/htmx.org@2.0.4" defer></script>
<script src="https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/js/all.min.js" defer></script>
<script src="/auth/static/passkey.js" defer></script>
```

Templates must use Basecoat classes/components such as `btn`, `card`, `input`,
`table`, `badge`, `alert`, `skeleton`, `spinner`, and `empty` where appropriate.

Forms/actions must use stable HTMX targets:

- Auth panel target: `#auth-panel`
- Register panel target: `#register-panel`
- Users table target: `#users-table`
- User row target: `#user-row-<user_id>`

Every HTMX action that mutates or verifies state must include an explicit
`hx-indicator` and a corresponding `.htmx-indicator` element.

Return HTML fragments for HTMX actions, not JSON.

### Passkey JavaScript

`static/passkey.js` should be intentionally tiny and vanilla. It should expose
small browser-side helpers for passkey login/registration demonstrations and
must not introduce a build step.

For the first proof, it is acceptable for server-side demo routes to return a
clear placeholder/simulated result when real browser credential APIs are not
available in tests. The UI contract still must be structured so a real
`navigator.credentials.get/create` implementation can call the existing
`my-auth` FastAPI routes later.

## Test-first implementation plan

### Phase 0 — Baseline and design gate

1. Inspect current status and recent history.
   - Command: `GIT_MASTER=1 git status --short --branch`
   - Command: `GIT_MASTER=1 git log --oneline -10`
2. Add `DESIGN.md` with the seven required sections above.
3. Run no product/UI code before `DESIGN.md` exists.

Acceptance:

- `DESIGN.md` exists at repo root.
- It defines the example's Basecoat/HTMX visual and interaction contract.

### Phase 1 — Contract tests before implementation

Add failing tests first for the example contract.

Required test coverage:

1. Core import cleanliness:
   - importing `my_usermanager` does not import FastAPI, Jinja, HTMX/Basecoat
     static code, `my_auth`, or Pydantic.
2. Example app isolation:
   - importing `examples.fastapi_htmx.app` is the only path that imports FastAPI
     and Jinja.
3. Base template contract:
   - Basecoat CSS CDN URL is present.
   - HTMX CDN URL is present.
   - Basecoat JS CDN URL is present.
   - `/auth/static/passkey.js` is present.
4. HTMX loader contract:
   - auth and user actions include `hx-indicator`.
   - indicator elements include `.htmx-indicator`.
5. Fragment contract:
   - `POST /auth/login` returns `text/html` and contains `#auth-panel` content.
   - `POST /auth/register` returns `text/html` and contains `#register-panel`
     content.
   - enable/disable user actions return the single `#user-row-<id>` fragment.
6. Host-policy contract:
   - registration/admin actions pass through explicit demo host policy helpers;
     they do not grant roles/admin/session implicitly inside the adapter layer.

Use FastAPI `TestClient` if available through the optional `fastapi` extra. If
the current test environment lacks optional UI dependencies, update the project
metadata only through optional extras/dev dependencies and keep core deps empty.

Acceptance:

- Tests fail for missing example files/routes before implementation.
- Test names clearly state Given/When/Then behavior.

### Phase 2 — Build the example app

Implement `examples/fastapi_htmx/app.py` and templates to satisfy tests.

Guidance:

- Use in-memory demo users based on existing `MemoryUserStore` and domain
  models where practical.
- Keep host hooks explicit and local to the example app:
  - `get_current_user`
  - `login`
  - `logout`
  - `registration_allowed`
  - `require_admin`
- Do not hide policy in `my_usermanager` core or existing adapter modules.
- Keep route handlers small: parse request, call demo host/store function,
  render template/fragment.
- Use ordinary forms/buttons first, then add `hx-*` attributes.
- Render validation/errors as the same fragment shape as success states.

Acceptance:

- Full pages render: `/auth/login`, `/auth/register`, `/account`,
  `/admin/users`.
- HTMX POST actions return fragments only.
- Loaders and stable targets are present in rendered HTML.
- No core import pollution.

### Phase 3 — Documentation

Add `examples/fastapi_htmx/README.md` and update the root `README.md` with a
short pointer to the example.

Document:

- The example is optional and no-build.
- Basecoat/HTMX are loaded via CDN.
- Host app owns sessions, redirects, registration policy, admin checks, and
  identity conflict policy.
- This is a proof/example, not a mandatory UI framework dependency.
- How to run the example with `uv run`.

Acceptance:

- A user can find and run the example from the root README.
- Docs do not imply `my-usermanager` core depends on FastAPI/Jinja/HTMX/Basecoat.

### Phase 4 — Browser QA

Run the example app and verify it in a real browser.

Use a command similar to:

```bash
uv run --extra fastapi --extra myauth uvicorn examples.fastapi_htmx.app:app --reload
```

If the project lacks `uvicorn` in optional/dev dependencies, add it only to an
optional example/dev path, not core dependencies.

Verify in browser:

- `/auth/login`
- `/auth/register`
- `/account`
- `/admin/users`
- mobile width around 375px
- tablet width around 768px
- desktop width around 1280px
- HTMX swaps on login/register/user enable/disable
- `hx-indicator` loader visibility during requests
- keyboard focus and visible focus states
- empty/error states
- Basecoat CDN styles and JS are loaded
- no bundler/build step is required

Record screenshot paths or browser evidence in the final implementation report.

Acceptance:

- Browser QA passes with no blocking visual/interaction defects.

### Phase 5 — Gates and LOC review

Run final gates:

```bash
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync basedpyright
uv run --no-sync pytest
```

Run focused optional/example gates if dependencies require extras:

```bash
uv run --extra fastapi --extra myauth pytest tests/test_fastapi_htmx_example.py
```

Measure pure LOC for all new/modified Python files. No file may exceed 250 pure
LOC. Avoid adding lines to `src/my_usermanager/adapters/my_auth_fastapi.py`.

Acceptance:

- All gates pass.
- No new or modified Python file exceeds 250 pure LOC.
- Import-cleanliness tests pass.

## Commit strategy

Use `git-master` rules. Every git command must be prefixed with `GIT_MASTER=1`.

Expected atomic commits:

1. `Add UI design system`
   - `DESIGN.md`
2. `Add FastAPI HTMX example tests`
   - focused tests for import/template/HTMX contracts
3. `Add FastAPI HTMX Basecoat example`
   - `examples/fastapi_htmx/app.py`
   - templates
   - `static/passkey.js`
4. `Document FastAPI HTMX example`
   - `examples/fastapi_htmx/README.md`
   - root `README.md` pointer
5. `Record FastAPI HTMX UI plan progress` if `.omo` execution artifacts are
   updated during implementation.

Include Sisyphus attribution footer/trailer if using the existing repo commit
workflow.

## Success criteria

This work is done only when all are true:

- Root `DESIGN.md` exists and governs the UI proof.
- `examples/fastapi_htmx/` runs as a no-build FastAPI/Jinja/HTMX/Basecoat demo.
- Basecoat and HTMX are loaded through CDN in the example.
- HTMX actions use stable targets and visible `hx-indicator` loaders.
- Login/register/account/users screens render and fragment-swap correctly.
- Tests prove import cleanliness, CDN/template contract, loader markup, fragment
  responses, and explicit host-policy boundaries.
- Browser QA verifies responsive, focus, loading, error, and empty states.
- Core imports and runtime dependencies remain clean.
- All quality gates pass.
- Docs clearly present this as an optional proof/example, not a mandatory UI
  dependency.

## Known follow-ups intentionally deferred

- Extracting reusable `my_usermanager.adapters.fastapi_htmx` plugin APIs.
- Adding a matching local `my-auth` repository UI plugin.
- Publishing versioned release tags or PyPI artifacts.
- Custom QR-code login/pairing. The example should rely on browser-native
  WebAuthn/passkey behavior only.
