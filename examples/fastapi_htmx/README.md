# FastAPI HTMX adapter composition example

This directory is a no-build FastAPI host app that composes two reusable UI
adapters instead of implementing reusable auth or user-management UI itself:

- `my_auth.fastapi_htmx.create_passkey_ui_router(...)` provides the passkey
  login and registration pages plus `/api/auth/*` JSON WebAuthn endpoints.
- `my_usermanager.adapters.fastapi_htmx.create_usermanager_ui_router(...)`
  provides the account page, admin users page, and HTMX user-row fragments.

The example remains an optional consumer. Importing or installing core
`my_usermanager` does not import FastAPI, Jinja, Pydantic, `my_auth`, HTMX,
Basecoat, React, Tailwind, an SPA shell, `npm` tooling, or a bundler.

Both reusable adapters are opt-in extras:

```sh
uv add "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git"
uv add "my-usermanager[fastapi-htmx,myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
```

## Run locally

Use the local editable `my-auth` checkout and temporary example-only runtime
dependencies. This keeps `my-usermanager` core dependencies unchanged.

```sh
uv run --no-sync \
  --with-editable /Users/mini-m4-1/Developer/my-auth \
  --with "fastapi>=0.115" \
  --with "jinja2>=3.1" \
  --with "uvicorn[standard]>=0.32" \
  uvicorn examples.fastapi_htmx.app:app --reload
```

Then open <http://127.0.0.1:8000/auth/login>.

Focused test command:

```sh
uv run --no-sync \
  --with-editable /Users/mini-m4-1/Developer/my-auth \
  --with "fastapi>=0.115" \
  --with "jinja2>=3.1" \
  --with "httpx>=0.27" \
  pytest tests/test_fastapi_htmx_example.py
```

## Adapter composition API

The passkey UI comes from `my_auth.fastapi_htmx`:

```python
from my_auth.fastapi_htmx import (
    PasskeyUiConfig,
    PasskeyUiRouter,
    create_passkey_ui_router,
    passkey_ui_static_files,
)

passkey_ui: PasskeyUiRouter = create_passkey_ui_router(
    service=_demo_passkey_service(),
    hooks=_passkey_hooks(),
    config=PasskeyUiConfig(
        paths=PASSKEY_PATHS,
        csrf_header_name=DEMO_CSRF_HEADER,
        csrf_token=_demo_csrf_token,
    ),
)
app.include_router(passkey_ui.router)
app.mount(
    passkey_ui.static_mount_path,
    passkey_ui.static_files,
    name="my_auth_fastapi_htmx_static",
)
```

`create_passkey_ui_router` returns `router`, `static_mount_path`, and
`static_files`. `passkey_ui_static_files()` is the public helper when a host
needs to create the packaged static mount directly. `PasskeyAuthRouter` still
owns the `/api/auth/*` JSON endpoints; the UI adapter supplies Jinja render
hooks and static files only.

The account/admin UI comes from `my_usermanager.adapters.fastapi_htmx`:

```python
from my_usermanager.adapters.fastapi_htmx import (
    CsrfContext,
    UserManagerUiConfig,
    UserManagerUiHooks,
    UserManagerUiRouter,
    UserRow,
    create_usermanager_ui_router,
    row_key_from_user_id,
    usermanager_ui_static_files,
)

hooks: UserManagerUiHooks = _usermanager_hooks()
usermanager_ui: UserManagerUiRouter = create_usermanager_ui_router(
    config=UserManagerUiConfig(login_url=PASSKEY_PATHS.login_page),
    hooks=hooks,
)
app.include_router(usermanager_ui.router)
app.mount(
    usermanager_ui.static_mount_path,
    usermanager_ui.static_files,
    name="my_usermanager_fastapi_htmx_static",
)

row = UserRow(
    user_id="unsafe/id space",
    row_key=row_key_from_user_id("unsafe/id space"),
    username="unsafe-user",
    display_name="Unsafe User",
    email="unsafe@example.invalid",
    disabled=False,
    is_admin=False,
)
csrf = CsrfContext(
    hidden_inputs=(("_demo_csrf", "demo-noop-csrf"),),
    headers={"X-Demo-CSRF": "demo-noop-csrf"},
)
```

`create_usermanager_ui_router` also returns `router`, `static_mount_path`, and
`static_files`. `usermanager_ui_static_files()` returns the packaged CSS static
mount if the host wires static serving separately.

## Template override contract

Both adapters use the same override order:

1. A custom Jinja `template_loader` wins.
2. Otherwise `template_override_directory` is searched before packaged
   templates.
3. Otherwise packaged templates are used.
4. Supplying both `template_loader` and `template_override_directory` is invalid
   and raises `ValueError`.

```python
from pathlib import Path
from jinja2 import DictLoader
from my_auth.fastapi_htmx import PasskeyUiConfig
from my_usermanager.adapters.fastapi_htmx import UserManagerUiConfig

passkey_templates = PasskeyUiConfig(
    template_override_directory=Path("app/templates/my_auth_fastapi_htmx"),
)
usermanager_templates = UserManagerUiConfig(
    template_loader=DictLoader({"account/index.html": "<main>Account</main>"}),
)
```

## Routes

| Route | Owner | Response | Purpose |
| --- | --- | --- | --- |
| `GET /` | example host | redirect | Sends users to `/auth/login`. |
| `GET /auth/login` | `my_auth.fastapi_htmx` | HTML | Passkey login page. |
| `GET /auth/register` | `my_auth.fastapi_htmx` | HTML | Passkey registration page. |
| `POST /api/auth/login/options` | `my_auth.fastapi.PasskeyAuthRouter` | JSON | WebAuthn login options. |
| `POST /api/auth/login/verify` | `my_auth.fastapi.PasskeyAuthRouter` | JSON | WebAuthn login verification. |
| `POST /api/auth/register/options` | `my_auth.fastapi.PasskeyAuthRouter` | JSON | WebAuthn registration options. |
| `POST /api/auth/register/verify` | `my_auth.fastapi.PasskeyAuthRouter` | JSON | WebAuthn registration verification. |
| `GET /account` | `my_usermanager.adapters.fastapi_htmx` | HTML | Account page with a host-rendered passkey panel. |
| `GET /admin/users` | `my_usermanager.adapters.fastapi_htmx` | HTML | Admin users table. |
| `POST /admin/users/disable` | `my_usermanager.adapters.fastapi_htmx` | HTML | HTMX row fragment after disabling one user. |
| `POST /admin/users/enable` | `my_usermanager.adapters.fastapi_htmx` | HTML | HTMX row fragment after enabling one user. |
| `GET /health` | example host | text | Local readiness probe. |

## Host-owned security boundaries

The host application owns sessions, app cookies, current-user lookup, CSRF
validation, persistence, registration policy, local user provisioning, admin
checks, role/grant changes, audit logging, redirects, and logout effects. In
this example those boundaries are deliberately demo-only:

Security ownership checklist: sessions; app cookies; CSRF validation;
persistence; registration policy; local user provisioning; admin checks;
role/grant changes; audit logging; redirects; logout effects.

- in-memory users only
- no-op demo CSRF hidden input and `X-Demo-CSRF` metadata only
- demo registration policy via `?registration=closed` for denial testing
- demo current user selected from local in-memory data
- demo admin requirement callback before user-management mutations
- no role/grant editor and no production audit logging
- passkey service and passkey hooks backed by in-memory scaffolding only

The adapter composition does not provide production sessions, does not provide production CSRF validation,
does not provide persistence, does not provide audit logging, does not provide role or grant editors,
and does not provide a production admin policy. The only adapter-owned cookies are the documented
my-auth WebAuthn challenge cookies (`passkey_challenge` and `passkey_register_name`) used by
`my_auth.fastapi.PasskeyAuthRouter`; adapters must not claim production app session/cookie ownership.

WebAuthn requires a secure browser context: HTTPS in production or a local
secure context such as localhost during development. Browsers without WebAuthn
support need host-provided fallback messaging or alternate account recovery.

## Frontend shape

The UI is server-rendered Jinja and HTMX with Basecoat-oriented markup from the
adapters. It swaps HTML fragments; `/api/auth/*` remains JSON for WebAuthn. This
example does not include React, shadcn, Tailwind, `npm`, a bundler, SPA state, or
client-side JSON templates.

The old duplicate example templates and `static/passkey.js` were removed because
the adapters now own the reusable UI resources. Add explicit override templates
only when demonstrating adapter override behavior.
