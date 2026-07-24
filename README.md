# my-usermanager

`my-usermanager` is a framework-neutral Python package for user management and
authorization. It accepts an already-authenticated subject from a host or
authentication provider and provides typed users, external identities, roles,
grants, claims, sessions, stores, and the `UserManager` facade.

The core package is dependency-free and does not import FastAPI, Jinja,
Pydantic, `my-auth`, or adapter resources as an import side effect.

## Scope, install, and import paths

- Distribution: `my-usermanager`; core import: `my_usermanager`
- Python: `>=3.12`; license: MIT
- Extras: `myauth`, `fastapi`, and `fastapi-htmx`
- Explicit adapters: `my_usermanager.adapters.my_auth`,
  `my_usermanager.adapters.my_auth_fastapi`,
  `my_usermanager.adapters.my_auth_sqlite`,
  `my_usermanager.adapters.fastapi_htmx`

```sh
uv add "my-usermanager @ git+https://github.com/mikolaj92/my-usermanager.git"
uv add "my-usermanager[myauth] @ git+https://github.com/mikolaj92/my-usermanager.git"
uv add "my-usermanager[fastapi] @ git+https://github.com/mikolaj92/my-usermanager.git"
uv add "my-usermanager[fastapi-htmx] @ git+https://github.com/mikolaj92/my-usermanager.git"
```

For the shared passkey stack, install the `myauth` and UI extras plus the
public `my-auth` UI extra:

```sh
uv add "my-usermanager[myauth,fastapi-htmx] @ git+https://github.com/mikolaj92/my-usermanager.git"
uv add "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git"
```

## `my-auth` identity and FastAPI integration

```python
from my_auth import PasskeyUser
from my_usermanager.adapters.my_auth import passkey_user_to_authenticated_subject

subject = passkey_user_to_authenticated_subject(
    PasskeyUser("passkey_user_123", b"opaque-handle", "alice", "Alice Example")
)
identity = subject.external_identity()
```

The passkey ID remains `ExternalIdentity(provider="my-auth", subject=...)`.
The adapter uses it as the local `User.user_id` when valid, otherwise derives
a deterministic local fallback without changing the external subject.

`my_usermanager.adapters.my_auth_fastapi` supplies explicit helpers for the
current `my-auth.fastapi.PasskeyRouteHooks` contract. Preparation is pure;
the host decides policy and provisioning. Completion receives verified
registration and is the durable boundary:

```python
from my_usermanager.adapters.my_auth_fastapi import (
    build_complete_registration,
    build_get_auth_user,
    build_prepare_registration,
    require_passkey_route_hooks,
)

prepare = build_prepare_registration(profile_for_policy)
complete = build_complete_registration(
    lambda request, verified: auth_db.complete_registration(
        request, verified, user=provision_user(verified),
        identity=identity_for(verified), grants=grants_for(verified),
    )
)
PasskeyRouteHooks = require_passkey_route_hooks()
hooks = PasskeyRouteHooks(
    get_session_user=get_session_user,
    get_auth_user=build_get_auth_user(store, resolve_passkey_profile),
    prepare_registration=prepare,
    complete_registration=complete,
    login=login,
    logout=logout,
    registration_allowed=registration_allowed,
    render_login=render_login,
    render_register=render_register,
)
```

`build_prepare_registration` performs no writes. `build_complete_registration`
normalizes a synchronous or asynchronous host backend to an async callback.
The underlying `my-auth` router accepts sync or async callbacks for every hook;
registration policy runs before options and again before verify, then WebAuthn
verification precedes completion, login, and non-fatal observer hooks.

## Canonical shared SQLite owner

Use one `SQLiteAuthDatabase` for a product that stores both passkeys and
user-management records. It owns the configured database path/connection,
initialization, operation stores, and transaction boundaries:

```python
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase

auth_db = SQLiteAuthDatabase("app.sqlite3")
auth_db.initialize()                 # explicit inspect + initialize/migrate
stores = auth_db.stores()            # operation-mode stores
with auth_db.transaction() as tx:    # one atomic transaction
    user = tx.users.create(user)
    tx.external_store(my_auth_credential_store_factory).save_registration(verified)
```

`SQLiteAuthDatabase.complete_registration(request, result, *, user, identity,
grants=())` is the canonical atomic completion for a verified registration: it
commits the passkey, UM user, external identity, and grants together, or rolls
back all of them. Failed options and failed WebAuthn verification never reach
it. Host domain rows and policy state remain host-owned and are supplied after
policy verification.

The owner must call `initialize()` explicitly at startup. It inspects both
schemas, creates empty schemas, stamps canonical-unversioned layouts, and
migrates supported legacy layouts. Unsupported schemas and orphan grants are
refused. Initialization/migration requires no pending transaction. Inspection
is read-only; do not treat inspection as initialization.

`SQLiteAuthDatabase.stores()` returns operation-mode stores whose mutations
commit independently. Stores bound to `SQLiteAuthDatabase.transaction()` use
`transaction_mode="external"` (savepoints); the outer context commits or rolls
back. Do not instantiate independent auth and UM databases for one product.

Direct UM SQLite stores expose `create_tables`, `inspect_sqlite_schema`, and
`migrate_sqlite_schema`; they are synchronous. `create_tables` bootstraps
version 2, while migration validates orphan grants and rebuilds supported
legacy grant layouts atomically. Direct store constructors receive an open
`sqlite3.Connection`; the caller owns its lifecycle and transaction policy.
Path-owned shared stores use per-operation connections with
`check_same_thread=False`, busy timeout, WAL, and foreign keys. A caller-owned
connection remains thread-affine by default: use one connection per thread or
explicitly coordinate access. Never mix multiple connection owners for one
logical database.

## Ownership matrix

| Concern | Owner |
| --- | --- |
| Users, external identities, roles, grants, audit events | `my-usermanager` stores / host policy |
| Passkey users, credentials, challenges, auth schema | `my-auth` |
| One shared DB path and cross-library transaction | `SQLiteAuthDatabase` |
| Registration policy, local provisioning, identity conflict policy | host callbacks |
| Application sessions, cookies, CSRF, login/logout, audit side effects | host application |
| Claim projection and authorization decisions | `my-usermanager` primitives + host policy |
| HTML rendering, static mounts, route integration | optional adapters; host owns policy and persistence |

## Grant claims, admin service, and sessions

```python
from my_usermanager import GrantClaimsProjector, Permission, Scope, role_claim

projector = GrantClaimsProjector(
    roles=role_store,
    grants=grant_store,
    claim_mappers=(role_claim("is_admin", "admin"),),
)
projection = projector.project(user.user_id, scope=Scope.global_())
principal = projection.to_session_principal(user)
```

The default projection includes `is_admin`, roles, and permissions for the
requested scope. `GrantAdminService` centralizes safe grant mutations,
including protection against unsafe self-demotion and removing the last active
admin. Hosts choose when to compute or refresh session principals.

`write_session_principal` and `read_session_principal` serialize a typed
principal into a host-owned session mapping. For DB-backed sessions, keep the
cookie opaque and implement `SessionTokenStore`; the host owns session lifetime,
cookie settings, CSRF, login/logout, and persistence.

## FastAPI/Jinja/HTMX user-management UI

The optional adapter installs into the host's canonical app-factory shell and shared platform asset mount. It remains usable without `my-auth`; passkey UI is an optional typed panel hook.

```python
from app_factory.fastapi import install_app_factory_ui
from my_usermanager.adapters.fastapi_htmx import (
    UserManagerUiConfig, install_usermanager_ui,
)

platform = install_app_factory_ui(app, environments=(templates.env,))
ui = install_usermanager_ui(app, platform=platform, hooks=hooks, config=UserManagerUiConfig(csrf_protection=csrf))
```

The host owns sessions, CSRF validation, persistence, authorization, provisioning, redirects, and audit effects. Every enabled mutation route requires a `CsrfProtection` implementation and validates its submitted token before callbacks. Account and admin route groups can be independently disabled. The adapter ships only package-specific CSS and extends `app_factory/shell.html`; it never accepts arbitrary full-page template overrides.

## Canonical runnable FastAPI stack

The complete no-build reference is [`examples/fastapi_htmx`](examples/fastapi_htmx/README.md):

```sh
uv run --no-sync \
  --with-editable /Users/mini-m4-main/Developer/hermes-repos/my-auth \
  --with "fastapi>=0.115" \
  --with "jinja2>=3.1" \
  --with "uvicorn[standard]>=0.32" \
  uvicorn examples.fastapi_htmx.app:app --reload
```

Open `http://127.0.0.1:8000/auth/login`. The host demo uses in-memory users,
explicit demo-only CSRF metadata, and host callbacks; it is not a production
session, persistence, admin, or audit implementation. WebAuthn requires HTTPS
or a local secure context such as localhost, and `/api/auth/*` remains JSON.

## 0.1 to 0.2 mapping

| 0.1 API or behavior | 0.2 API or behavior |
| --- | --- |
| `build_make_registration_user_with_identity_link` | `build_prepare_registration` + host-owned `complete_registration` |
| `PasskeyRouteHooks.make_registration_user` | `prepare_registration` + `complete_registration` |
| `finish_registration` result passed to identity lookup | `verify_registration` returns `VerifiedRegistration`, then shared atomic completion |
| Implicit SQLite bootstrap | Explicit `SQLiteAuthDatabase.initialize()` or inspect then `create_tables`/`migrate_sqlite_schema` |
| Independent my-auth and UM DB owners | One `SQLiteAuthDatabase` owner and one shared configured connection/path |
| `transaction_mode` absent from direct stores | `operation` (independent commit) or `external` (savepoint; caller commits) |
| `PasskeyCookies.challenge` / `register_name` | Separate my-auth authentication/registration challenge cookies |
| `Initial version: 0.1.0` | `__version__ == "0.2.0"`; schema version 2 |

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run basedpyright src tests
```
