# Changelog

## Unreleased

- Persist OIDC state/nonce/PKCE in host SQLite bound to an httponly cookie
  digest. Replay, login CSRF, TTL, and a missing cookie fail closed; secrets
  stay out of the cookie (#152).
- Add `install_local_identity`: one FastAPI call mounts passkey login,
  account, and admin UI on a shared SQLite file, signed session, and CSRF.
- Point AGENTS.md nested pins at the current BOM row `v0.7.2 / v0.5.6 / v0.6.6`.
- Align the nested app-factory source with `v0.7.2`. Correct invitation docs
  to the my-auth 0.5 enrollment contract.
- Add `users.invite` to the built-in permission catalogue and admin role (#133).
- Verify OIDC `azp` when present and require it for multi-audience ID tokens (#151).
- Add host-owned JWKS cache with TTL, one refresh per unknown `kid`, and
  fail-closed fetch errors (#153).
- Load OpenID discovery only for a host-configured HTTPS issuer; mismatched
  issuer metadata and fetch failures stay fail-closed (#150).
- Add optional `require_step_up` / `MemoryStepUpStore` for host-gated
  destructive actions. Missing store leaves behavior unchanged (#144–#148).
- Prove the cheap later swap in-process: two issuer URLs, one local `user_id`,
  no `my_auth` on the product route. Keycloak is not required.

## 0.6.6

- Add `/oidc/login` that starts authorization-code + S256 PKCE at the
  configured issuer. One product "Sign in" control; passkeys stay on `/login`
  behind authorize.
- Add an optional same-origin OIDC relying-party callback: one-time
  state/nonce/PKCE, RS256 ID-token verification, and exact `(issuer, sub)`
  mapping onto an existing local user. No auto-link, no token minting, no
  OpenID Provider in this package.
- Add optional `deliver_issued_invitation` after durable commit. Hosts own
  transport; missing transport keeps the one-time manual link, while transport
  failure returns explicit `delivery_failed` without rolling back the pending
  invitation or pretending mail+SQL atomicity. Automatic delivery hides the raw
  URL unless the host reveals it. Core does not add SMTP, an outbox, or a broker.
- Record the product split: my-auth is a minimal, pluggable OpenID Provider;
  this package keeps local users, issuer/sub links, and grants so a host can
  swap the issuer without rewriting domain routes.
- Add GET filters and prev/next paging to the packaged users and audit
  panels, wrapping store `UserQuery` / `AuditFilters` and the shared
  app-factory pager. Legacy two-argument list hooks still work.
- Add the optional SQLite opaque session store with hashed tokens, TTL,
  owner-scoped revocation, explicit schema initialization, and host-driven
  principal refresh.
- Ship `src/my_usermanager/py.typed` so the `Typing :: Typed` classifier is
  true (#155). Drop the duplicate `dev` extra in favor of `[dependency-groups] dev` (#156).
- Align nested my-auth development source with `v0.5.6`.

## 0.6.5

- Add optional `StandardUserManagerUiHooks` for mechanical `UserManager`-backed row projection, profile updates, account transitions, and global role/permission mutations. Session lookup, administrator policy, role catalog, CSRF, invitations, auditing, and product side effects remain host-owned.
- Align development sources with app-factory `v0.6.22` and my-auth `v0.5.4`.

All notable changes to `my-usermanager` will be documented in this file.

## 0.6.2

- Pin the final neutral `my-auth` source tag `v0.5.1` (#105).

## 0.6.1

- Require an explicit host-selected role for every SQLite self-registration;
  registration order no longer confers a special first-user role (#99).
- Allow an administrator to revoke their own final admin grant when another
  active administrator remains, while retaining zero-admin lockout protection
  (#99).
- Require the neutral `my-auth` 0.5 line for the 0.6 API and release metadata
  (#103).

## 0.5.10

- Split SQLite schema lifecycle and store CRUD into single-purpose modules while
  keeping `my_usermanager.adapters.sqlite` as the stable public facade (#79).

- Teach the runnable FastAPI example to use app-factory's single
  `install_identity_adapters` composer instead of copying adapter installers (#81).

- Render account logout only through app-factory's `platform_session` and fail
  explicitly when the host omits `platform_paths` (#78).

- Make packaged user-manager pages extend app-factory's canonical authenticated
  identity shell without a local navigation or skip-link fork (#77).

- Align package metadata, `__version__`, and `SECURITY.md` at `0.5.10`.
  Nested sources use app-factory `v0.6.13` and my-auth `v0.4.7`; the public
  extra remains `my-auth>=0.4,<0.5`.

## 0.5.8

- Coordinate supported my-auth v2 legacy migration inside
  `SQLiteAuthDatabase.initialize()` so auth + UM + invitation DDL share one
  transaction (#66). Nested sources stay app-factory `v0.6.11` / my-auth
  `v0.4.6`.

## 0.5.7

- Re-export app-factory signed-session CSRF as `SessionCsrfProtection` (#63).
- Pin `[tool.uv.sources]` app-factory to `v0.6.11` and my-auth to `v0.4.6`
  (COMPAT chrome generation; hosts override `app-factory[platform]`). Extra
  stays `my-auth>=0.4,<0.5`.

## 0.5.6

- Pin nested my-auth to `v0.4.5` so packaged activation/recovery/credentials
  compose app-factory identity shells. Keep app-factory `v0.6.6`. Extra stays
  `my-auth>=0.4,<0.5`.
- Include extras `fastapi-htmx` and `myauth` in the `dev` group so default
  `uv sync` installs `app_factory` and `my_auth` for the test suite.
- Expose the same `dev` tools as a `dev` extra so `uv run --extra dev` works.

## 0.5.5

- `SQLiteAuthDatabase.initialize()` calls `my_auth.ensure_sqlite_schema` on
  already-current auth schemas so additive enrollment DDL
  (`passkey_enrollment_capabilities`) is stamped without a host second call.
- Pin `[tool.uv.sources]` app-factory to `v0.6.6` and my-auth to `v0.4.4`
  (extra stays `my-auth>=0.4,<0.5`) so nested chrome matches tagged TAP
  `client_shell`. Hosts only override `app-factory[platform]`.

## 0.5.4

- Pin `[tool.uv.sources]` my-auth to `v0.4.2` (app-factory stays `v0.6.4`) so
  the next BOM row can be app-factory `v0.6.5` / my-auth `v0.4.2` /
  my-usermanager `v0.5.4` and hosts only override `app-factory[platform]`.

## 0.5.3

- Pin `[tool.uv.sources]` to app-factory `v0.6.4` and my-auth `v0.4.1` so
  BOM v0.6.4 hosts only need to override `app-factory[platform]`.

## 0.5.2

- `SQLiteAuthDatabase.initialize()` stamps invitation metadata (`um_invitations`)
  in the same owned transaction as UM/auth schema via
  `create_invitation_tables(..., transaction_mode="external")`. Hosts no longer
  call `create_invitation_tables` after initialize.

## 0.5.1

- Complete account management flows (disable/enable, sessions, and audit in
  the admin UI).
- Remove SQLite legacy dual-read shims; inspect fails closed on legacy
  grant/audit layouts (#45).
- Protect the final active administrator from disable and revoke lockout
  (#46).
- Shared admin UI for invitation status, reissue, and revoke (#47).

## 0.4.5

- Convert profile validation failures into HTTP 400 responses instead of uncaught 500 errors.

## 0.4.4

- Fix FastAPI route annotations by importing `fastapi.responses.Response` at runtime.

## 0.4.0

- **Breaking:** `User.username` is required (passkey replaces password, not the handle).
- Optional profile demographics: `birth_date`, `gender` (`female` | `male` | `other`).
- `UserProfileUpdate` carries the same fields; empty first/last name clear to `None`.
- `UserStore.get_by_username` + `DuplicateUsernameError` (case-insensitive uniqueness).
- SQLite schema **v3** with migration from v2 (null usernames → `user_id`).
- Account UI: optional profile form when host implements `update_own_profile`.

## 0.3.3

- FastAPI/HTMX adapter: optional host Jinja `environment` on
  `install_usermanager_ui` (package templates attach with host loaders first).
- `UserManagerUiConfig.base_template` (default `base.html`) so account/admin
  pages can extend a host shell that provides a `content` block.
- `UserManagerUiConfig.labels` plus optional hooks `page_context` for chrome
  string overrides / per-request i18n (`DEFAULT_UI_LABELS`, `resolve_ui_labels`).
- Packaged templates use label keys; package CSS is linked from base and page
  content for host-shell embeds.

## 0.3.2

- Log out control on account page (platform session).

## 0.3.1

- Release typed FastAPI usermanager UI polish.

## 0.3.0

- Typed FastAPI/HTMX user-management UI adapter.

## 0.1.0

- Bootstrap repository skeleton with packaging, tests, docs, and CI.
