"""HTML response helpers for the FastAPI HTMX adapter."""

from __future__ import annotations

from html import escape

from fastapi import status
from fastapi.responses import HTMLResponse, Response


def forbidden_response() -> HTMLResponse:
    """Return the host-policy admin-denial fragment."""
    return error_response(
        status.HTTP_403_FORBIDDEN,
        "Admin access required",
        "The host application denied admin access for this request.",
    )


def error_response(status_code: int, title: str, message: str) -> HTMLResponse:
    """Return a stable HTML error fragment for HTMX targets."""
    markup = (
        '<section id="usermanager-ui-status" class="um-alert" '
        'role="alert" aria-live="assertive">'
        f"<h2>{escape(title)}</h2><p>{escape(message)}</p></section>"
    )
    return HTMLResponse(markup, status_code=status_code)


def response_text(response: Response) -> str:
    """Decode a host-rendered HTML response body."""
    return bytes(response.body).decode(response.charset)
