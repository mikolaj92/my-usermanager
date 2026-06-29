# FastAPI HTMX Basecoat UI adapter/example draft

status: awaiting-approval
pending_action: write `.omo/plans/fastapi-htmx-basecoat-ui.md`
slug: fastapi-htmx-basecoat-ui

## User request

Build a lightweight FastAPI + Jinja + HTMX + Basecoat UI proof/example/plugin layer for `my-auth` and `my-usermanager`.

The user wants the shared OSS app stack to stay consistent:

- FastAPI server-rendered apps.
- HTMX for requests/swaps/loaders.
- Basecoat UI via CDN.
- No React, no SPA, no bundler, no npm build.
- `my-auth` and `my-usermanager` must remain generic adapters/plugins, not full applications.

## Grounded repository findings

Repository: `/Users/mini-m4-1/Developer/my-usermanager`

- Current top level has `README.md`, `SECURITY.md`, `pyproject.toml`, `src/`, `tests/`, `.omo/`, but no `DESIGN.md`, no `examples/`, no `templates/`, and no `static/`.
- Current package contains core modules plus adapters:
  - `src/my_usermanager/subjects.py`
  - `src/my_usermanager/adapters/my_auth.py`
  - `src/my_usermanager/adapters/my_auth_fastapi.py`
- Existing tests cover core, subjects, `my-auth` adapter, optional FastAPI helper behavior, and import cleanliness.
- `pyproject.toml` keeps core runtime dependencies empty: `dependencies = []`.
- Optional extras are already split:
  - `fastapi = ["fastapi>=0.115", "pydantic>=2.10"]`
  - `myauth = ["my-auth @ git+https://github.com/mikolaj92/my-auth"]`
- README explicitly requires core import cleanliness: importing `my_usermanager` must not import FastAPI or Pydantic.
- README documents that host applications own sessions, login/logout, registration policy, provisioning, redirects, and grants.
- No design system exists yet, and the frontend skill requires `DESIGN.md` before UI components are implemented.
- `src/my_usermanager/adapters/my_auth_fastapi.py` is already near the pure-LOC warning band; the UI work should not grow this file. New optional UI code should be split into separate modules/files.

## External/stack findings already confirmed

- Basecoat supports CDN/no-build use:
  - CSS: `https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/basecoat.cdn.min.css`
  - JS all-components bundle: `https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/js/all.min.js`
- HTMX loader behavior should use the official `hx-indicator` / `.htmx-indicator` pattern.
- `my-auth` already provides passkey/WebAuthn backend flows plus a small vanilla browser helper pattern. It does not provide a full HTMX/Basecoat UI.

## Recommended approach

Plan and implement in this order:

1. Add `DESIGN.md` first.
   - Required by the frontend design-system gate.
   - Keep it minimal, utilitarian, and OSS-admin oriented.
   - Tokens should describe Basecoat/CDN usage, not invent a custom CSS framework.

2. Build `examples/fastapi_htmx/` first as the proof.
   - Proves the stack end-to-end before extracting reusable APIs.
   - Avoids prematurely baking a plugin API before browser QA.
   - Keeps core package behavior unchanged.

3. Add optional UI helper/plugin modules only after the example proves the route/template contract.
   - Candidate future modules:
     - `my_usermanager.adapters.fastapi_htmx`
     - eventually matching `my-auth` side helper/module in its own repo or separate package.
   - Do not add mandatory HTMX/Basecoat dependencies to core.

4. Keep all UI assets server-rendered and CDN/no-build.
   - Jinja templates return full pages and fragments.
   - HTMX swaps server-rendered HTML fragments.
   - Basecoat classes/components provide UI styling.
   - Small vanilla JS only for passkey/WebAuthn browser calls.

## Proposed proof app shape

```text
examples/fastapi_htmx/
  app.py
  README.md
  templates/
    base.html
    auth/login.html
    auth/register.html
    auth/_login_panel.html
    auth/_register_panel.html
    account/index.html
    users/list.html
    users/_row.html
  static/
    passkey.js
```

Suggested shared head contract:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/basecoat.cdn.min.css">
<script src="https://unpkg.com/htmx.org@2.0.4" defer></script>
<script src="https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/js/all.min.js" defer></script>
<script src="/auth/static/passkey.js" defer></script>
```

Suggested routes/fragments:

```text
GET  /auth/login          -> login page/panel
POST /auth/login          -> login result fragment
GET  /auth/register       -> registration page/panel
POST /auth/register       -> registration result fragment
GET  /account             -> account/passkey page
GET  /admin/users         -> users table
POST /admin/users/{id}/disable -> user row fragment
POST /admin/users/{id}/enable  -> user row fragment
```

## Scope IN

- `DESIGN.md` with minimal shared UI/design tokens and interaction rules.
- Example FastAPI/Jinja/HTMX/Basecoat proof app under `examples/fastapi_htmx/`.
- Tests for template rendering, HTMX fragment responses, loader markup, import cleanliness, and no new core dependencies.
- Browser QA plan for Basecoat CDN, HTMX swaps, `hx-indicator`, focus/labels/errors, and no-bundler operation.
- README/docs explaining that this is an optional example/plugin layer and host apps still own policy/session concerns.

## Scope OUT

- No full application conversion.
- No React, SPA, Tailwind build pipeline, npm, or bundler.
- No mandatory FastAPI/HTMX/Basecoat/Jinja dependency in `my_usermanager` core.
- No changes to `my-auth` repository unless explicitly planned as a separate follow-up.
- No custom QR-login/pairing protocol. Browser-native passkey/WebAuthn behavior remains the supported auth mechanism.

## Test and verification strategy

- TDD for Python route/template helper behavior where practical.
- Preserve existing gates:
  - `uv run --no-sync ruff format --check .`
  - `uv run --no-sync ruff check .`
  - `uv run --no-sync basedpyright`
  - `uv run --no-sync pytest`
- Add focused tests for:
  - example app import does not pollute core imports;
  - Basecoat/HTMX CDN links render in `base.html`;
  - `hx-indicator` appears in auth/user actions;
  - HTMX fragment endpoints return HTML fragments, not JSON;
  - host policy hooks are explicit and not bypassed;
  - user row enable/disable returns stable row targets.
- Browser QA after implementation:
  - run the example app;
  - verify desktop/tablet/mobile widths;
  - verify loader visibility during HTMX requests;
  - verify swaps, errors, empty states, focus, and keyboard paths;
  - verify no bundler/build step is required.

## Recommended defaults for unresolved decisions

1. Build proof/example first, not reusable plugin API first.
   - Reason: avoids premature API design before verifying real HTMX/Basecoat behavior.

2. Put the first proof in `my-usermanager/examples/fastapi_htmx/`.
   - Reason: this repo already contains the completed `my-auth` adapter and user-management integration seam.

3. Create `DESIGN.md` in project root during implementation.
   - Reason: required by frontend design-system gate and shared by future OSS apps.

4. Keep template override API minimal in the first pass.
   - Reason: prove route/fragment contracts first; extract reusable override conventions after browser QA.

5. Keep `my-auth` repo changes out of this first plan.
   - Reason: no local checkout is present; this repo can prove the integration through the public `my-auth` dependency first.

## Approval gate

The next action is to write `.omo/plans/fastapi-htmx-basecoat-ui.md` with a decision-complete implementation plan for a worker.

Approval requested for this approach:

- create a root `DESIGN.md` first;
- implement/verify `examples/fastapi_htmx/` as the first proof;
- use FastAPI + Jinja + HTMX + Basecoat via CDN + tiny vanilla passkey JS;
- preserve generic core imports and optional adapter/plugin boundaries;
- defer reusable package/plugin extraction until the proof is browser-tested.

If approved, write exactly one plan file: `.omo/plans/fastapi-htmx-basecoat-ui.md`.
