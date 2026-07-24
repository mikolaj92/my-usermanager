"""Static file helpers for the FastAPI HTMX user-manager UI adapter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

if TYPE_CHECKING:
    from fastapi import FastAPI


def usermanager_ui_static_files() -> StaticFiles:
    """Return a StaticFiles mount for packaged adapter CSS."""
    static_directory = Path(__file__).with_name("static")
    return StaticFiles(directory=str(static_directory), check_dir=True)


def normalize_mount_path(path: str) -> str:
    """Normalize a mount path for equality and ancestry checks."""
    normalized = path.rstrip("/")
    return normalized or "/"


def mount_paths_overlap(left: str, right: str) -> bool:
    """Return True when mount paths are equal or nested under each other."""
    left_path = normalize_mount_path(left)
    right_path = normalize_mount_path(right)
    if left_path == right_path:
        return True
    if left_path == "/" or right_path == "/":
        return True
    return left_path.startswith(f"{right_path}/") or right_path.startswith(
        f"{left_path}/"
    )


def ensure_static_mount_available(app: FastAPI, mount_path: str) -> None:
    """Reject equal/parent/child overlap with already-mounted paths."""
    candidate = normalize_mount_path(mount_path)
    for route in app.routes:
        if not isinstance(route, Mount):
            continue
        existing = normalize_mount_path(route.path)
        if mount_paths_overlap(candidate, existing):
            message = (
                f"static mount path {candidate!r} overlaps existing mount {existing!r}"
            )
            raise ValueError(message)
