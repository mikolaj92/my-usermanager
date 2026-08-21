# Changelog

All notable changes to `my-usermanager` will be documented in this file.

## Unreleased

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

## 0.1.0 - Unreleased

- Bootstrap repository skeleton with packaging, tests, docs, and CI.
