"""Optional FastAPI, Jinja, HTMX, and Basecoat demo host app."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs
from warnings import filterwarnings

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from starlette.responses import Response

_BASE_DIR: Final = Path(__file__).resolve().parent
_DEMO_ADMIN_ID: Final = "demo-user"
_TEMPLATES: Final = Jinja2Templates(directory=str(_BASE_DIR / "templates"))
_ROUTER: Final = APIRouter()

filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated*",
    module="fastapi.testclient",
)


@dataclass(frozen=True, slots=True)
class DemoUser:
    """Host-owned user row for the in-memory demo UI."""

    user_id: str
    username: str
    display_name: str
    email: str
    admin: bool = False
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class FormValues:
    """Parsed URL-encoded form values from an HTMX request."""

    fields: dict[str, tuple[str, ...]]

    def first(self, field_name: str) -> str:
        """Return the first stripped field value, or an empty string."""
        values = self.fields.get(field_name, ())
        if values == ():
            return ""
        return values[0].strip()


@dataclass(frozen=True, slots=True)
class PanelMessage:
    """Server-rendered status copy for a swapped panel."""

    tone: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class RegistrationForm:
    """Typed registration form data owned by the demo host."""

    username: str
    display_name: str


def _initial_users() -> dict[str, DemoUser]:
    return {
        _DEMO_ADMIN_ID: DemoUser(
            user_id=_DEMO_ADMIN_ID,
            username="admin",
            display_name="Demo Administrator",
            email="admin@example.invalid",
            admin=True,
        ),
        "auditor-user": DemoUser(
            user_id="auditor-user",
            username="auditor",
            display_name="Audit Reviewer",
            email="auditor@example.invalid",
        ),
    }


_DEMO_USERS: Final = _initial_users()


def get_current_user() -> DemoUser:
    """Return the demo subject selected by the host application."""
    return _DEMO_USERS[_DEMO_ADMIN_ID]


def login(username: str) -> PanelMessage:
    """Apply demo login policy without creating sessions or cookies."""
    if username == "":
        return PanelMessage(
            tone="error",
            title="Username required",
            body="Enter a username to render the signed-in panel fragment.",
        )
    return PanelMessage(
        tone="success",
        title="Demo sign-in accepted",
        body=f"The host app would now establish a session for {username}.",
    )


def logout() -> PanelMessage:
    """Describe demo logout without mutating framework-neutral code."""
    return PanelMessage(
        tone="success",
        title="Signed out locally",
        body="A real host app would clear its own session cookie here.",
    )


def registration_allowed() -> bool:
    """Return the host-owned registration policy for the demo."""
    return True


def require_admin(user: DemoUser) -> DemoUser:
    """Require the caller-selected demo user to be an administrator."""
    if user.admin:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access is required for this demo action.",
    )


@_ROUTER.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect the bare demo root to the login screen."""
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


@_ROUTER.get("/health", response_class=PlainTextResponse)
def health() -> str:
    """Return a plain readiness response for browser QA."""
    return "ok"


@_ROUTER.get("/auth/login", response_class=HTMLResponse)
def show_login(request: Request) -> Response:
    """Render the full login page."""
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"current_user": get_current_user(), "message": None},
    )


@_ROUTER.post("/auth/login", response_class=HTMLResponse)
async def submit_login(request: Request) -> Response:
    """Render the login panel fragment for an HTMX form post."""
    form = await _read_form_values(request)
    message = login(form.first("username"))
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="auth/_login_panel.html",
        context={"message": message},
    )


@_ROUTER.get("/auth/register", response_class=HTMLResponse)
def show_register(request: Request) -> Response:
    """Render the full registration page."""
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="auth/register.html",
        context={"current_user": get_current_user(), "message": None},
    )


@_ROUTER.post("/auth/register", response_class=HTMLResponse)
async def submit_register(request: Request) -> Response:
    """Render the registration panel fragment for an HTMX form post."""
    form_values = await _read_form_values(request)
    registration = RegistrationForm(
        username=form_values.first("username"),
        display_name=form_values.first("display_name"),
    )
    message = _register(registration)
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="auth/_register_panel.html",
        context={"message": message},
    )


@_ROUTER.get("/account", response_class=HTMLResponse)
def show_account(request: Request) -> Response:
    """Render the account and passkey demonstration page."""
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="account/index.html",
        context={"current_user": get_current_user(), "logout_message": logout()},
    )


@_ROUTER.get("/admin/users", response_class=HTMLResponse)
def list_users(request: Request) -> Response:
    """Render the full demo user table page."""
    admin = require_admin(get_current_user())
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="users/list.html",
        context={"current_user": admin, "users": tuple(_DEMO_USERS.values())},
    )


@_ROUTER.post("/admin/users/{user_id}/disable", response_class=HTMLResponse)
def disable_user(request: Request, user_id: str) -> Response:
    """Disable one demo user and render only its table row fragment."""
    _ = require_admin(get_current_user())
    user = _replace_user(_require_user(user_id), disabled=True)
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="users/_row.html",
        context={"user": user},
    )


@_ROUTER.post("/admin/users/{user_id}/enable", response_class=HTMLResponse)
def enable_user(request: Request, user_id: str) -> Response:
    """Enable one demo user and render only its table row fragment."""
    _ = require_admin(get_current_user())
    user = _replace_user(_require_user(user_id), disabled=False)
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="users/_row.html",
        context={"user": user},
    )


def create_app() -> FastAPI:
    """Create the optional no-build example application."""
    demo_app = FastAPI(title="my-usermanager FastAPI HTMX Basecoat example")
    demo_app.mount(
        "/auth/static",
        StaticFiles(directory=_BASE_DIR / "static"),
        name="auth-static",
    )
    demo_app.include_router(_ROUTER)
    return demo_app


async def _read_form_values(request: Request) -> FormValues:
    body = (await request.body()).decode()
    parsed = parse_qs(body, keep_blank_values=True)
    return FormValues(
        fields={key: tuple(values) for key, values in parsed.items()},
    )


def _register(form: RegistrationForm) -> PanelMessage:
    if not registration_allowed():
        return PanelMessage(
            tone="error",
            title="Registration closed",
            body="The demo host policy is currently refusing new registrations.",
        )
    if form.username == "" or form.display_name == "":
        return PanelMessage(
            tone="error",
            title="Registration needs two fields",
            body="Enter both a display name and username to render this fragment.",
        )
    user_id = _user_id_from_username(form.username)
    _DEMO_USERS[user_id] = DemoUser(
        user_id=user_id,
        username=form.username,
        display_name=form.display_name,
        email=f"{user_id}@example.invalid",
    )
    return PanelMessage(
        tone="success",
        title="Demo registration accepted",
        body=f"The host app added {form.display_name} to its in-memory user list.",
    )


def _require_user(user_id: str) -> DemoUser:
    user = _DEMO_USERS.get(user_id)
    if user is not None:
        return user
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Demo user was not found.",
    )


def _replace_user(user: DemoUser, *, disabled: bool) -> DemoUser:
    updated = replace(user, disabled=disabled)
    _DEMO_USERS[user.user_id] = updated
    return updated


def _user_id_from_username(username: str) -> str:
    parts = [character if character.isalnum() else "-" for character in username]
    return "".join(parts).strip("-").casefold() or "registered-user"


app: Final = create_app()
