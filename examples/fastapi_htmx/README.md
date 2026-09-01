# FastAPI HTMX adapter composition example

This directory is a no-build FastAPI host app that composes two reusable UI
adapters instead of implementing reusable auth or user-management UI itself:

- `my_auth.fastapi_htmx.install_passkey_ui(...)` installs passkey login and
  registration pages plus `/api/auth/*` JSON WebAuthn endpoints.
- `my_usermanager.adapters.fastapi_htmx.install_usermanager_ui(...)` installs
  the account page, admin users page, and HTMX user-row fragments.
  Both installers receive the same `AppFactoryUi` platform.
The example remains an optional consumer. Importing or installing core
`my_usermanager` does not import FastAPI, Jinja, Pydantic, `my_auth`, HTMX,
Basecoat, React, Tailwind, an SPA shell, `npm` tooling, or a bundler.

Both reusable adapters are opt-in extras:

```sh
uv add "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git@v0.4.6"
uv add "my-usermanager[fastapi-htmx,myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
```

## Run locally

Use the published `my-auth` extra and temporary example-only runtime
dependencies. This keeps `my-usermanager` core dependencies unchanged.

```sh
uv run --no-sync \
  --with "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git@v0.4.6" \
  --with "fastapi>=0.115" \
  --with "jinja2>=3.1" \
  --with "uvicorn[standard]>=0.32" \
  uvicorn examples.fastapi_htmx.app:app --reload
```

Then open <http://127.0.0.1:8000/auth/login>.

Focused test command:

```sh
uv run --no-sync \
  --with "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git@v0.4.6" \
  --with "fastapi>=0.115" \
  --with "jinja2>=3.1" \
  --with "httpx>=0.27" \
  pytest tests/test_fastapi_htmx_example.py
```

The passkey UI uses the typed installer contracts:

```python
from app_factory.fastapi import install_app_factory_ui
from my_auth.fastapi_htmx import PasskeyUiConfig, install_passkey_ui
from my_usermanager.adapters.fastapi_htmx import UserManagerUiConfig, install_usermanager_ui

platform = install_app_factory_ui(app, environments=())
install_passkey_ui(app, platform=platform, service=service, hooks=passkey_hooks,
                   config=PasskeyUiConfig(paths=PASSKEY_PATHS))
install_usermanager_ui(app, platform=platform, hooks=usermanager_hooks,
                       config=UserManagerUiConfig(csrf_protection=csrf))
```

The host supplies typed `PasskeyPanel` and `CsrfProtection` implementations.
The latter validates submitted tokens before any user-management mutation.
The adapters own their routers, templates, and static mounts; hosts do not
call legacy `create_*` router factories or manually mount adapter static files.

The account/admin UI comes from `my_usermanager.adapters.fastapi_htmx`:

```python
from my_usermanager.adapters.fastapi_htmx import (
    CsrfContext,
    UserManagerUiConfig,
    UserManagerUiHooks,
    UserRow,
    install_usermanager_ui,
    row_key_from_user_id,
)

hooks: UserManagerUiHooks = _usermanager_hooks()
install_usermanager_ui(
    app,
    platform=platform,
    hooks=hooks,
    config=UserManagerUiConfig(
        login_url=PASSKEY_PATHS.login_page,
        csrf_protection=csrf,
    ),
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
csrf_fields = CsrfContext(
    hidden_inputs=(("_demo_csrf", "demo-noop-csrf"),),
    headers={"X-Demo-CSRF": "demo-noop-csrf"},
)
```

`install_usermanager_ui` owns the router, templates, and packaged CSS mount.

## Template override contract

Pass a host Jinja `environment` to `install_usermanager_ui` so packaged
templates attach with host loaders first. Set `UserManagerUiConfig.base_template`
to a host template that provides a `content` block. Chrome strings come from
`UserManagerUiConfig.labels` and an optional hooks `page_context` mapping.

```python
from jinja2 import Environment, FileSystemLoader
from my_usermanager.adapters.fastapi_htmx import UserManagerUiConfig, install_usermanager_ui

host_templates = Environment(loader=FileSystemLoader("app/templates"))
install_usermanager_ui(
    app,
    platform=platform,
    hooks=hooks,
    config=UserManagerUiConfig(
        csrf_protection=csrf,
        base_template="shell.html",
        labels={"nav_account": "Konto"},
    ),
    environment=host_templates,
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
- demo-only in-memory role/capability grant callbacks and no production audit logging
- passkey service and passkey hooks backed by in-memory scaffolding only

The adapter composition does not provide production sessions, does not provide production CSRF validation,
does not provide persistence, does not provide audit logging, does not provide production role/grant policy,
and does not provide a production admin policy. The only adapter-owned cookies are the documented
my-auth WebAuthn challenge cookies (`passkey_authentication_challenge` and
`passkey_registration_challenge`) used by `my_auth.fastapi.PasskeyAuthRouter`;
adapters must not claim production app session/cookie ownership.

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
