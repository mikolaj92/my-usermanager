from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT: Final = REPO_ROOT / "examples" / "fastapi_htmx"
APP_PATH: Final = EXAMPLE_ROOT / "app.py"
APP_MODULE: Final = "examples.fastapi_htmx.app"
TEMPLATE_ROOT: Final = EXAMPLE_ROOT / "templates"
BASE_TEMPLATE: Final = TEMPLATE_ROOT / "base.html"
DEMO_USER_ID: Final = "demo-user"

FORBIDDEN_CORE_IMPORT_PREFIXES: Final = (
    "examples.fastapi_htmx",
    "fastapi",
    "jinja2",
    "my_auth",
    "my_usermanager.adapters.my_auth",
    "my_usermanager.adapters.my_auth_fastapi",
    "pydantic",
    "starlette.templating",
)
FRAMEWORK_IMPORT_PREFIXES: Final = ("fastapi", "jinja2", "starlette.templating")
OPTIONAL_EXAMPLE_DEPENDENCIES: Final = ("fastapi", "jinja2", "httpx2")
REQUIRED_BASE_TEMPLATE_SNIPPETS: Final = (
    "https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/basecoat.cdn.min.css",
    "https://unpkg.com/htmx.org@2.0.4",
    "https://cdn.jsdelivr.net/npm/basecoat-css@0.3.11/dist/js/all.min.js",
    "/auth/static/passkey.js",
)
FORBIDDEN_ADAPTER_POLICY_SNIPPETS: Final = (
    "ADMIN_ROLE_NAME",
    "grant_permission(",
    "grant_role(",
    "request.session",
    "set_cookie(",
)
ADAPTER_BOUNDARY_FILES: Final = (
    REPO_ROOT / "src" / "my_usermanager" / "adapters" / "my_auth.py",
    REPO_ROOT / "src" / "my_usermanager" / "adapters" / "my_auth_fastapi.py",
)
HX_INDICATOR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""hx-indicator\s*=\s*["'](?P<selector>[^"']+)["']""",
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


def require_optional_example_dependencies() -> None:
    for dependency in OPTIONAL_EXAMPLE_DEPENDENCIES:
        if importlib.util.find_spec(dependency) is None:
            pytest.skip(
                f"FastAPI HTMX example tests require optional dependency: {dependency}",
            )


def read_required_file(path: Path) -> str:
    assert path.is_file(), f"Phase 2 must create {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def read_template_group(name: str) -> str:
    template_dir = TEMPLATE_ROOT / name
    assert template_dir.is_dir(), (
        f"Phase 2 must create {template_dir.relative_to(REPO_ROOT)}"
    )
    template_paths = tuple(sorted(template_dir.glob("*.html")))
    assert template_paths != (), (
        f"Phase 2 must add HTML templates under {template_dir.relative_to(REPO_ROOT)}"
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in template_paths)


def assert_loader_contract(markup: str, group_name: str) -> None:
    indicator_matches = tuple(HX_INDICATOR_PATTERN.finditer(markup))
    assert indicator_matches != (), f"{group_name} actions must use hx-indicator"
    assert "htmx-indicator" in markup, (
        f"{group_name} indicators must include the .htmx-indicator class"
    )
    for indicator_match in indicator_matches:
        selector = indicator_match.group("selector")
        if selector.startswith("#"):
            target_id = selector.removeprefix("#")
            assert f'id="{target_id}"' in markup or f"id='{target_id}'" in markup, (
                f"{group_name} hx-indicator target {selector} must exist in markup"
            )


def assert_route_contract(script_body: str) -> None:
    require_optional_example_dependencies()
    script = dedent(
        f"""
        from importlib import import_module

        app_module = import_module({APP_MODULE!r})
        testclient_module = import_module("fastapi.testclient")
        client = testclient_module.TestClient(app_module.app)

        {script_body}
        """,
    )
    assert_subprocess_passed(run_fresh_python(script))


def test_core_import_keeps_optional_ui_dependencies_out() -> None:
    # Given: a fresh interpreter that imports only the framework-neutral package.
    import_check = dedent(
        f"""
        import sys

        import my_usermanager

        forbidden_prefixes = {FORBIDDEN_CORE_IMPORT_PREFIXES!r}
        loaded = [
            prefix
            for prefix in forbidden_prefixes
            if any(
                module_name == prefix or module_name.startswith(f"{{prefix}}.")
                for module_name in sys.modules
            )
        ]

        assert my_usermanager.__version__ == "0.1.0"
        assert loaded == [], loaded
        """,
    )

    # When: the import runs in isolation.
    completed = run_fresh_python(import_check)

    # Then: FastAPI/Jinja/Pydantic/my-auth/example UI modules are not loaded.
    assert_subprocess_passed(completed)


def test_example_app_import_is_the_framework_boundary() -> None:
    require_optional_example_dependencies()

    # Given: core import is clean before the optional example is imported.
    import_check = dedent(
        f"""
        import sys
        from importlib import import_module

        import my_usermanager

        framework_prefixes = {FRAMEWORK_IMPORT_PREFIXES!r}
        before = [
            prefix
            for prefix in framework_prefixes
            if any(
                module_name == prefix or module_name.startswith(f"{{prefix}}.")
                for module_name in sys.modules
            )
        ]
        assert my_usermanager.__version__ == "0.1.0"
        assert before == [], before

        app_module = import_module({APP_MODULE!r})

        after = [
            prefix
            for prefix in framework_prefixes
            if any(
                module_name == prefix or module_name.startswith(f"{{prefix}}.")
                for module_name in sys.modules
            )
        ]
        missing = [prefix for prefix in framework_prefixes if prefix not in after]
        assert missing == [], missing
        assert hasattr(app_module, "app")
        """,
    )

    # When: the example app import is the explicit FastAPI/Jinja boundary.
    completed = run_fresh_python(import_check)

    # Then: that import owns the framework side effects and exposes `app`.
    assert_subprocess_passed(completed)


def test_base_template_uses_no_build_cdn_assets() -> None:
    # Given: the planned shared Jinja layout.
    base_template = read_required_file(BASE_TEMPLATE)

    # When / Then: Basecoat, HTMX, Basecoat JS, and passkey JS are CDN/static only.
    for required_snippet in REQUIRED_BASE_TEMPLATE_SNIPPETS:
        assert required_snippet in base_template


def test_auth_templates_have_htmx_targets_and_loaders() -> None:
    # Given: planned auth page and panel templates.
    auth_markup = read_template_group("auth")

    # When / Then: auth actions target stable fragments and show HTMX loaders.
    assert "#auth-panel" in auth_markup
    assert "#register-panel" in auth_markup
    assert_loader_contract(auth_markup, "auth")


def test_user_templates_have_row_targets_and_loaders() -> None:
    # Given: planned admin user page and row templates.
    user_markup = read_template_group("users")

    # When / Then: user actions target stable row fragments and show HTMX loaders.
    assert "#users-table" in user_markup
    assert "#user-row-" in user_markup or "user-row-{{" in user_markup
    assert_loader_contract(user_markup, "user")


def test_given_login_action_when_posted_then_auth_panel_fragment_is_html() -> None:
    # Given / When / Then: login returns the HTML fragment HTMX swaps into #auth-panel.
    assert_route_contract(
        """
        response = client.post("/auth/login", data={"username": "alice"})
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 200, response.text
        assert content_type.startswith("text/html"), content_type
        assert "application/json" not in content_type
        assert 'id="auth-panel"' in response.text or "id='auth-panel'" in response.text
        """,
    )


def test_register_action_returns_register_panel_html() -> None:
    # Given / When / Then: register returns the #register-panel HTML fragment.
    assert_route_contract(
        """
        response = client.post(
            "/auth/register",
            data={"display_name": "Alice Example", "username": "alice"},
        )
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 200, response.text
        assert content_type.startswith("text/html"), content_type
        assert "application/json" not in content_type
        has_double_id = 'id="register-panel"' in response.text
        has_single_id = "id='register-panel'" in response.text
        assert has_double_id or has_single_id
        """,
    )


def test_disable_user_action_returns_single_user_row_html() -> None:
    # Given / When / Then: disabling a demo user returns only that user's row fragment.
    assert_route_contract(
        f"""
        response = client.post("/admin/users/{DEMO_USER_ID}/disable")
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 200, response.text
        assert content_type.startswith("text/html"), content_type
        assert "application/json" not in content_type
        has_double_id = 'id="user-row-{DEMO_USER_ID}"' in response.text
        has_single_id = "id='user-row-{DEMO_USER_ID}'" in response.text
        has_users_table = (
            'id="users-table"' in response.text
            or "id='users-table'" in response.text
        )
        assert has_double_id or has_single_id
        assert not has_users_table
        """,
    )


def test_enable_user_action_returns_single_user_row_html() -> None:
    # Given / When / Then: enabling a demo user returns only that user's row fragment.
    assert_route_contract(
        f"""
        response = client.post("/admin/users/{DEMO_USER_ID}/enable")
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 200, response.text
        assert content_type.startswith("text/html"), content_type
        assert "application/json" not in content_type
        has_double_id = 'id="user-row-{DEMO_USER_ID}"' in response.text
        has_single_id = "id='user-row-{DEMO_USER_ID}'" in response.text
        has_users_table = (
            'id="users-table"' in response.text
            or "id='users-table'" in response.text
        )
        assert has_double_id or has_single_id
        assert not has_users_table
        """,
    )


def test_host_policy_boundaries_stay_in_demo_helpers() -> None:
    # Given: the example host app source and framework-neutral adapter sources.
    app_source = read_required_file(APP_PATH)

    # When / Then: registration and admin routes pass through explicit host helpers.
    assert "def registration_allowed" in app_source
    assert app_source.count("registration_allowed") >= 2
    assert "def require_admin" in app_source
    assert app_source.count("require_admin") >= 2

    # And: optional adapter/core layers do not create implicit admin grants or sessions.
    for adapter_path in ADAPTER_BOUNDARY_FILES:
        adapter_source = read_required_file(adapter_path)
        for forbidden_snippet in FORBIDDEN_ADAPTER_POLICY_SNIPPETS:
            assert forbidden_snippet not in adapter_source
