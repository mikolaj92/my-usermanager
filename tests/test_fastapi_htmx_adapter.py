from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
ADAPTER_MODULE: Final = "my_usermanager.adapters.fastapi_htmx"
FORBIDDEN_UI_IMPORTS: Final = (
    "fastapi",
    "jinja2",
    "pydantic",
    "my_auth",
    ADAPTER_MODULE,
)


def run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def assert_subprocess_passed(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_root_and_adapters_imports_keep_ui_dependencies_out() -> None:
    # Given: fresh processes import package root and adapters namespace only.
    import_blocks = (("import my_usermanager",), ("import my_usermanager.adapters",))

    # When: each import runs in isolation.
    for import_block in import_blocks:
        script = "\n".join(
            (
                "import sys",
                *import_block,
                f"forbidden = {FORBIDDEN_UI_IMPORTS!r}",
                "loaded = [name for name in forbidden if name in sys.modules]",
                "assert loaded == [], loaded",
            ),
        )
        completed = run_fresh_python(script)

        # Then: optional UI deps, my-auth, and fastapi_htmx are not loaded.
        assert_subprocess_passed(completed)


def test_ui_boundary_errors_without_ui_deps() -> None:
    # Given: FastAPI and Jinja2 are blocked before the explicit adapter import.
    script = dedent(
        f"""
        import importlib
        import importlib.abc
        import sys

        class BlockUiDependencies(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname.split(".", 1)[0] in {{"fastapi", "jinja2"}}:
                    raise ModuleNotFoundError(fullname, name=fullname)
                return None

        sys.meta_path.insert(0, BlockUiDependencies())
        try:
            importlib.import_module({ADAPTER_MODULE!r})
        except ModuleNotFoundError as exc:
            if exc.name == {ADAPTER_MODULE!r}:
                raise AssertionError("missing planned fastapi_htmx module") from exc
            assert "fastapi-htmx" in str(exc), str(exc)
        except ImportError as exc:
            assert "fastapi-htmx" in str(exc), str(exc)
        else:
            raise AssertionError("adapter import succeeded while UI deps were blocked")
        """,
    )
    # When: the optional UI boundary is imported without UI dependencies.
    completed = run_fresh_python(script)
    # Then: the failure tells hosts to install the fastapi-htmx extra.
    assert_subprocess_passed(completed)


def test_public_api_resources_and_static_contracts() -> None:
    # Given: the planned adapter module exists as the explicit UI boundary.
    script = dedent(
        f"""
        import importlib
        import inspect
        from collections.abc import Mapping
        from dataclasses import fields, is_dataclass
        from importlib.resources import files
        from pathlib import Path
        from typing import get_type_hints

        adapter = importlib.import_module({ADAPTER_MODULE!r})
        public_api = (
            "CsrfContext", "UserManagerUiConfig", "UserManagerUiHooks",
            "UserManagerUiRouter", "UserRow", "create_usermanager_ui_router",
            "row_key_from_user_id", "usermanager_ui_static_files",
        )
        assert tuple(adapter.__all__) == public_api
        assert all(hasattr(adapter, name) for name in public_api)
        def field_names(class_):
            assert is_dataclass(class_) and class_.__dataclass_params__.frozen
            assert hasattr(class_, "__slots__")
            return tuple(field.name for field in fields(class_))
        expected_fields = {{
            adapter.UserRow: (
                "user_id", "row_key", "username", "display_name", "email",
                "disabled", "is_admin",
            ),
            adapter.CsrfContext: ("hidden_inputs", "headers"),
            adapter.UserManagerUiRouter: (
                "router", "static_mount_path", "static_files",
            ),
            adapter.UserManagerUiConfig: (
                "account_path", "users_path", "disable_user_path",
                "enable_user_path", "static_mount_path", "static_url_path",
                "login_url", "template_override_directory", "template_loader",
            ),
        }}
        for class_, names in expected_fields.items():
            assert field_names(class_) == names
        assert (
            adapter.CsrfContext.__annotations__["hidden_inputs"]
            == "tuple[tuple[str, str], ...]"
        )
        csrf_hints = get_type_hints(adapter.CsrfContext)
        assert csrf_hints["hidden_inputs"] == tuple[tuple[str, str], ...]
        assert csrf_hints["headers"] == Mapping[str, str]
        config = adapter.UserManagerUiConfig()
        defaults = (
            "/account", "/admin/users", "/admin/users/disable", "/admin/users/enable",
            "/usermanager/ui/static", "/usermanager/ui/static", "/auth/login",
            None, None,
        )
        config_fields = expected_fields[adapter.UserManagerUiConfig]
        actual_defaults = tuple(getattr(config, name) for name in config_fields)
        assert actual_defaults == defaults
        signature = inspect.signature(adapter.create_usermanager_ui_router)
        params = tuple(signature.parameters.values())
        assert tuple(param.name for param in params) == ("config", "hooks")
        assert all(param.kind is inspect.Parameter.KEYWORD_ONLY for param in params)
        assert all(param.default is inspect.Parameter.empty for param in params)
        expected_hooks = {{
            "get_current_user": ("self", "request"),
            "require_admin": ("self", "request", "current_user"),
            "list_users": ("self", "request", "current_user"),
            "set_user_disabled": (
                "self", "request", "current_user", "user_id", "disabled"
            ),
            "csrf_context": ("self", "request"),
            "after_user_disabled_changed": ("self", "request", "current_user", "row"),
            "render_passkey_panel": ("self", "request", "current_user"),
        }}
        for name, expected in expected_hooks.items():
            actual = inspect.signature(getattr(adapter.UserManagerUiHooks, name))
            assert tuple(actual.parameters) == expected
        required_resources = (
            "templates/base.html", "templates/account/index.html",
            "templates/users/list.html", "templates/users/_row.html",
            "templates/auth/_integration_panel.html", "static/usermanager-ui.css",
        )
        base = files({ADAPTER_MODULE!r})
        missing = [
            name for name in required_resources if not base.joinpath(name).is_file()
        ]
        assert missing == []
        row_template = base.joinpath("templates/users/_row.html").read_text(
            encoding="utf-8"
        )
        assert "csrf_inputs|safe" not in row_template
        assert "{{% for name, value in csrf_inputs %}}" in row_template

        source_root = Path("src/my_usermanager/adapters/fastapi_htmx")
        forbidden = (
            "request.session", "set_cookie(", "grant_role(",
            "grant_permission(", "ADMIN_ROLE_NAME",
        )
        assert source_root.is_dir()
        for source_path in source_root.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            assert all(snippet not in source for snippet in forbidden)
        """,
    )
    # When: API, dataclass, resource, and forbidden-source contracts are checked.
    completed = run_fresh_python(script)
    # Then: the planned public contract is present and host policy snippets are absent.
    assert_subprocess_passed(completed)


def test_routes_render_html_delegate_callbacks_and_hide_unsafe_ids() -> None:
    # Given: the adapter is mounted in FastAPI with host-owned callbacks.
    script = dedent(
        f"""
        import importlib, re, warnings
        from types import SimpleNamespace
        warnings.filterwarnings(
            "ignore", message="Using `httpx` with `starlette.testclient`*"
        )
        from fastapi import FastAPI, Response
        from fastapi.testclient import TestClient
        from my_usermanager.subjects import AuthenticatedSubject

        adapter = importlib.import_module({ADAPTER_MODULE!r})
        unsafe_id = "unsafe/id space\\\"quote'<tag>&tail"
        attr_pattern = re.compile(
            r'''(?:id|hx-target|hx-post|action)\\s*=\\s*["']([^"']+)["']'''
        )

        counts = dict.fromkeys(("current", "admin", "listed", "csrf", "passkeys"), 0)
        disabled_changes, after_changes = [], []

        def current_user(request):
            counts["current"] += 1; return AuthenticatedSubject(
                provider="test", subject="subject-1", user_id="admin-user"
            )

        def require_admin(request, current_user):
            assert current_user.user_id == "admin-user"; counts["admin"] += 1

        def row(disabled):
            values = {{
                "user_id": unsafe_id,
                "row_key": adapter.row_key_from_user_id(unsafe_id),
                "username": "unsafe-user", "display_name": "Unsafe User",
                "email": "unsafe@example.invalid", "disabled": disabled,
                "is_admin": False,
            }}
            return adapter.UserRow(**values)

        def list_users(request, current_user):
            counts["listed"] += 1; return (row(False),)

        def set_disabled(request, current_user, user_id, disabled):
            disabled_changes.append((user_id, disabled)); return row(disabled)

        def csrf_context(request):
            counts["csrf"] += 1
            return adapter.CsrfContext(
                hidden_inputs=(
                    ("csrf", "<token&value>"),
                    ("csrf_html", '<input name="owned" value="unsafe">'),
                ),
                headers={{"X-CSRF-Token": "v"}},
            )

        def after_changed(request, current_user, changed_row):
            after_changes.append(changed_row.user_id)

        def passkey_panel(request, current_user):
            counts["passkeys"] += 1; return Response("<p>Passkeys</p>")

        hooks = SimpleNamespace(
            get_current_user=current_user, require_admin=require_admin,
            list_users=list_users, set_user_disabled=set_disabled,
            csrf_context=csrf_context, after_user_disabled_changed=after_changed,
            render_passkey_panel=passkey_panel,
        )
        config, app = adapter.UserManagerUiConfig(), FastAPI()
        ui = adapter.create_usermanager_ui_router(config=config, hooks=hooks)
        app.include_router(ui.router); app.mount(ui.static_mount_path, ui.static_files)
        client = TestClient(app)
        bad_response = client.post(
            config.disable_user_path,
            content=b"user_id=%FF",
            headers={{"content-type": "application/x-www-form-urlencoded"}},
        )
        bad_content_type = bad_response.headers.get("content-type", "")
        assert bad_response.status_code == 400, bad_response.text
        assert bad_content_type.startswith("text/html"), bad_content_type
        assert disabled_changes == []
        assert after_changes == []
        responses = (
            client.get(config.account_path),
            client.get(config.users_path),
            client.post(config.disable_user_path, data={{"user_id": unsafe_id}}),
            client.post(config.enable_user_path, data={{"user_id": unsafe_id}}),
        )
        for response in responses:
            content_type = response.headers.get("content-type", "")
            assert response.status_code == 200 and content_type.startswith("text/html")
            assert "application/json" not in content_type
        paths = {{route.path for route in ui.router.routes}}
        assert {{
            config.account_path, config.users_path,
            config.disable_user_path, config.enable_user_path,
        }} <= paths
        assert all(
            "{{user_id}}" not in path and unsafe_id not in path for path in paths
        )

        html = "\\n".join(response.text for response in responses)
        assert 'name="user_id"' in html
        assert "quote" in html and "&lt;tag&gt;&amp;tail" in html
        assert (
            '<input type="hidden" name="csrf" value="&lt;token&amp;value&gt;">'
            in html
        )
        assert '<input name="owned" value="unsafe">' not in html
        assert "csrf_html" in html and "&lt;input" in html
        values = [match.group(1) for match in attr_pattern.finditer(html)]
        assert values != []
        for bad in (unsafe_id, "unsafe/id", "<tag>", "&tail"):
            assert all(bad not in value for value in values)
        key = adapter.row_key_from_user_id(unsafe_id)
        assert key == adapter.row_key_from_user_id(unsafe_id) and key != unsafe_id
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key) is not None
        assert counts["current"] >= 4 and counts["admin"] >= 3
        assert counts["listed"] >= 1 and counts["csrf"] >= 1
        assert counts["passkeys"] >= 1
        assert disabled_changes == [(unsafe_id, True), (unsafe_id, False)]
        assert after_changes == [unsafe_id, unsafe_id]
        """,
    )
    # When: account, admin list, disable, and enable routes are exercised.
    completed = run_fresh_python(script)
    # Then: pages/fragments are HTML and unsafe ids stay out of routes/selectors.
    assert_subprocess_passed(completed)
