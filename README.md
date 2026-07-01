# my-usermanager

`my-usermanager` is a framework-neutral Python package for user management and authorization. It accepts an already-authenticated subject from a host application or authentication provider, then provides typed authorization and user-management primitives.

The core package includes immutable domain values, store protocols, in-memory contract implementations, and a safe `UserManager` facade. The facade enforces the package's default policy: administrators manage user access grants, while ordinary users can update only their own basic profile fields such as username, first name, last name, display name, and email.

## Scope

- Core package import: `my_usermanager`
- Distribution name: `my-usermanager`
- Python: `>=3.12`
- Tooling: `uv`, Hatchling, pytest, Ruff, basedpyright
- License: MIT
- Optional adapter modules:
  - `my_usermanager.adapters.my_auth`
  - `my_usermanager.adapters.my_auth_fastapi`
  - `my_usermanager.adapters.fastapi_htmx`
- Optional extras: `my-usermanager[myauth]`, `my-usermanager[fastapi]`,
  `my-usermanager[fastapi-htmx]`

The core package must stay framework neutral. Importing `my_usermanager` must
not import FastAPI, Jinja, Pydantic, HTMX/static-template code, `my_auth`, or
optional adapter modules as side effects. Importing `my_usermanager.adapters`
also stays a clean namespace import; use explicit adapter imports.

## Public OSS and install

`my-usermanager` is public OSS under the MIT License at <https://github.com/mikolaj92/my-usermanager>. `my-auth` is also public OSS under the MIT License at <https://github.com/mikolaj92/my-auth>.

No PyPI release is documented here. Install from the public GitHub repositories:

```sh
uv add "my-usermanager @ git+https://github.com/mikolaj92/my-usermanager.git"
```

Install the optional `my-auth` adapter extra from the same public repository:

```sh
uv add "my-usermanager[myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
```

The `myauth` extra pulls `my-auth @ git+https://github.com/mikolaj92/my-auth`. Prefer a real release tag or commit pin for applications once one exists; do not rely on an invented tag.

Install the optional FastAPI/Jinja/HTMX UI adapter extra when rendering the
server-side user-management UI:

```sh
uv add "my-usermanager[fastapi-htmx] @ git+https://github.com/mikolaj92/my-usermanager.git"
```

When composing with the passkey UI from `my-auth`, install both public MIT
packages with their UI extras:

```sh
uv add "my-usermanager[fastapi-htmx,myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
uv add "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git"
```

## `my-auth` adapter

Install `my-usermanager[myauth]`, then map a `my-auth` passkey user into the dependency-free subject seam:

```python
from my_auth import PasskeyUser
from my_usermanager.adapters.my_auth import passkey_user_to_authenticated_subject

passkey_user = PasskeyUser(
    user_id="passkey_user_123",
    user_handle=b"opaque-passkey-handle",
    name="alice",
    display_name="Alice Example",
)

subject = passkey_user_to_authenticated_subject(passkey_user)
identity = subject.external_identity()
```

`PasskeyUser.user_id` is preserved as `ExternalIdentity(provider="my-auth", subject=...)`. When it is a valid `my-usermanager` identifier it is also used as the local `User.user_id`; otherwise the adapter derives a deterministic local fallback while preserving the original external subject.

## FastAPI hook helpers

`my_usermanager.adapters.my_auth_fastapi` provides small helpers for `my_auth.fastapi.PasskeyRouteHooks`. The host application still owns sessions, login/logout callbacks, registration policy, and local user provisioning:

Install both optional extras from the public repository when using these helpers: `my-usermanager[myauth,fastapi]`.

```python
from my_usermanager.adapters.my_auth_fastapi import (
    PasskeyRegistrationLink,
    PasskeyUserProfile,
    build_after_login_identity_linker,
    build_after_register_identity_linker,
    build_get_auth_user,
    build_make_registration_user_with_identity_link,
    require_passkey_route_hooks,
)


def registration_policy(request, display_name: str) -> PasskeyRegistrationLink:
    local_user = provision_local_user(request, display_name)  # host policy
    return PasskeyRegistrationLink(
        local_user_id=local_user.user_id,
        profile=PasskeyUserProfile(
            user_id=local_user.user_id,
            user_handle=make_passkey_handle(local_user),
            name=local_user.username or local_user.user_id,
            display_name=display_name,
        ),
    )


PasskeyRouteHooks = require_passkey_route_hooks()
hooks = PasskeyRouteHooks(
    get_session_user=get_session_user,  # host session lookup
    get_auth_user=build_get_auth_user(store, resolve_passkey_profile),
    make_registration_user=build_make_registration_user_with_identity_link(
        store,
        registration_policy,
    ),
    login=login,  # host writes its own session
    logout=logout,  # host clears its own session
    registration_allowed=registration_allowed,
    after_register=build_after_register_identity_linker(store),
    after_login=build_after_login_identity_linker(store),
)
```

The helpers return `None` for missing, unlinked, disabled, or policy-denied users so `my-auth` can deny access. They never create roles, permissions, admin grants, or sessions. Registration/provisioning must be an explicit host decision.

## FastAPI/Jinja/HTMX user-management UI adapter

`my_usermanager.adapters.fastapi_htmx` is an optional server-rendered
FastAPI/Jinja/HTMX/Basecoat adapter. It provides account/admin HTML pages and
HTMX row fragments while the host keeps all authentication, authorization, and
persistence decisions.

```python
from fastapi import FastAPI
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

app = FastAPI()
config = UserManagerUiConfig(login_url="/auth/login")
hooks: UserManagerUiHooks = build_host_hooks()

users_ui: UserManagerUiRouter = create_usermanager_ui_router(config=config, hooks=hooks)
app.include_router(users_ui.router)
app.mount(
    users_ui.static_mount_path,
    users_ui.static_files,
    name="my_usermanager_fastapi_htmx_static",
)

safe_key = row_key_from_user_id("external/id with spaces")
row = UserRow(
    user_id="external/id with spaces",
    row_key=safe_key,
    username="alice",
    display_name="Alice Example",
    email="alice@example.invalid",
    disabled=False,
    is_admin=False,
)
csrf = CsrfContext(
    hidden_inputs=(("csrf_token", host_csrf_token),),
    headers={"X-CSRF-Token": host_csrf_token},
)
```

`create_usermanager_ui_router` returns an object with `router`,
`static_mount_path`, and `static_files`. Mount using the returned values so
custom `UserManagerUiConfig.static_mount_path` values stay correct. The helper
`usermanager_ui_static_files()` returns the packaged `StaticFiles` object for
hosts that need to wire static files manually.

`UserManagerUiHooks` is the host callback seam. The adapter calls hooks for
current-user lookup, admin authorization, user listing, disabled-state changes,
CSRF metadata, post-mutation side effects, and optional passkey-panel HTML. It
does not create application users, sessions, roles, grants, audit records, or
cookies itself.

### Host-owned security responsibilities

The host application owns sessions, app cookies, CSRF validation, persistence,
registration policy, local user provisioning, admin checks, role/grant changes,
audit logging, redirects, and logout effects. The UI adapter only renders forms
and calls host hooks. It must not be documented or treated as production
security/session/persistence/admin/role/audit policy.

When composed with `my-auth`, the only adapter-owned cookie exception is the
existing WebAuthn challenge flow in `my_auth.fastapi.PasskeyAuthRouter`
(`passkey_challenge` and `passkey_register_name`). Those cookies are not
application session cookies.

### Template overrides

Template resolution follows the same contract as the passkey adapter:

1. A custom `template_loader` wins.
2. Otherwise `template_override_directory` is searched before packaged
   templates.
3. Otherwise packaged templates are used.
4. Supplying both `template_loader` and `template_override_directory` is
   invalid and raises `ValueError`.

```python
from pathlib import Path
from jinja2 import DictLoader
from my_usermanager.adapters.fastapi_htmx import UserManagerUiConfig

directory_override = UserManagerUiConfig(
    template_override_directory=Path("app/templates/usermanager"),
)
loader_override = UserManagerUiConfig(
    template_loader=DictLoader({"account/index.html": "<main>Account</main>"}),
)
```

The adapter is intentionally no-build: no React, shadcn, Tailwind, npm,
bundler, SPA shell, or client-side JSON templates. It is server-rendered
FastAPI/Jinja/HTMX/Basecoat.

## Optional FastAPI HTMX Basecoat example

See [`examples/fastapi_htmx`](examples/fastapi_htmx/README.md) for an optional no-build FastAPI/Jinja/HTMX/Basecoat proof. It composes packaged adapter templates/static resources without making the framework-neutral core depend on FastAPI, Jinja, HTMX, Basecoat, React, npm, or a bundler.

The example is a host app reference only: the host remains responsible for sessions, app cookies, CSRF validation, persistence, registration policy, local user provisioning, admin checks, role/grant changes, audit logging, redirects, logout effects, identity conflict policy, and template ownership decisions.

When the example composes `my-auth`, passkeys still require HTTPS or another
browser secure context such as localhost. Browsers without WebAuthn support need
host-provided fallback or recovery guidance, and `/api/auth/*` remains JSON.

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright src tests
```

## Version

Initial version: `0.1.0`
