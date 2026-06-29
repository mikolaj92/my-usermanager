# FastAPI HTMX Basecoat example

This directory is a simple, clean FastAPI + Jinja + HTMX + Basecoat CDN login
and user-management proof for the active FastAPI HTMX Basecoat UI plan. It shows
how a host application can render auth, account, and admin-user screens around
`my-usermanager` concepts without turning `my_usermanager` into a web framework
package.

The example is optional and no-build. Importing or installing the core
`my_usermanager` package does not import or require FastAPI, Jinja, HTMX,
Basecoat, uvicorn, React, npm, a bundler, or a Tailwind build. Those pieces stay
inside this example host app or the command used to run it locally.

## Boundaries

`my-usermanager` stays framework-neutral. The host application owns all runtime
web policy, including:

- sessions, session storage, cookies, cookie flags, and logout behavior
- redirects after login, logout, registration, and denied access
- registration policy and local user provisioning
- admin checks and authorization decisions before admin routes mutate users
- identity conflict policy, including second-passkey or already-linked identity
  decisions
- CSRF protection, HTTPS, rate limiting, persistence, audit logging, and other
  production hardening
- whether templates are copied, overridden, replaced, or kept as a reference only

The demo functions in `app.py` are intentionally local host-policy hooks. They
do not grant roles, create real sessions, write cookies, or define production
registration rules for downstream applications.

## Frontend shape

The UI is server-rendered HTML:

- Jinja renders full pages and HTMX fragments.
- HTMX is loaded from a CDN and swaps HTML fragments, not JSON responses.
- Basecoat CSS and Basecoat JS are loaded from CDNs.
- `static/passkey.js` is tiny vanilla JavaScript for passkey demo hooks.
- There is no React, SPA shell, npm install, bundler, client-side JSON template,
  or Tailwind build step.

## Run locally

Run the demo with temporary example-only dependencies. This keeps core project
dependencies unchanged and avoids adding uvicorn to `pyproject.toml`:

```sh
uv run --no-sync \
  --with "fastapi>=0.115" \
  --with "jinja2>=3.1" \
  --with "uvicorn[standard]>=0.32" \
  uvicorn examples.fastapi_htmx.app:app --reload
```

Then open <http://127.0.0.1:8000/auth/login>.

The focused contract test remains:

```sh
uv run --no-sync pytest tests/test_fastapi_htmx_example.py
```

## Routes

| Route | Purpose |
| --- | --- |
| `GET /` | Redirects to `/auth/login`. |
| `GET /auth/login` | Full login page. |
| `POST /auth/login` | HTMX HTML fragment for `#auth-panel`. |
| `GET /auth/register` | Full registration page. |
| `POST /auth/register` | HTMX HTML fragment for `#register-panel`. |
| `GET /account` | Demo account and passkey page. |
| `GET /admin/users` | Demo admin user table page. |
| `POST /admin/users/{user_id}/disable` | HTMX HTML row fragment for one disabled user. |
| `POST /admin/users/{user_id}/enable` | HTMX HTML row fragment for one enabled user. |
| `GET /health` | Plain health response for QA. |

## Demo-only vs. production responsibility

Demo-only behavior:

- in-memory users
- simulated login and registration result fragments
- demo admin selection
- placeholder passkey browser hooks
- CDN assets and local templates used as a proof of the route/template contract

Production host responsibility:

- real session and cookie handling
- real passkey/WebAuthn integration, such as wiring browser credential calls to
  the host's `my-auth` routes
- durable user storage and explicit registration/provisioning policy
- admin authorization, identity-link conflict handling, and audit behavior
- CSRF and other production hardening for every mutating form
- template ownership decisions: copy, override, replace, or ignore this example
