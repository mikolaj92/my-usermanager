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
- Optional extras: `my-usermanager[myauth]`, `my-usermanager[fastapi]`

The core package must stay framework neutral. Importing `my_usermanager` must not import FastAPI or Pydantic as side effects.

## Public OSS and install

`my-usermanager` is public OSS under the MIT License at <https://github.com/mikolaj92/my-usermanager>. `my-auth` is also public OSS under the MIT License at <https://github.com/mikolaj92/my-auth>.

No PyPI release is documented here. Install from the public GitHub repositories:

```sh
uv add "my-usermanager @ git+https://github.com/mikolaj92/my-usermanager.git"
python -m pip install "my-usermanager @ git+https://github.com/mikolaj92/my-usermanager.git"
```

Install the optional `my-auth` adapter extra from the same public repository:

```sh
uv add "my-usermanager[myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
python -m pip install "my-usermanager[myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
```

The `myauth` extra pulls `my-auth @ git+https://github.com/mikolaj92/my-auth`. Prefer a real release tag or commit pin for applications once one exists; do not rely on an invented tag.

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

## Optional FastAPI HTMX Basecoat example

See [`examples/fastapi_htmx`](examples/fastapi_htmx/README.md) for an optional no-build FastAPI/Jinja/HTMX/Basecoat proof. It uses CDN UI assets and a tiny vanilla passkey helper without making the framework-neutral core depend on FastAPI, Jinja, HTMX, Basecoat, React, npm, or a bundler.

The example is a host app reference only: the host remains responsible for sessions, cookies, redirects, registration policy, admin checks, identity conflict policy, CSRF/production hardening, and template ownership decisions.

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
