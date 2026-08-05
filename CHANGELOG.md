# Changelog

All notable changes to `my-usermanager` will be documented in this file.

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
