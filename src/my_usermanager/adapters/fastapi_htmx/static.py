"""Static file helpers for the FastAPI HTMX user-manager UI adapter."""

from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles


def usermanager_ui_static_files() -> StaticFiles:
    """Return a StaticFiles mount for packaged adapter CSS."""
    static_directory = Path(__file__).with_name("static")
    return StaticFiles(directory=str(static_directory), check_dir=True)
