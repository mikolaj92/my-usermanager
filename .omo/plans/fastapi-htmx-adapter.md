# Plan: coordinated FastAPI HTMX Basecoat adapters for my-auth and my-usermanager

Status: approved for plan; not approved for implementation.
User decision: option B — plan both adapters at once across both repositories.
Source draft: `.omo/drafts/fastapi-htmx-adapter.md`.
Durable notepad: `/var/folders/q2/7_fm0c4j1cv7t04tgk60szr00000gn/T/ulw-20260629-225318.XXXXXX.md.UZLvlZiKLJ`.
Momus review status: first Momus pass found blocking planning gaps; this version incorporates those required fixes.

## 0. Outcome

Build two coordinated, optional, importable FastAPI/Jinja/HTMX/Basecoat UI adapters:

1. `my_auth.fastapi_htmx` in `mikolaj92/my-auth`
   - Provides reusable passkey login/register HTML UI.
   - Reuses existing `my_auth.fastapi.PasskeyAuthRouter`, `PasskeyRouteHooks`, `PasskeyPaths`, `PasskeyCookies`, and `src/my_auth/static/passkey.js`.
   - Does not add passkey-core behavior and does not bloat `src/my_auth/fastapi.py` or `src/my_auth/passkeys.py`.

2. `my_usermanager.adapters.fastapi_htmx` in `mikolaj92/my-usermanager`
   - Provides reusable account/admin user-management HTML UI.
   - Composes with `my-auth` through host-provided callbacks and existing optional adapter boundaries.
   - Turns `examples/fastapi_htmx` into a consumer/composition example instead of the source of reusable behavior.

Clean root imports are mandatory:

- `import my_auth` must not import FastAPI, Jinja2, HTMX/static-template code, or `my_usermanager`.
- `import my_usermanager` must not import FastAPI, Jinja2, Pydantic, HTMX/static-template code, `my_auth`, or optional adapter modules.
- `import my_usermanager.adapters` must also remain a clean namespace import and must not import `fastapi_htmx` or any optional UI dependency.

## 1. Starting facts

### `my-usermanager`

- Local path: `/Users/mini-m4-1/Developer/my-usermanager`.
- Planning baseline observed on `main` at `c61c1e851df43a5f184257aef510bf801a07e14c`.
- Core dependency list is intentionally empty: `dependencies = []`.
- Existing extras: `fastapi = ["fastapi>=0.115", "pydantic>=2.10"]`; `myauth = ["my-auth @ git+https://github.com/mikolaj92/my-auth"]`.
- Existing optional adapters: `src/my_usermanager/adapters/my_auth.py` and `src/my_usermanager/adapters/my_auth_fastapi.py`.
- Current UI is example-only under `examples/fastapi_htmx/`.
- Current tests protect root import cleanliness, optional dependency boundaries, HTMX fragments, and forbidden host-policy ownership.
- `DESIGN.md` is the design-system source of truth.

### `my-auth`

- No local checkout was found at `/Users/mini-m4-1/Developer/my-auth` during planning.
- Planning evidence came from a read-only temp checkout at `/var/folders/q2/7_fm0c4j1cv7t04tgk60szr00000gn/T/opencode/my-auth`, branch `main`, HEAD `fdaf866a043c840c68c5eb31f56a4f07922b280a`, remote `https://github.com/mikolaj92/my-auth.git`.
- Implementation must locate or create a writable checkout before touching `my-auth`; the temp checkout is evidence only.
- Package target: Python `>=3.11`; runtime dependency `webauthn>=2.2,<3`; optional extra `fastapi = ["fastapi>=0.115"]`.
- `src/my_auth/__init__.py` exports only core passkey symbols and must stay FastAPI/Jinja/UI-free.
- Existing optional FastAPI boundary: `src/my_auth/fastapi.py`, exporting `AuthUser`, `PasskeyAuthRouter`, `PasskeyCookies`, `PasskeyPaths`, and `PasskeyRouteHooks`.
- Existing ES-module helper: `src/my_auth/static/passkey.js`, exporting `loginPasskey`, `registerPasskey`, and `passkeyEncoding`.
- Add a separate `src/my_auth/fastapi_htmx/` package; do not expand `my_auth.fastapi` or `my_auth.passkeys` for UI behavior.

## 2. Non-negotiable boundaries

- Host applications own app sessions, app cookies, CSRF validation, persistence, registration policy, local user provisioning, admin checks, role/grant changes, audit logging, redirects, and logout effects.
- The only adapter-owned cookie exception is the existing WebAuthn challenge-flow cookie behavior in `my_auth.fastapi.PasskeyAuthRouter` (`passkey_challenge` and `passkey_register_name`). Application/session cookies remain host-owned.
- UI dependencies are opt-in through a new UI-specific extra in both repos: `fastapi-htmx = ["fastapi>=0.115", "jinja2>=3.1"]`. Do not broaden existing lightweight `fastapi` extras silently.
- Runtime templates/static assets are real packaged files, verified from built wheels and installed-wheel imports.
- No React, shadcn, Tailwind, npm, bundler, SPA state, or JSON-plus-client-template UI.
- Use server-rendered HTML, HTMX fragments, Basecoat UI classes/tokens, and `DESIGN.md` tokens.
- No raw colors outside token definitions. No undocumented inline `style=` attributes or magic spacing; any layout exception must be named in `DESIGN.md` or the adapter CSS.
- All Python commands and docs use `uv`. No new `pip` or `python -m pip` examples.
- Implementation commands that invoke `git` or clone/fetch/switch branches must be prefixed with `GIT_MASTER=1`.

## 3. Exact shared contract

Define this contract before repo-specific implementation. Use `@dataclass(frozen=True, slots=True)` and `Protocol`; do not use Pydantic for adapter configuration.

Python compatibility rules:

- `my-auth` supports Python `>=3.11`; do not use Python 3.12+ syntax there. In particular, do not use `type MaybeAwaitable[T] = ...`.
- `my-auth` type alias pattern:

  ```python
  from collections.abc import Awaitable
  from typing import TypeVar

  T = TypeVar("T")
  MaybeAwaitable = T | Awaitable[T]
  ```

- `my-usermanager` supports Python `>=3.12`; do not use Python 3.13-only syntax or APIs even if local tests run on Python 3.13.

### Shared router return objects

Both factories return frozen dataclasses, not a bare `APIRouter`:

```python
@dataclass(frozen=True, slots=True)
class PasskeyUiRouter:
    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles


@dataclass(frozen=True, slots=True)
class UserManagerUiRouter:
    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles
```

Hosts mount explicitly:

```python
passkey_ui = create_passkey_ui_router(service=service, hooks=passkey_hooks)
app.include_router(passkey_ui.router)
app.mount(passkey_ui.static_mount_path, passkey_ui.static_files, name="my_auth_fastapi_htmx_static")

users_ui = create_usermanager_ui_router(config=user_ui_config, hooks=user_ui_hooks)
app.include_router(users_ui.router)
app.mount(users_ui.static_mount_path, users_ui.static_files, name="my_usermanager_fastapi_htmx_static")
```

Static helper names and return types are fixed:

- `passkey_ui_static_files() -> StaticFiles`
- `usermanager_ui_static_files() -> StaticFiles`

Template override mechanism is fixed:

1. If `template_loader` is supplied, use it.
2. Else if `template_override_directory` is supplied, use a `ChoiceLoader` with override directory first and packaged templates second.
3. Else use packaged templates only.
4. Supplying both `template_loader` and `template_override_directory` is invalid and must raise `ValueError`.

All route URLs must be prefix-safe and generated from config values or `url_for`; no hardcoded deployment path such as `/auth/static/passkey.js` may appear in templates except as a documented default value in config.

All mutating adapter forms parse `application/x-www-form-urlencoded` with `await request.body()` plus `urllib.parse.parse_qs`. Do not use `Request.form()` in adapter route handlers and do not require `python-multipart` for this scope.

## 4. Exact `my_auth.fastapi_htmx` contract

Target module: `src/my_auth/fastapi_htmx/`.

### Public API

```python
def create_passkey_ui_router(
    *,
    service: Any,
    hooks: PasskeyRouteHooks,
    config: PasskeyUiConfig | None = None,
) -> PasskeyUiRouter: ...
```

`__all__` must include exactly these UI symbols:

- `PasskeyUiConfig`
- `PasskeyUiRouter`
- `create_passkey_ui_router`
- `passkey_ui_static_files`

There is no separate `PasskeyUiHooks` public type in this scope. The host seam is the existing
`my_auth.fastapi.PasskeyRouteHooks`, wrapped only to supply packaged `render_login` and
`render_register` functions.

### `PasskeyUiConfig`

Define this frozen dataclass exactly:

```python
@dataclass(frozen=True, slots=True)
class PasskeyUiConfig:
    paths: PasskeyPaths
    cookies: PasskeyCookies
    static_mount_path: str
    static_url_path: str
    passkey_js_url: str
    template_override_directory: Path | None
    template_loader: BaseLoader | None
    csrf_header_name: str
    csrf_token: Callable[[Request], MaybeAwaitable[str | None]]
    login_success_url: str | None
    register_success_url: str | None
    login_error_target_id: str
    register_error_target_id: str
```

Default values:

- `paths = PasskeyPaths()`
- `cookies = PasskeyCookies()`
- `static_mount_path = "/auth/ui/static"`
- `static_url_path = "/auth/ui/static"`
- `passkey_js_url = "/auth/ui/static/passkey-ui.js"`
- `template_override_directory = None`
- `template_loader = None`
- `csrf_header_name = "X-CSRF-Token"`
- `csrf_token = no_csrf_token` where the default returns `None`
- `login_success_url = None`
- `register_success_url = None`
- `login_error_target_id = "passkey-login-status"`
- `register_error_target_id = "passkey-register-status"`

Success/error behavior is decided:

- Existing `/api/auth/*` WebAuthn endpoints remain JSON endpoints, including JSON error responses from `PasskeyAuthRouter`.
- `create_passkey_ui_router` includes exactly one `PasskeyAuthRouter(service=service, hooks=wrapped_hooks, paths=config.paths, cookies=config.cookies).router`.
- The UI adapter supplies `render_login` and `render_register` hooks on `wrapped_hooks` using packaged Jinja templates. It copies all non-render host callbacks from `hooks`.
- Do not duplicate login/register routes. If a future change needs duplicate routes, add a new plan and tests first.
- HTMX/HTML error fragments apply only to UI page/fragment rendering, not to the existing `/api/auth/*` JSON verify/options endpoints.
- `src/my_auth/fastapi_htmx/static/passkey.js` is the runtime-served ES module dependency for the UI adapter. It is a copied/re-exported copy or intentionally thin wrapper around `src/my_auth/static/passkey.js`.
- `passkey-ui.js` imports `./passkey.js` from the same `my_auth/fastapi_htmx/static` package directory and passes CSRF via `fetchOptions.headers` for all passkey JSON requests. It must not mutate WebAuthn credential JSON bodies.
- The single `StaticFiles` mount over `my_auth/fastapi_htmx/static` serves `passkey-ui.js`, `passkey.js`, and `passkey-ui.css`; do not depend on a second static mount for `my_auth/static` at runtime.
- Add a test that `fastapi_htmx/static/passkey.js` either byte-for-byte matches `my_auth/static/passkey.js` or intentionally wraps/re-exports it with documented differences.
- On JSON `{ "ok": true }`, the UI JS redirects only if `login_success_url` or `register_success_url` is configured; otherwise it renders a success status fragment/text in the configured target.

### Files

Create these files and keep pure Python modules under 250 nonblank/noncomment LOC:

```text
src/my_auth/fastapi_htmx/
  __init__.py
  config.py
  router.py
  templates.py
  static.py
  templates/
    base.html
    login.html
    register.html
    _login_panel.html
    _register_panel.html
    _passkey_status.html
  static/
    passkey.js
    passkey-ui.js
    passkey-ui.css
```

## 5. Exact `my_usermanager.adapters.fastapi_htmx` contract

Target module: `src/my_usermanager/adapters/fastapi_htmx/`.

### Public API

```python
def create_usermanager_ui_router(
    *,
    config: UserManagerUiConfig,
    hooks: UserManagerUiHooks,
) -> UserManagerUiRouter: ...
```

`src/my_usermanager/adapters/__init__.py` must not import or re-export this adapter. The import must remain explicit:

```python
from my_usermanager.adapters.fastapi_htmx import UserManagerUiConfig, create_usermanager_ui_router
```

`__all__` for `fastapi_htmx` must include exactly these UI symbols unless tests intentionally update it:

- `CsrfContext`
- `UserManagerUiConfig`
- `UserManagerUiHooks`
- `UserManagerUiRouter`
- `UserRow`
- `create_usermanager_ui_router`
- `row_key_from_user_id`
- `usermanager_ui_static_files`

### Dataclasses and protocols

```python
@dataclass(frozen=True, slots=True)
class UserRow:
    user_id: str
    row_key: str
    username: str
    display_name: str | None
    email: str | None
    disabled: bool
    is_admin: bool


@dataclass(frozen=True, slots=True)
class CsrfContext:
    hidden_inputs: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class UserManagerUiConfig:
    account_path: str
    users_path: str
    disable_user_path: str
    enable_user_path: str
    static_mount_path: str
    static_url_path: str
    login_url: str
    template_override_directory: Path | None
    template_loader: BaseLoader | None
```

Default config values:

- `account_path = "/account"`
- `users_path = "/admin/users"`
- `disable_user_path = "/admin/users/disable"`
- `enable_user_path = "/admin/users/enable"`
- `static_mount_path = "/usermanager/ui/static"`
- `static_url_path = "/usermanager/ui/static"`
- `login_url = "/auth/login"`
- `template_override_directory = None`
- `template_loader = None`

`UserManagerUiHooks` is a protocol with these exact callbacks:

```python
class UserManagerUiHooks(Protocol):
    def get_current_user(self, request: Request) -> MaybeAwaitable[AuthenticatedSubject | None]: ...
    def require_admin(self, request: Request, current_user: AuthenticatedSubject) -> MaybeAwaitable[None]: ...
    def list_users(self, request: Request, current_user: AuthenticatedSubject) -> MaybeAwaitable[Sequence[UserRow]]: ...
    def set_user_disabled(self, request: Request, current_user: AuthenticatedSubject, user_id: str, disabled: bool) -> MaybeAwaitable[UserRow]: ...
    def csrf_context(self, request: Request) -> MaybeAwaitable[CsrfContext]: ...
    def after_user_disabled_changed(self, request: Request, current_user: AuthenticatedSubject, row: UserRow) -> MaybeAwaitable[None]: ...
    def render_passkey_panel(self, request: Request, current_user: AuthenticatedSubject) -> MaybeAwaitable[Response | None]: ...
```

Policy/error behavior is fixed:

- `get_current_user` returning `None` yields `303` redirect to `config.login_url` for full-page GETs and a `401` HTML fragment for HTMX/mutating requests.
- `require_admin` returns `None` on success. It denies access only by raising `fastapi.HTTPException(status_code=403)` or `PermissionError`. The adapter converts denial to a `403` HTML page/fragment; it never infers roles or grants.
- `set_user_disabled` is the only mutation callback in scope. The adapter does not persist, audit, revoke sessions, or grant roles except by invoking host callbacks.

### Route table

Routes are fixed and configurable through `UserManagerUiConfig`:

| Method | Default path | Purpose | Response |
|---|---|---|---|
| `GET` | `/account` | account page for current user | full `text/html` page |
| `GET` | `/admin/users` | admin users page/table | full `text/html` page |
| `POST` | `/admin/users/disable` | disable user from hidden form field | `users/_row.html` fragment or HTML error fragment |
| `POST` | `/admin/users/enable` | enable user from hidden form field | `users/_row.html` fragment or HTML error fragment |

Do not put raw `user_id` in path params, DOM IDs, CSS selectors, or HTMX targets. Mutating forms use hidden, escaped `user_id` fields plus DOM-safe `row_key` fields. `row_key_from_user_id(user_id: str) -> str` must produce a stable DOM-safe value, e.g. a prefixed URL-safe SHA-256 digest. If a host supplies its own `row_key`, tests must prove it is DOM-safe and not equal to unsafe raw IDs.

### Files

Create these files and keep pure Python modules under 250 nonblank/noncomment LOC:

```text
src/my_usermanager/adapters/fastapi_htmx/
  __init__.py
  config.py
  protocols.py
  router.py
  templates.py
  static.py
  ids.py
  templates/
    base.html
    account/index.html
    users/list.html
    users/_row.html
    auth/_integration_panel.html
  static/
    usermanager-ui.css
```

Do not add `usermanager-ui.js` in this scope. Verification requires JavaScript assets only for `my-auth`; `my-usermanager` ships HTML/CSS only unless a later reviewed plan adds JS.

## 6. Phase 0 — repo setup, branch safety, and environment sync

Run this phase before any tests or implementation.

### `my-usermanager`

Workdir: `/Users/mini-m4-1/Developer/my-usermanager`.

1. Inspect state:

   ```bash
   GIT_MASTER=1 git status --short
   GIT_MASTER=1 git diff --stat
   GIT_MASTER=1 git branch --show-current
   GIT_MASTER=1 git rev-parse HEAD
   ```

2. Implementation may proceed only if the only pre-existing dirty/untracked files are:

   ```text
   .omo/drafts/fastapi-htmx-adapter.md
   .omo/plans/fastapi-htmx-adapter.md
   ```

   Any other dirty or untracked product path is a blocker and must be surfaced to the user before implementation.

3. Do not edit, stage, or commit `.omo` planning artifacts unless the user explicitly asks for planning artifacts to be committed.

4. Create a work branch before implementation:

   ```bash
   GIT_MASTER=1 git switch -c fastapi-htmx-adapter
   ```

   If the branch exists, inspect it with `GIT_MASTER=1 git status --short` and ask before reusing or switching.

5. Sync dev environment before any `uv run --no-sync ...` command:

   ```bash
   uv sync --dev
   ```

### `my-auth`

1. Locate or create a writable checkout:

   ```bash
   ls "/Users/mini-m4-1/Developer"
   ```

   If `/Users/mini-m4-1/Developer/my-auth` is absent:

   ```bash
   GIT_MASTER=1 gh repo clone mikolaj92/my-auth "/Users/mini-m4-1/Developer/my-auth"
   ```

   If `gh repo clone` is unavailable, use:

   ```bash
   GIT_MASTER=1 git clone https://github.com/mikolaj92/my-auth.git "/Users/mini-m4-1/Developer/my-auth"
   ```

2. Workdir: `/Users/mini-m4-1/Developer/my-auth`.

3. Inspect state:

   ```bash
   GIT_MASTER=1 git status --short
   GIT_MASTER=1 git diff --stat
   GIT_MASTER=1 git branch --show-current
   GIT_MASTER=1 git rev-parse HEAD
   GIT_MASTER=1 git remote -v
   ```

4. Stop if the checkout is dirty, not on the expected GitHub remote, or already contains unrelated local work.

5. Create a work branch before implementation:

   ```bash
   GIT_MASTER=1 git switch -c fastapi-htmx-adapter
   ```

6. Sync dev environment before any `uv run --no-sync ...` command:

   ```bash
   uv sync --dev
   ```

## 7. Phase 1 — failing tests first

Write failing tests before implementation. Confirm failures are for missing implementation/contract, not syntax errors or missing imports unrelated to the planned code.

Focused UI test commands use transient UI dependencies until the new extra exists. Because adapter route handlers must use stdlib URL-encoded parsing, do not add `python-multipart` and do not call `Request.form()`.

### `my-auth` red tests

Create `tests/test_fastapi_htmx_adapter.py` covering:

- Root import cleanliness: `import my_auth` does not load `fastapi`, `jinja2`, or `my_auth.fastapi_htmx`.
- Optional UI boundary: `import my_auth.fastapi_htmx` is the only UI import boundary and produces actionable missing-extra errors when FastAPI/Jinja2 are absent.
- Exact public API: `PasskeyUiConfig`, `PasskeyUiRouter`, `create_passkey_ui_router`, and `passkey_ui_static_files` exist with the exact return shape.
- `create_passkey_ui_router` constructs exactly one `PasskeyAuthRouter(...).router` and supplies packaged `render_login`/`render_register` hooks.
- Packaged template/static resources are visible through `importlib.resources`.
- Login/register pages render `text/html`, stable targets, Basecoat/HTMX markup, CSRF header metadata, unsupported-browser fallback, and ES-module `passkey-ui.js` wiring.
- Existing `/api/auth/*` endpoints remain JSON, including JSON error responses.
- Challenge cookies remain the only adapter-owned cookies.
- Prefix/static URL configuration works under non-root mounts.

Focused red command:

```bash
uv run --no-sync --with "fastapi>=0.115" --with "jinja2>=3.1" --with "httpx>=0.27" pytest tests/test_fastapi_htmx_adapter.py
```

### `my-usermanager` red tests

Create `tests/test_fastapi_htmx_adapter.py` covering:

- Root import cleanliness: `import my_usermanager` does not load optional UI deps or `my_auth`.
- Adapter namespace cleanliness: `import my_usermanager.adapters` does not load `fastapi`, `jinja2`, `pydantic`, `my_auth`, or `my_usermanager.adapters.fastapi_htmx`.
- Optional UI boundary: `import my_usermanager.adapters.fastapi_htmx` is explicit and produces actionable missing-extra errors when FastAPI/Jinja2 are absent.
- Exact public API: `UserRow`, `CsrfContext`, `UserManagerUiConfig`, `UserManagerUiHooks`, `UserManagerUiRouter`, `create_usermanager_ui_router`, `row_key_from_user_id`, and `usermanager_ui_static_files` exist with exact contracts.
- Packaged template/static resources are visible through `importlib.resources`.
- `/account`, `/admin/users`, `/admin/users/disable`, and `/admin/users/enable` render `text/html` pages/fragments and never JSON fragments.
- Host callbacks own current user, admin policy, CSRF, persistence, audit/session-revocation notifications, and passkey integration.
- Forbidden snippets are absent from adapter implementation: `request.session`, app `set_cookie(`, `grant_role(`, `grant_permission(`, `ADMIN_ROLE_NAME`.
- IDs containing punctuation, slashes, spaces, quotes, `<`, `>`, and `&` do not appear raw in DOM IDs, selectors, HTMX targets, or path params.
- Existing optional-dependency skip behavior in `tests/test_fastapi_htmx_example.py` is repaired so route-contract tests cannot silently skip when UI deps are present; specifically remove/fix the `httpx2` skip strategy if FastAPI/Jinja/httpx are available.

Focused red command:

```bash
uv run --no-sync --with "fastapi>=0.115" --with "jinja2>=3.1" --with "httpx>=0.27" pytest tests/test_fastapi_htmx_adapter.py tests/test_fastapi_htmx_example.py
```

### Installed-wheel/no-extra red checks

Add tests or documented verification snippets for both repos proving:

- A wheel installed without `fastapi-htmx` can import package root cleanly.
- Importing the UI adapter without UI deps fails with an actionable message naming the `fastapi-htmx` extra.
- Installing from built wheel with UI deps exposes packaged templates/static through `importlib.resources`.

## 8. Phase 2 — package metadata and wheel resources

Add UI-specific extras in both repos:

```toml
[project.optional-dependencies]
fastapi-htmx = ["fastapi>=0.115", "jinja2>=3.1"]
```

Do not add `my-auth` to `my-usermanager`'s `fastapi-htmx` extra. Cross-repo composition uses explicit editable installs in tests/examples.

Add exact Hatchling wheel resource entries.

### `my-auth` Hatchling entries

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/my_auth/static/passkey.js" = "my_auth/static/passkey.js"
"src/my_auth/fastapi_htmx/templates" = "my_auth/fastapi_htmx/templates"
"src/my_auth/fastapi_htmx/static" = "my_auth/fastapi_htmx/static"
```

The `src/my_auth/fastapi_htmx/static` directory must contain `passkey.js`, `passkey-ui.js`, and
`passkey-ui.css`. The root `src/my_auth/static/passkey.js` entry remains included for source parity
and backwards-compatible direct exposure, but the reusable UI serves the copied/re-exported
`my_auth/fastapi_htmx/static/passkey.js` from its single static mount.

### `my-usermanager` Hatchling entries

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/my_usermanager/adapters/fastapi_htmx/templates" = "my_usermanager/adapters/fastapi_htmx/templates"
"src/my_usermanager/adapters/fastapi_htmx/static" = "my_usermanager/adapters/fastapi_htmx/static"
```

Verification requires JavaScript package data only for `my-auth` in this scope. `my-usermanager` ships templates and CSS only.

Concrete wheel checks after `uv build` in `my-auth`:

```bash
uv run --no-sync python -c "import pathlib, zipfile; wheel=next(pathlib.Path('dist').glob('*.whl')); names=set(zipfile.ZipFile(wheel).namelist()); required=('my_auth/static/passkey.js','my_auth/fastapi_htmx/templates/base.html','my_auth/fastapi_htmx/templates/login.html','my_auth/fastapi_htmx/templates/register.html','my_auth/fastapi_htmx/templates/_login_panel.html','my_auth/fastapi_htmx/templates/_register_panel.html','my_auth/fastapi_htmx/templates/_passkey_status.html','my_auth/fastapi_htmx/static/passkey.js','my_auth/fastapi_htmx/static/passkey-ui.js','my_auth/fastapi_htmx/static/passkey-ui.css'); missing=[p for p in required if p not in names]; raise SystemExit('missing '+repr(missing) if missing else 0)"
```

Concrete wheel check after `uv build` in `my-usermanager`:

```bash
uv run --no-sync python -c "import pathlib, zipfile; wheel=next(pathlib.Path('dist').glob('*.whl')); names=set(zipfile.ZipFile(wheel).namelist()); required=('my_usermanager/adapters/fastapi_htmx/templates/base.html','my_usermanager/adapters/fastapi_htmx/templates/account/index.html','my_usermanager/adapters/fastapi_htmx/templates/users/list.html','my_usermanager/adapters/fastapi_htmx/templates/users/_row.html','my_usermanager/adapters/fastapi_htmx/templates/auth/_integration_panel.html','my_usermanager/adapters/fastapi_htmx/static/usermanager-ui.css'); missing=[p for p in required if p not in names]; raise SystemExit('missing '+repr(missing) if missing else 0)"
```

Concrete installed-wheel resource checks:

```bash
uv run --isolated --no-project --with "dist/my_auth-0.1.0-py3-none-any.whl" --with "fastapi>=0.115" --with "jinja2>=3.1" python -c "from importlib.resources import files; base=files('my_auth.fastapi_htmx'); required=('templates/base.html','templates/login.html','templates/register.html','templates/_login_panel.html','templates/_register_panel.html','templates/_passkey_status.html','static/passkey.js','static/passkey-ui.js','static/passkey-ui.css'); missing=[p for p in required if not base.joinpath(p).is_file()]; raise SystemExit('missing '+repr(missing) if missing else 0)"
uv run --isolated --no-project --with "dist/my_usermanager-0.1.0-py3-none-any.whl" --with "fastapi>=0.115" --with "jinja2>=3.1" python -c "from importlib.resources import files; base=files('my_usermanager.adapters.fastapi_htmx'); required=('templates/base.html','templates/account/index.html','templates/users/list.html','templates/users/_row.html','templates/auth/_integration_panel.html','static/usermanager-ui.css'); missing=[p for p in required if not base.joinpath(p).is_file()]; raise SystemExit('missing '+repr(missing) if missing else 0)"
```

If either wheel filename changes due to versioning, substitute only the concrete wheel filename after `uv build`; do not leave package/resource placeholders in the verification command.

## 9. Phase 3 — implement `my_auth.fastapi_htmx`

Implement after red tests and package metadata are in place.

Rules:

- Use `PasskeyAuthRouter` exactly once.
- Copy host non-render hooks from the supplied `PasskeyRouteHooks`; replace only `render_login` and `render_register` with packaged-template renderers.
- Keep all WebAuthn options/verify endpoints JSON.
- `passkey-ui.js` imports local `./passkey.js` from `my_auth/fastapi_htmx/static`; that file is the runtime-served copied/re-exported copy or intentional wrapper of `my_auth/static/passkey.js`.
- Test that `my_auth/fastapi_htmx/static/passkey.js` matches `my_auth/static/passkey.js` byte-for-byte, or that the wrapper intentionally re-exports/wraps the same `loginPasskey`, `registerPasskey`, and `passkeyEncoding` API with documented differences.
- Do not load passkey browser code as a plain deferred script; use ES modules.
- Send CSRF with `fetchOptions.headers` on passkey JSON requests.
- Do not call `Request.form()`.
- Do not add sessions, app cookies, persistence, registration policy, redirects, or logout behavior except through existing host hooks/configured client-side success URLs.
- Keep modules below 250 pure LOC; split if needed.

## 10. Phase 4 — implement `my_usermanager.adapters.fastapi_htmx`

Implement only after `my-auth` focused UI tests, root-import checks, and wheel resource checks pass locally.

Rules:

- Use the exact dataclasses/protocols from this plan.
- Use hidden `user_id` fields and DOM-safe `row_key`; do not use raw IDs in route params or DOM IDs.
- Use stdlib URL-encoded parsing and reject unsupported content types with HTML error fragments.
- Keep admin policy in `require_admin`; adapter denial behavior is fixed by this plan.
- No sessions, app cookies, grants, roles, persistence, redirects, or CSRF decisions except by invoking host callbacks/config.
- Render validation/policy errors as HTML fragments with stable targets and live regions.
- Follow `DESIGN.md`; no raw colors, undocumented inline styles, or magic spacing.
- Keep modules below 250 pure LOC; split if needed.

## 11. Phase 5 — composed example rewrite

Rewrite `examples/fastapi_htmx` as a consumer/composition app:

- Compose `my_auth.fastapi_htmx` from the local editable `my-auth` checkout.
- Compose `my_usermanager.adapters.fastapi_htmx` from the current repo.
- Keep demo-only host callbacks, no-op CSRF, in-memory users, and demo registration policy in the example app.
- Remove duplicated reusable templates/static from the example or keep them only as explicit override examples.
- Preserve useful route contracts where possible while using prefix-safe URL generation.
- Update `examples/fastapi_htmx/README.md` with UV-only run/test commands and host-owned security boundaries.

## 12. Phase 6 — docs

Update:

- `/Users/mini-m4-1/Developer/my-auth/README.md`
- `/Users/mini-m4-1/Developer/my-usermanager/README.md`
- `/Users/mini-m4-1/Developer/my-usermanager/examples/fastapi_htmx/README.md`

Docs must include:

- `uv add` / `uv sync` / `uv run` commands only.
- `fastapi-htmx` extra names.
- Public API examples using the exact factories/dataclasses in this plan.
- Explicit host-owned security responsibilities.
- The `my-auth` challenge-cookie exception.
- Static mounting and template override examples.
- Browser/WebAuthn secure-context notes.

## 13. Cross-repo sequencing gate

Hard gate: do not start `my-usermanager` composition work until local `/Users/mini-m4-1/Developer/my-auth` passes:

```bash
uv sync --dev
uv run --no-sync --with "fastapi>=0.115" --with "jinja2>=3.1" --with "httpx>=0.27" pytest tests/test_fastapi_htmx_adapter.py
uv run --no-sync python -c "import sys, my_auth; assert 'fastapi' not in sys.modules and 'jinja2' not in sys.modules"
uv build
```

Then run the repo-specific wheel resource checks from Phase 2.

Paired validation from `my-usermanager` must use the local editable checkout:

```bash
uv run --no-sync --with-editable /Users/mini-m4-1/Developer/my-auth --with "fastapi>=0.115" --with "jinja2>=3.1" --with "httpx>=0.27" pytest tests/test_fastapi_htmx_adapter.py tests/test_fastapi_htmx_example.py
```

Do not validate coordinated work against the remote `myauth` extra.

## 14. Verification gates

### `my-auth`

Run from `/Users/mini-m4-1/Developer/my-auth`:

```bash
GIT_MASTER=1 git status --short
GIT_MASTER=1 git diff --stat
uv sync --dev
uv run --no-sync pytest
uv build
```

Run lint/type gates in `my-auth` only if the implementation intentionally adds and configures them in `pyproject.toml`; do not invent a `ruff` gate for the current repo without adding/configuring it.

### `my-usermanager`

Run from `/Users/mini-m4-1/Developer/my-usermanager`:

```bash
GIT_MASTER=1 git status --short
GIT_MASTER=1 git diff --stat
uv sync --dev
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync basedpyright src tests
uv build
```

Also run the paired editable validation command from Phase 13.

### Import-boundary probes

Both repos need fresh-process probes and tests:

```bash
uv run --no-sync python -c "import sys, my_auth; forbidden={'fastapi','jinja2','my_auth.fastapi','my_auth.fastapi_htmx'}; loaded=forbidden & set(sys.modules); raise SystemExit(f'forbidden loaded: {loaded}' if loaded else 0)"
uv run --no-sync python -c "import sys, my_usermanager; forbidden={'fastapi','jinja2','pydantic','my_auth','my_usermanager.adapters.fastapi_htmx'}; loaded=forbidden & set(sys.modules); raise SystemExit(f'forbidden loaded: {loaded}' if loaded else 0)"
uv run --no-sync python -c "import sys, my_usermanager.adapters; forbidden={'fastapi','jinja2','pydantic','my_auth','my_usermanager.adapters.fastapi_htmx'}; loaded=forbidden & set(sys.modules); raise SystemExit(f'forbidden loaded: {loaded}' if loaded else 0)"
```

### Forbidden policy scan

Scan new adapter files and fail on:

- `request.session`
- app `set_cookie(` outside documented `my-auth` challenge-cookie code
- `grant_role(`
- `grant_permission(`
- `ADMIN_ROLE_NAME`
- direct persistence/session/redirect policy not routed through host callbacks/config

### HTTP/HTMX contracts

Use TestClient tests to verify:

- Full pages and fragments return `text/html`.
- UI/page/fragment errors are HTML, not JSON.
- `/api/auth/*` WebAuthn endpoints remain JSON, including JSON errors.
- Stable target IDs, `hx-target`, `hx-swap`, form actions, and indicators exist.
- Non-root prefixes work.
- CSRF hidden fields and passkey JS headers appear.
- DOM-safe IDs and selectors never contain raw unsafe user IDs.

### Browser/design QA

TestClient checks are required but not sufficient. Browser/design acceptance requires a live composed example.

Startup command from `/Users/mini-m4-1/Developer/my-usermanager`:

```bash
uv run --no-sync --with-editable /Users/mini-m4-1/Developer/my-auth --with "fastapi>=0.115" --with "jinja2>=3.1" --with "httpx>=0.27" --with "uvicorn[standard]>=0.32" uvicorn examples.fastapi_htmx.app:app --host 127.0.0.1 --port 8765
```

Evidence locations:

- Summary/action log: `.omo/evidence/fastapi-htmx-browser-qa.md`
- Screenshots: `.omo/evidence/fastapi-htmx/`

Required browser checks at 375, 768, and 1280 px:

- Login page, register page, account page, admin users page.
- Enable/disable user HTMX swap.
- Registration denied edge.
- Unsupported passkey browser or non-secure-context fallback.
- Keyboard navigation, focus visibility, labels, live regions, and error announcements.
- No console errors.
- No horizontal overflow on mobile.

Design-source audit:

- Scan templates/static for raw hex colors outside token definitions.
- Scan for undocumented `style=` attributes.
- Scan for magic spacing not represented in `DESIGN.md` tokens or adapter CSS.

### Docs audit

Search changed docs for `pip`, `python -m pip`, and non-UV Python command examples. All new/changed Python commands must use `uv`. Docs must explicitly state host-owned security responsibilities.

## 15. Acceptance criteria

Implementation is done only when all are true:

1. `my_auth.fastapi_htmx` exists, imports as an optional boundary, ships packaged templates/static, renders passkey login/register UI, and reuses existing passkey router/hooks without changing passkey core behavior.
2. `my_usermanager.adapters.fastapi_htmx` exists, imports as an optional boundary, ships packaged templates/static, renders account/admin user-management UI, and keeps host policy in callbacks.
3. `examples/fastapi_htmx` consumes the adapters instead of being the source of reusable behavior.
4. Root imports and `my_usermanager.adapters` import remain clean.
5. UI extras are opt-in and do not broaden existing lightweight extras silently.
6. Forbidden policy ownership scans pass, with only the documented `my-auth` challenge-cookie exception.
7. Wheel/package-data verification passes for both packages.
8. HTTP/HTMX route tests pass for full pages, fragments, error fragments, prefix mounting, CSRF seams, DOM-safe IDs, and JSON-vs-HTML endpoint boundaries.
9. Existing full test suites pass in both repositories using `uv`.
10. Browser/design QA passes at 375, 768, and 1280 px with evidence in `.omo/evidence/`.
11. All new/changed docs use `uv` only and describe host-owned security boundaries.

## 16. Known deferrals

- Do not build role/grant editors.
- Do not add production session management, CSRF implementation, persistence, or audit storage to either adapter.
- Do not create a React/shadcn/Tailwind component system.
- Do not publish to PyPI or tag releases unless separately requested.
- Do not commit automatically unless the user explicitly requests commits.

## 17. Suggested commit boundaries if commits are later requested

Use git-master rules and keep tests with implementation:

1. `my-auth`: failing tests, UI extra, package-data/resource plumbing.
2. `my-auth`: `fastapi_htmx` implementation and README docs.
3. `my-usermanager`: failing tests, UI extra, package-data/resource plumbing.
4. `my-usermanager`: `adapters.fastapi_htmx` implementation.
5. `my-usermanager`: example composition rewrite and docs.

If any commit touches more than three files, justify why those files are inseparable.
