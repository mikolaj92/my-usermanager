"""Configuration values for the FastAPI HTMX user-manager UI adapter."""

from __future__ import annotations

# Required at runtime because get_type_hints evaluates postponed annotations.
from collections.abc import Mapping  # noqa: TC003
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import APIRouter
    from fastapi.staticfiles import StaticFiles
    from jinja2 import BaseLoader


@dataclass(frozen=True, slots=True)
class UserRow:
    """Host-supplied user row rendered by the reusable admin UI."""

    user_id: str
    row_key: str
    username: str
    display_name: str | None
    email: str | None
    disabled: bool
    is_admin: bool


@dataclass(frozen=True, slots=True)
class CsrfContext:
    """Host-provided CSRF field pairs and optional header metadata."""

    hidden_inputs: tuple[tuple[str, str], ...]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class UserManagerUiConfig:
    """Route, static, and template settings for the adapter."""

    account_path: str = "/account"
    users_path: str = "/admin/users"
    disable_user_path: str = "/admin/users/disable"
    enable_user_path: str = "/admin/users/enable"
    static_mount_path: str = "/usermanager/ui/static"
    static_url_path: str = "/usermanager/ui/static"
    login_url: str = "/auth/login"
    template_override_directory: Path | None = None
    template_loader: BaseLoader | None = None


@dataclass(frozen=True, slots=True)
class UserManagerUiRouter:
    """Router plus static mount values returned to host applications."""

    router: APIRouter
    static_mount_path: str
    static_files: StaticFiles
