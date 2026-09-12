"""One FastAPI call installs passkey login and packaged user management."""

from __future__ import annotations

import subprocess
import sys
from textwrap import dedent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _run(code: str, *, cwd: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_install_local_identity_exposes_login_account_and_admin(
    tmp_path: Path,
) -> None:
    _run(
        dedent(
            """
            from pathlib import Path

            from fastapi import FastAPI, Request
            from fastapi.testclient import TestClient

            from my_usermanager.adapters.fastapi import write_current_user
            from my_usermanager.adapters.local_identity import install_local_identity
            from my_usermanager.models import Scope, User
            from my_usermanager.sessions import SessionPrincipal

            app = FastAPI()
            identity = install_local_identity(
                app,
                database=Path("auth.db"),
                rp_id="localhost",
                origin="http://localhost",
                session_secret="test-secret",
                app_name="Plugin demo",
            )

            client = TestClient(app)
            login = client.get("/login")
            assert login.status_code == 200
            assert "passkey" in login.text.lower() or "login" in login.text.lower()

            account = client.get("/account", follow_redirects=False)
            assert account.status_code in {302, 303, 401}
            assert account.status_code != 500

            _ = identity.manager.users.create(User("admin", "admin"))
            _ = identity.manager.grants.add_role_grant(
                "admin", "admin", Scope.global_()
            )

            def seed_admin_session(request: Request) -> dict[str, bool]:
                _ = write_current_user(
                    request,
                    SessionPrincipal(
                        user_id="admin",
                        username="admin",
                        roles=frozenset({"admin"}),
                        claims={"is_admin": True},
                    ),
                )
                return {"ok": True}

            _ = app.add_api_route("/__login", seed_admin_session, methods=["POST"])
            assert client.post("/__login").json() == {"ok": True}
            admin = client.get("/admin/users")
            assert admin.status_code == 200
            assert "admin" in admin.text
            """
        ),
        cwd=tmp_path,
    )


def test_install_local_identity_uses_one_sqlite_database(tmp_path: Path) -> None:
    _run(
        dedent(
            """
            import sqlite3
            from pathlib import Path
            from typing import cast

            from fastapi import FastAPI

            from my_usermanager.adapters.local_identity import install_local_identity

            conn = sqlite3.connect(Path("auth.db"), check_same_thread=False)
            app = FastAPI()
            identity = install_local_identity(
                app,
                database=conn,
                rp_id="127.0.0.1",
                origin="http://127.0.0.1:8000",
                session_secret="test-secret",
            )
            rows = cast(
                "list[tuple[str]]",
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(),
            )
            tables = {row[0] for row in rows}
            assert "um_users" in tables
            assert "passkey_credentials" in tables or "passkey_users" in tables
            assert identity.manager is not None
            """
        ),
        cwd=tmp_path,
    )
