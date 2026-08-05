# ruff: noqa: E402, PLC0415, S105
from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast

if TYPE_CHECKING:
    from fastapi import Request

    from my_usermanager.adapters.fastapi_htmx import (
        CapabilityOption,
        CsrfContext,
        CsrfProtection,
        PasskeyPanel,
        PermissionGrantRow,
        UserManagerUiHooks,
        UserRow,
    )

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`*")

import pytest
from app_factory.fastapi import AppFactoryUi, install_app_factory_ui
from fastapi import FastAPI
from fastapi.testclient import TestClient

from my_usermanager.subjects import AuthenticatedSubject

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
ADAPTER_MODULE: Final = "my_usermanager.adapters.fastapi_htmx"


def fresh(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_root_imports_do_not_load_ui_dependencies() -> None:
    script = """
import sys
import my_usermanager
import my_usermanager.adapters
for name in (
    "fastapi", "jinja2", "pydantic", "my_auth",
    "my_usermanager.adapters.fastapi_htmx",
):
    assert name not in sys.modules, name
"""
    result = fresh(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_ui_boundary_reports_missing_optional_dependencies() -> None:
    script = """
import importlib
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.', 1)[0] in {'fastapi', 'jinja2', 'app_factory'}:
            raise ModuleNotFoundError(fullname, name=fullname)
        return None
sys.meta_path.insert(0, Block())
try:
    importlib.import_module('my_usermanager.adapters.fastapi_htmx')
except ImportError as exc:
    assert 'fastapi-htmx' in str(exc)
else:
    raise AssertionError('adapter import unexpectedly succeeded')
"""
    result = fresh(script)
    assert result.returncode == 0, result.stderr


def test_public_api_and_resources_are_clean() -> None:
    import importlib.resources

    import my_usermanager.adapters.fastapi_htmx as adapter

    assert tuple(adapter.__all__) == (
        "DEFAULT_UI_LABELS",
        "CapabilityOption",
        "CsrfContext",
        "CsrfProtection",
        "ExternalIdentityRow",
        "PasskeyPanel",
        "PermissionGrantRow",
        "UserManagerUi",
        "UserManagerUiConfig",
        "UserManagerUiConflict",
        "UserManagerUiHooks",
        "UserManagerUiRouter",
        "UserRow",
        "install_usermanager_ui",
        "resolve_ui_labels",
        "row_key_from_user_id",
    )
    assert not hasattr(adapter, "create_usermanager_ui_static_files")
    assert not hasattr(adapter, "usermanager_ui_static_files")
    assert not hasattr(adapter.UserManagerUiConfig, "template_loader")
    assert not hasattr(adapter.UserManagerUiConfig, "template_override_directory")
    package = importlib.resources.files(adapter.__name__)
    for resource in (
        "templates/base.html",
        "templates/account/index.html",
        "templates/users/list.html",
        "templates/users/_row.html",
        "templates/auth/_integration_panel.html",
        "static/usermanager-ui.css",
    ):
        assert package.joinpath(resource).is_file(), resource


class FakeUiHooks:
    calls: list[object]
    panel: PasskeyPanel | None
    user: AuthenticatedSubject
    row: UserRow

    def __init__(
        self, *, panel: PasskeyPanel | None = None, calls: list[object] | None = None
    ) -> None:
        import my_usermanager.adapters.fastapi_htmx as adapter

        self.calls = calls if calls is not None else []
        self.panel = panel
        self.user = AuthenticatedSubject(
            provider="test", subject="subject", user_id="admin"
        )
        self.row = adapter.UserRow(
            user_id="user-1",
            row_key="user-1",
            username="user",
            display_name="User",
            email="user@example.test",
            disabled=False,
            is_admin=False,
        )

    def get_current_user(self, _request: Request) -> AuthenticatedSubject:
        return self.user

    def require_admin(
        self, _request: Request, _current_user: AuthenticatedSubject
    ) -> None:
        return None

    def list_users(
        self, _request: Request, _current_user: AuthenticatedSubject
    ) -> tuple[UserRow, ...]:
        return (self.row,)

    def role_options(
        self, _request: Request, _current_user: AuthenticatedSubject
    ) -> tuple[str, ...]:
        return ("member",)

    def capability_options(
        self, _request: Request, _current_user: AuthenticatedSubject
    ) -> tuple[CapabilityOption, ...]:
        return ()

    def set_user_disabled(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        _user_id: str,
        disabled: bool,
    ) -> UserRow:
        self.calls.append(("set", disabled))
        return self.row

    def grant_role(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        _user_id: str,
        _role_name: str,
    ) -> UserRow:
        self.calls.append("grant-role")
        return self.row

    def revoke_role(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        _user_id: str,
        _role_name: str,
    ) -> UserRow:
        self.calls.append("revoke-role")
        return self.row

    def grant_permission(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        _user_id: str,
        _permission: PermissionGrantRow,
    ) -> UserRow:
        self.calls.append("grant-permission")
        return self.row

    def revoke_permission(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        _user_id: str,
        _permission: PermissionGrantRow,
    ) -> UserRow:
        self.calls.append("revoke-permission")
        return self.row

    def csrf_context(self, _request: Request) -> CsrfContext:
        import my_usermanager.adapters.fastapi_htmx as adapter

        return adapter.CsrfContext((("csrf", "good"),), {})

    def after_user_disabled_changed(
        self, _request: Request, _current_user: AuthenticatedSubject, _row: UserRow
    ) -> None:
        self.calls.append("after")

    def render_passkey_panel(
        self, _request: Request, _current_user: AuthenticatedSubject
    ) -> PasskeyPanel | None:
        return self.panel


    def update_own_profile(
        self,
        _request: Request,
        _current_user: AuthenticatedSubject,
        update: object,
    ) -> AuthenticatedSubject:
        self.calls.append(("update", update))
        return self.user


class FakeCsrfProtection:
    valid: bool

    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    def token(self, _request: Request) -> str:
        return "good"

    def validate(self, _request: Request, submitted_token: str) -> object:
        if self.valid and submitted_token == "good":
            return None
        raise ValueError("bad csrf")  # noqa: EM101, TRY003


def _hooks(
    *, panel: PasskeyPanel | None = None, calls: list[object] | None = None
) -> UserManagerUiHooks:
    return cast(
        "UserManagerUiHooks", cast("object", FakeUiHooks(panel=panel, calls=calls))
    )


def _csrf(valid: bool = True) -> CsrfProtection:
    return cast("CsrfProtection", cast("object", FakeCsrfProtection(valid)))


class ResponseLike(Protocol):
    status_code: int
    text: str


class ClientLike(Protocol):
    def get(self, url: str) -> object: ...

    def post(
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> object: ...



def _client(app: FastAPI) -> ClientLike:
    return cast("ClientLike", cast("object", TestClient(app)))


def _response(response: object) -> ResponseLike:
    return cast("ResponseLike", response)


def _post(
    client: ClientLike,
    url: str,
    data: dict[str, str] | None = None,
    *,
    follow_redirects: bool = True,
) -> ResponseLike:
    return _response(client.post(url, data=data, follow_redirects=follow_redirects))



def _get(client: ClientLike, url: str) -> ResponseLike:
    return _response(client.get(url))


def test_route_toggles_and_csrf_guard() -> None:
    import my_usermanager.adapters.fastapi_htmx as adapter

    calls: list[object] = []
    hooks = _hooks(calls=calls)
    platform = AppFactoryUi(
        static_path="/static/platform",
        mount_name="platform",
        asset_prefix="/static/platform",
    )
    config = adapter.UserManagerUiConfig(csrf_protection=_csrf())
    app = FastAPI()
    _ = install_app_factory_ui(
        app,
        environments=[],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    ui = adapter.install_usermanager_ui(
        app, platform=platform, hooks=hooks, config=config
    )
    assert (
        adapter.install_usermanager_ui(
            app, platform=platform, hooks=hooks, config=config
        )
        is ui
    )
    client = _client(app)
    bad = _post(
        client,
        config.disable_user_path,
        {"user_id": "user-1", "csrf": "bad"},
    )
    assert bad.status_code == 403
    assert calls == []
    good = _post(
        client,
        config.disable_user_path,
        {"user_id": "user-1", "csrf": "good"},
    )
    assert good.status_code == 200
    assert calls == [("set", True), "after"]
    assert _get(client, config.account_path).status_code == 200
    assert _get(client, config.users_path).status_code == 200

    disabled = adapter.UserManagerUiConfig(
        account_enabled=False,
        admin_enabled=False,
        csrf_protection=None,
    )
    app2 = FastAPI()
    _ = install_app_factory_ui(
        app2,
        environments=[],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    client2 = _client(app2)
    assert _get(client2, disabled.account_path).status_code == 404
    assert _get(client2, disabled.users_path).status_code == 404
    assert _post(client2, disabled.disable_user_path).status_code == 404


def test_profile_update_rejects_future_birth_date_with_400() -> None:
    """Validation failures from UserProfileUpdate must map to HTTP 400."""
    import my_usermanager.adapters.fastapi_htmx as adapter

    from datetime import date, timedelta

    calls: list[object] = []
    hooks = _hooks(calls=calls)
    platform = AppFactoryUi(
        static_path="/static/platform",
        mount_name="platform",
        asset_prefix="/static/platform",
    )
    config = adapter.UserManagerUiConfig(csrf_protection=_csrf())
    app = FastAPI()
    _ = install_app_factory_ui(
        app,
        environments=[],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    _ = adapter.install_usermanager_ui(
        app, platform=platform, hooks=hooks, config=config
    )
    client = _client(app)
    future = (date.today() + timedelta(days=1)).isoformat()
    response = _post(
        client,
        config.profile_path,
        {
            "username": "admin",
            "birth_date": future,
            "csrf": "good",
        },
    )
    assert response.status_code == 400
    assert "Profile update failed" in response.text
    assert calls == []

    cleared = _post(
        client,
        config.profile_path,
        {
            "username": "admin",
            "csrf": "good",
        },
        follow_redirects=False,
    )
    assert cleared.status_code == 303
    assert calls and calls[0][0] == "update"


def test_passkey_panel_uses_named_packaged_template() -> None:
    import my_usermanager.adapters.fastapi_htmx as adapter

    panel = adapter.PasskeyPanel(
        template_name="auth/_integration_panel.html",
        context={"integration_name": "Passkeys"},
    )
    platform = AppFactoryUi(
        static_path="/static/platform",
        mount_name="platform",
        asset_prefix="/static/platform",
    )
    app = FastAPI()
    _ = install_app_factory_ui(
        app,
        environments=[],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    _ = adapter.install_usermanager_ui(
        app,
        platform=platform,
        hooks=_hooks(panel=panel),
        config=adapter.UserManagerUiConfig(csrf_protection=_csrf()),
    )
    response = _get(_client(app), "/account")
    assert response.status_code == 200
    assert "Passkeys" in response.text


def test_admin_requires_csrf_protection() -> None:
    import my_usermanager.adapters.fastapi_htmx as adapter

    with pytest.raises(ValueError, match="csrf_protection"):
        _ = adapter.UserManagerUiConfig(admin_enabled=True)
    _ = adapter.UserManagerUiConfig(admin_enabled=False)
    with pytest.raises(ValueError, match="base_template"):
        _ = adapter.UserManagerUiConfig(
            admin_enabled=False, base_template="   "
        )


def test_labels_and_host_base_template_are_applied() -> None:
    """Hosts may override chrome strings and extend a host shell template."""
    from jinja2 import ChoiceLoader, DictLoader, Environment, PackageLoader

    import my_usermanager.adapters.fastapi_htmx as adapter

    class LabeledHooks(FakeUiHooks):
        def page_context(self, _request: Request) -> dict[str, object]:
            return {
                "shell_marker": "host-shell-ok",
                "labels": {"users_title": "Uzytkownicy"},
            }

    host_base = (
        "<!doctype html><html><body>"
        "<div id='host-shell'>{{ shell_marker }}</div>"
        "{% block content %}{% endblock %}"
        "</body></html>"
    )
    environment = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"host_shell.html": host_base}),
                PackageLoader(
                    "my_usermanager.adapters.fastapi_htmx", "templates"
                ),
            ]
        ),
        autoescape=True,
    )
    platform = AppFactoryUi(
        static_path="/static/platform",
        mount_name="platform",
        asset_prefix="/static/platform",
    )
    app = FastAPI()
    _ = install_app_factory_ui(
        app,
        environments=[environment],
        static_path=platform.static_path,
        mount_name=platform.mount_name,
    )
    hooks = cast(
        "UserManagerUiHooks",
        cast("object", LabeledHooks()),
    )
    _ = adapter.install_usermanager_ui(
        app,
        platform=platform,
        hooks=hooks,
        config=adapter.UserManagerUiConfig(
            csrf_protection=_csrf(),
            base_template="host_shell.html",
            labels={"users_badge": "Administracja"},
            account_enabled=False,
        ),
        environment=environment,
    )
    response = _get(_client(app), "/admin/users")
    assert response.status_code == 200
    assert "host-shell-ok" in response.text
    assert "Uzytkownicy" in response.text
    assert "Administracja" in response.text
    assert "User management navigation" not in response.text


def test_resolve_ui_labels_merges_defaults_config_and_overrides() -> None:
    import my_usermanager.adapters.fastapi_htmx as adapter

    merged = adapter.resolve_ui_labels(
        {"users_title": "From config"},
        overrides={"users_title": "From request", "action_enable": "Wlacz"},
    )
    assert merged["users_title"] == "From request"
    assert merged["action_enable"] == "Wlacz"
    assert merged["nav_account"] == adapter.DEFAULT_UI_LABELS["nav_account"]
