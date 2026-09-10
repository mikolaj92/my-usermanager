# AGENTS.md

## Scope

`my-usermanager` is the framework-neutral user-management and authorization
library: typed users, external identities, roles, grants, claims, sessions,
stores, and the `UserManager` facade. Optional extras (`myauth`, `fastapi`,
`fastapi-htmx`) adapt that core to my-auth and FastAPI/HTMX. See `README.md`
and `DESIGN.md`.

The core package stays dependency-free. Do not import FastAPI, Jinja, Pydantic,
`my-auth`, or adapter resources as an import side effect.

## Design law

**Prefer small Unix-style modules and compose them.** Hosts compose identity UI
through `app_factory.adapters.install_identity_adapters`. Do not fork chrome or
installer glue, and do not call `install_usermanager_ui` from product hosts.

| Belongs here | Belongs in consumers |
|--------------|----------------------|
| Users, identities, roles, grants, claims, sessions, audit | Product routes, ORM, domain policy |
| `UserManager`, invitation/self-registration, last-admin invariants | Registration exposure, initial grants, CSRF, cookies |
| Explicit my-auth / FastAPI adapters and packaged admin/account pages | Chrome/shell, theme, platform assets (app-factory) |
| Shared SQLite owner (`SQLiteAuthDatabase`) for passkeys + UM | Host provisioning, identity conflict policy, product effects |

Do **not** absorb product workflows, Fala graphs, or host business logic.
Passkey ceremony and the minimal OpenID Provider live in `my-auth`. This package
is the local account/grants side of a swappable RP. Packaged pages extend
`app_factory/identity_authenticated_shell.html`. Pin one immutable BOM row;
nested sources in this package are app-factory v0.6.22 / my-auth v0.5.4 /
my-usermanager v0.6.5. The full matrix lives in app-factory
[`COMPAT.md`](https://github.com/mikolaj92/app-factory/blob/main/COMPAT.md)
— do not duplicate it here.

## Hard bans

- **No** re-implementing passkey ceremony (that lives in my-auth).
- **No** copying app-factory templates, theme/shell boots, or navigation chrome.
- **No** forking `install_usermanager_ui` / `install_passkey_ui` glue in hosts;
  use `install_identity_adapters`.
- **No** absorbing product workflows, Fala graphs, or host business logic.

## Preferred BOM

This package's nested pins (do not mix rows):

| app-factory | my-auth | my-usermanager |
|-------------|---------|----------------|
| v0.6.22 | v0.5.4 | v0.6.5 |

Hosts override `app-factory[platform]` only when bumping chrome; keep the three
direct pins on one COMPAT row. Source of truth:
https://github.com/mikolaj92/app-factory/blob/main/COMPAT.md
