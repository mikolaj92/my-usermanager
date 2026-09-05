from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT: Final = REPO_ROOT / "examples" / "fastapi_htmx"
APP_PATH: Final = EXAMPLE_ROOT / "app.py"
README_PATH: Final = EXAMPLE_ROOT / "README.md"
APP_MODULE: Final = "examples.fastapi_htmx.app"
DEMO_USER_ID: Final = "demo-user"

FORBIDDEN_CORE_IMPORTS: Final = (
    "fastapi",
    "jinja2",
    "pydantic",
    "my_auth",
    "my_usermanager.adapters.fastapi_htmx",
)
OPTIONAL_EXAMPLE_DEPENDENCIES: Final = ("fastapi", "jinja2", "httpx", "my_auth")
FORBIDDEN_APP_SOURCE_SNIPPETS: Final = (
    "request.session",
    "set_cookie(",
    "grant_permission(",
    "grant_role(",
    "ADMIN_ROLE_NAME",
    "Request.form(",
    "python-multipart",
    "React",
    "shadcn",
    "Tailwind",
    "npm",
    "bundler",
    "SPA",
)


def run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    source_paths = (
        REPO_ROOT / "src",
        Path("/Users/mini-m4-main/Developer/my-auth/src"),
        Path("/Users/mini-m4-main/Developer/app-factory"),
    )
    env["PYTHONPATH"] = (
        ":".join(str(path) for path in source_paths) + ":" + env.get("PYTHONPATH", "")
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
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
    assert path.is_file(), f"missing required file: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


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


def test_root_and_adapters_imports_keep_optional_ui_dependencies_out() -> None:
    # Given: fresh processes import the framework-neutral package boundaries only.
    import_blocks = (("import my_usermanager",), ("import my_usermanager.adapters",))

    # When / Then: optional UI deps, my-auth, and fastapi_htmx stay unloaded.
    for import_block in import_blocks:
        script = "\n".join(
            (
                "import sys",
                *import_block,
                f"forbidden = {FORBIDDEN_CORE_IMPORTS!r}",
                "loaded = [name for name in forbidden if name in sys.modules]",
                "assert loaded == [], loaded",
            ),
        )
        assert_subprocess_passed(run_fresh_python(script))


def test_example_source_consumes_adapters_instead_of_duplicate_templates() -> None:
    # Given: the composed example host source.
    app_source = read_required_file(APP_PATH)

    assert "install_identity_adapters" in app_source
    assert "PasskeyBinding" in app_source
    assert "UserManagerBinding" in app_source
    assert "install_passkey_ui" not in app_source
    assert "install_usermanager_ui" not in app_source
    assert "install_app_factory_ui" not in app_source
    assert "Jinja2Templates" not in app_source
    assert "StaticFiles(directory=" not in app_source
    assert tuple((EXAMPLE_ROOT / "templates").glob("**/*.html")) == ()
    assert not (EXAMPLE_ROOT / "static" / "passkey.js").exists()


def test_readme_uses_uv_only_and_names_host_owned_security_boundaries() -> None:
    # Given: the example README documents the composed demo.
    readme = read_required_file(README_PATH)
    root_readme = read_required_file(REPO_ROOT / "README.md")

    # When / Then: commands are uv-only and production security is host-owned.
    assert "uv run --no-sync" in readme
    published_my_auth = '--with "my-auth[fastapi-htmx] @ git+https://github.com/mikolaj92/my-auth.git@v0.5.4"'
    assert published_my_auth in readme
    assert published_my_auth in root_readme
    forbidden_commands = ("pip ", "python -m pip", "npm ", "pnpm ", "yarn ")
    for snippet in forbidden_commands:
        assert snippet not in readme
    forbidden_machine_paths = (
        "/Users/mini-m4-1/Developer/my-auth",
        "/Users/mini-m4-main/Developer/hermes-repos/my-auth",
        "/Users/mini-m4-main/Developer/my-auth",
    )
    for snippet in forbidden_machine_paths:
        assert snippet not in readme
        assert snippet not in root_readme
    forbidden_unexported = (
        "create_usermanager_ui_router",
        "usermanager_ui_static_files",
    )
    for snippet in forbidden_unexported:
        assert snippet not in readme
    required_boundaries = (
        "host application owns sessions",
        "no-op demo CSRF",
        "in-memory users",
        "does not provide production sessions",
        "does not provide production CSRF validation",
        "does not provide persistence",
        "does not provide audit logging",
        "does not provide production role/grant policy",
    )
    for snippet in required_boundaries:
        assert snippet in readme


def test_policy_scan_for_changed_example_files() -> None:
    # Given: only executable example Python is in this phase's implementation scope.
    source = read_required_file(APP_PATH)

    # When / Then: the example has no production policy/session/SPA implementation.
    for forbidden in FORBIDDEN_APP_SOURCE_SNIPPETS:
        assert forbidden not in source


def test_passkey_login_and_register_pages_are_my_auth_adapter_html() -> None:
    # Given / When / Then: my-auth owns passkey login/register UI pages.
    assert_route_contract(
        """
        login = client.get("/auth/login")
        register = client.get("/auth/register")
        for response in (login, register):
            content_type = response.headers.get("content-type", "")
            assert response.status_code == 200, response.text
            assert content_type.startswith("text/html"), content_type
            assert "application/json" not in content_type

        assert 'data-passkey-form="login"' in login.text
        assert 'data-passkey-form="register"' in register.text
        assert "/auth/ui/static/passkey-ui.js" in login.text + register.text
        assert "X-Demo-CSRF" in login.text + register.text
        assert "demo-noop-csrf" in login.text + register.text
        assert "data-passkey-demo" not in login.text + register.text
        """,
    )


def test_account_page_omits_optional_passkey_panel() -> None:
    # Given / When / Then: returning None leaves the optional section absent.
    assert_route_contract(
        """
        response = client.get("/account")
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 200, response.text
        assert content_type.startswith("text/html"), content_type
        assert "Passkey integration" not in response.text
        assert "Provide render_passkey_panel" not in response.text
        """,
    )


def test_admin_users_page_uses_dom_safe_keys_and_noop_csrf_inputs() -> None:
    # Given / When / Then: unsafe raw ids are kept out of DOM ids and HTMX attrs.
    assert_route_contract(
        r"""
        import re

        unsafe_id = app_module.DEMO_UNSAFE_USER_ID
        response = client.get("/admin/users")
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 200, response.text
        assert content_type.startswith("text/html"), content_type
        assert 'id="users-table"' in response.text
        assert 'name="_demo_csrf" value="demo-noop-csrf"' in response.text
        assert "Unsafe User" in response.text
        attr_pattern = re.compile(
            r'''(?:id|hx-target|hx-post|action)\s*=\s*["']([^"']+)["']'''
        )
        values = [match.group(1) for match in attr_pattern.finditer(response.text)]
        assert values != []
        for bad in (unsafe_id, "unsafe/id", "<tag>", "&tail"):
            assert all(bad not in value for value in values)
        assert re.search(r'id="user-row-[A-Za-z][A-Za-z0-9_-]*"', response.text)
        """
    )


def test_disable_enable_fragments_mutate_only_in_memory_demo_users() -> None:
    # Given / When / Then: HTMX mutations call host callbacks and swap one row.
    # Disable a non-admin so the final active administrator invariant stays intact.
    assert_route_contract(
        """
        target = "auditor-user"
        disable = client.post(
            "/admin/users/disable",
            data={"user_id": target, "csrf": "demo-noop-csrf"},
        )
        enable = client.post(
            "/admin/users/enable",
            data={"user_id": target, "csrf": "demo-noop-csrf"},
        )
        table = client.get("/admin/users")

        for response in (disable, enable, table):
            content_type = response.headers.get("content-type", "")
            assert response.status_code == 200, response.text
            assert content_type.startswith("text/html"), content_type
            assert "application/json" not in content_type

        assert 'id="user-row-' in disable.text
        assert 'id="users-table"' not in disable.text
        assert "Disabled" in disable.text
        assert "Active" in enable.text
        assert "Active" in table.text
        """,
    )


def test_admin_users_page_shows_invitation_status_and_lifecycle_actions() -> None:
    assert_route_contract(
        """
        page = client.get("/admin/users")
        assert page.status_code == 200, page.text
        assert "Pending Invitee" in page.text
        assert "Pending" in page.text
        assert "Invitation pending" in page.text
        assert "Reissue invitation" in page.text
        assert "Revoke invitation" in page.text
        assert "capability=" not in page.text

        reissue = client.post(
            "/admin/users/invitations/reissue",
            data={"invitation_id": "invite-pending-user", "csrf": "demo-noop-csrf"},
            follow_redirects=True,
        )
        assert reissue.status_code == 200, reissue.text
        assert "/activate?capability=pending-user-token-reissued" in reissue.text
        assert "Copy this activation link now" in reissue.text

        revoke = client.post(
            "/admin/users/invitations/revoke",
            data={"invitation_id": "invite-pending-user", "csrf": "demo-noop-csrf"},
        )
        assert revoke.status_code == 200, revoke.text
        assert 'id="user-row-' in revoke.text
        assert "Invitation revoked" in revoke.text
        assert "capability=" not in revoke.text
        """,
    )


def test_demo_rejects_disabling_final_active_administrator() -> None:
    assert_route_contract(
        f"""
        before = client.get("/admin/users").text
        response = client.post(
            "/admin/users/disable",
            data={{"user_id": {DEMO_USER_ID!r}, "csrf": "demo-noop-csrf"}},
        )
        after = client.get("/admin/users").text
        assert response.status_code == 409, response.text
        assert "last active admin" in response.text
        assert before == after
        """,
    )


def test_bad_csrf_token_is_rejected_before_demo_mutation() -> None:
    assert_route_contract(
        f"""
        before = client.get("/admin/users").text
        response = client.post(
            "/admin/users/disable",
            data={{"user_id": {DEMO_USER_ID!r}, "csrf": "wrong-token"}},
        )
        after = client.get("/admin/users").text
        assert response.status_code == 403, response.text
        assert "CSRF validation failed" in response.text
        assert before == after
        """,
    )


def test_malformed_disable_form_returns_html_error_without_mutation() -> None:
    # Given / When / Then: malformed form input is rejected as an HTML fragment.
    assert_route_contract(
        """
        response = client.post(
            "/admin/users/disable",
            content=b"user_id=%FF",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        content_type = response.headers.get("content-type", "")

        assert response.status_code == 400, response.text
        assert content_type.startswith("text/html"), content_type
        assert "Malformed form body" in response.text
        """,
    )


def test_api_auth_endpoints_remain_json_and_adapter_challenge_cookie_only() -> None:
    # Given / When / Then: /api/auth remains JSON with passkey challenge cookies.
    assert_route_contract(
        """
        options = client.post("/api/auth/login/options")
        missing_cookie = client.post(
            "/api/auth/login/verify",
            json={"id": "credential"},
        )

        assert options.status_code == 200, options.text
        assert options.headers["content-type"].startswith("application/json")
        assert options.json()["challenge"] == "demo-login-challenge"
        set_cookies = options.headers.get_list("set-cookie")
        assert set_cookies != []
        assert all(
            cookie.startswith("passkey_authentication_challenge=")
            for cookie in set_cookies
        )

        assert missing_cookie.status_code == 400, missing_cookie.text
        assert missing_cookie.headers["content-type"].startswith("application/json")
        assert missing_cookie.json()["detail"] == "missing passkey challenge"
        """,
    )
