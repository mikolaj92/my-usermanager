# Changelog

All notable changes to `my-usermanager` will be documented in this file.

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
