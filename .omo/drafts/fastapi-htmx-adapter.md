# FastAPI HTMX adapter draft

status: approved-for-plan
pending_action: run Metis gap analysis, then write `.omo/plans/fastapi-htmx-adapter.md`

## User intent

Build a real reusable UI adapter, not only an example/proof. The adapter should:

- use FastAPI + Jinja + HTMX + Basecoat UI;
- keep UI in real `.html` template files where possible;
- be importable by a host FastAPI app so it can include/router-mount the HTML UI;
- cover both `my-usermanager` and `my-auth` with coordinated importable adapters;
- use `uv` for all Python commands and documentation.

## Evidence already collected

- `DESIGN.md` exists and is the design source of truth. The core package must not require UI/template/HTMX/Basecoat/browser dependencies.
- Current UI lives under `examples/fastapi_htmx/` as a no-build proof, not under `src/my_usermanager/adapters/` as a reusable adapter.
- Current proof app combines policy, demo data, routes, and rendering in `examples/fastapi_htmx/app.py`; it should be split before expansion.
- Existing optional adapters are `src/my_usermanager/adapters/my_auth.py` and `src/my_usermanager/adapters/my_auth_fastapi.py`.
- No local `/Users/mini-m4-1/Developer/my-auth` checkout exists, so direct edits to `my-auth` require locating/cloning/fetching that repository first.
- A read-only temp checkout exists at `/var/folders/q2/7_fm0c4j1cv7t04tgk60szr00000gn/T/opencode/my-auth` on `main` at `fdaf866a043c840c68c5eb31f56a4f07922b280a` with remote `https://github.com/mikolaj92/my-auth.git`.
- Existing tests protect root import cleanliness, optional dependency boundaries, HTMX fragments, and forbidden host-policy ownership such as sessions, cookies, grants, and admin role changes.
- `my-usermanager` has `dependencies = []`, optional extras `fastapi = ["fastapi>=0.115", "pydantic>=2.10"]` and `myauth = ["my-auth @ git+https://github.com/mikolaj92/my-auth"]`, and Hatchling wheel package `src/my_usermanager` without explicit package-data rules yet.
- `my-usermanager` current example app is `examples/fastapi_htmx/app.py`; templates are under `examples/fastapi_htmx/templates/`, static helper under `examples/fastapi_htmx/static/passkey.js`, and docs explicitly call it optional/example-only.
- `my-auth` core import stays clean via `src/my_auth/__init__.py`; optional FastAPI boundary is `src/my_auth/fastapi.py` with `PasskeyAuthRouter`, `PasskeyRouteHooks`, `PasskeyPaths`, and `PasskeyCookies`.
- `my-auth` already ships vanilla WebAuthn helper `src/my_auth/static/passkey.js` with `loginPasskey` and `registerPasskey` defaulting to `/api/auth/...` routes.
- `my-auth` `pyproject.toml` uses Hatchling, Python `>=3.11`, dependency `webauthn>=2.2,<3`, optional `fastapi = ["fastapi>=0.115"]`, and no explicit package-data rules yet.
- `my-auth` FastAPI adapter is already 238 lines and `my_auth/passkeys.py` is 396 lines, so UI work should avoid bloating existing modules; use a separate `my_auth/fastapi_htmx/` package.

## Scope decision

User selected option B: plan two adapters at once, across both repositories. The plan must not assume `/Users/mini-m4-1/Developer/my-auth` exists; implementation should either use the temp checkout if intentionally adopted or begin by locating/cloning/fetching `my-auth` into an explicit worktree.

## Coordinated approach to plan

Adapter 1 should live in `my-usermanager`:

- add `src/my_usermanager/adapters/fastapi_htmx/` as the importable adapter;
- package templates under `src/my_usermanager/adapters/fastapi_htmx/templates/` and static helpers under `src/my_usermanager/adapters/fastapi_htmx/static/` if needed;
- expose `UserManagerUiConfig` plus `create_usermanager_ui_router(...)` or an equivalent small import API;
- keep all auth/session/cookie/CSRF/persistence/admin-policy decisions as host-provided callbacks/protocols;
- provide optional `my-auth` integration hooks through existing `my_auth_fastapi` boundaries without requiring `my_auth` on root import;
- rewrite `examples/fastapi_htmx` to consume the real adapter instead of duplicating it;
- add/adjust tests first and use only `uv run ...` commands.

Adapter 2 should live in `my-auth`:

- add `src/my_auth/fastapi_htmx/` as an optional import boundary separate from `my_auth.fastapi`;
- package templates under `src/my_auth/fastapi_htmx/templates/` and reuse or expose `src/my_auth/static/passkey.js` without forcing the host into a bundled frontend;
- expose a small API such as `PasskeyUiConfig`, `create_passkey_ui_router(...)`, or a helper that wires `PasskeyAuthRouter` render hooks to packaged Jinja templates;
- render login/register/passkey fragments with HTMX-compatible HTML while keeping app-owned sessions, CSRF, registration policy, and persistence in existing `PasskeyRouteHooks`;
- keep `import my_auth` free of FastAPI/Jinja/static-template imports;
- add tests around packaged templates/static, import boundaries, hook ownership, JS endpoint defaults, and rendered page/fragment behavior.

## Remaining decisions for the plan

No user interview is required before writing the plan. Defensible defaults:

- Extras: add new UI-specific extras rather than broadening existing optional extras silently, e.g. `fastapi-htmx` or `fastapi-ui`; exact names should be chosen in plan and docs.
- Package data: include/verify `.html`, `.css`/`.js` assets in built wheels for both packages via Hatchling configuration and/or tests.
- Static asset ownership: prefer helper functions/configurable mount paths over automatic app-global mounting where possible.
- UI scope: first reusable pass should cover login/register/passkey panels in `my-auth` and account/admin-user management surfaces in `my-usermanager`, preserving the existing example semantics.
- Verification: TDD, package-data tests, import-boundary subprocess tests, HTTP/HTML fragment tests, docs command checks, and browser QA for full pages at 375/768/1280.

## Plan gate

The scope decision is settled. Next step is mandatory Metis gap analysis, then write one decision-complete plan at `.omo/plans/fastapi-htmx-adapter.md`. Approval authorizes plan writing only, not product-code implementation.

## Test strategy

Default: TDD. Add failing tests first for root import cleanliness, adapter boundary imports, packaged templates/static files, host callback behavior, HTMX fragments, forbidden policy ownership, optional `my-auth` laziness, docs commands using `uv`, then implement.
