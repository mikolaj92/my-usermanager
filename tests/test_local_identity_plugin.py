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
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (completed.stdout, completed.stderr) == ("", "")


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
            identity = install_local_identity(app, database=Path("auth.db"),
                rp_id="localhost", origin="http://localhost",
                session_secret="test-secret", app_name="Plugin demo")

            client = TestClient(app)
            login = client.get("/login")
            assert login.status_code == 200
            assert "passkey" in login.text.lower() or "login" in login.text.lower()

            account = client.get("/account", follow_redirects=False)
            assert account.status_code in {302, 303, 401}
            assert account.status_code != 500

            _ = identity.manager.users.create(User("admin", "admin"))
            grants = identity.manager.grants
            _ = grants.add_role_grant("admin", "admin", Scope.global_())

            def seed_admin_session(request: Request) -> dict[str, bool]:
                _ = write_current_user(
                    request,
                    SessionPrincipal(user_id="admin", username="admin",
                        roles=frozenset({"admin"}), claims={"is_admin": True}),
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
            identity = install_local_identity(FastAPI(), database=conn,
                rp_id="127.0.0.1", origin="http://127.0.0.1:8000",
                session_secret="test-secret")
            sql = "SELECT name FROM sqlite_master WHERE type='table'"
            rows = cast("list[tuple[str]]", conn.execute(sql).fetchall())
            tables = {row[0] for row in rows}
            assert "um_users" in tables
            assert "passkey_credentials" in tables or "passkey_users" in tables
            assert identity.manager is not None

            import asyncio, threading
            from my_usermanager.adapters.my_auth_fastapi import (
                build_complete_registration,
            )

            async def registration_execution_replay():
                for fail in (False, True):
                    started, release = threading.Event(), threading.Event()
                    observed = []
                    async def finish():
                        observed.append("awaited")
                        if fail:
                            raise ValueError("completion failed")
                        return None
                    def complete(*args):
                        observed.append(threading.get_ident()); started.set()
                        assert release.wait(5)
                        return finish()
                    hook = build_complete_registration(complete)
                    task = asyncio.create_task(hook(None, None))
                    try:
                        assert await asyncio.to_thread(started.wait, 5)
                        assert len(observed) == 1
                        assert observed[0] != threading.get_ident()
                        task.cancel()
                        await asyncio.sleep(0)
                        task.cancel()
                        await asyncio.sleep(0)
                        assert not task.done()
                        release.set()
                        try:
                            await task
                        except ValueError as exc:
                            assert fail
                            assert isinstance(exc.__cause__, asyncio.CancelledError)
                        except asyncio.CancelledError:
                            assert not fail
                        else:
                            raise AssertionError("cancellation lost")
                        assert observed[1:] == ["awaited"]
                    finally:
                        release.set()
            asyncio.run(registration_execution_replay())
            """
        ),
        cwd=tmp_path,
    )


def test_install_local_identity_hides_passkeys_after_unlink(
    tmp_path: Path,
) -> None:
    _run(
        dedent(
            """
            import sqlite3
            from dataclasses import replace
            from pathlib import Path

            from fastapi import FastAPI, Request
            from fastapi.testclient import TestClient

            from my_usermanager.adapters.fastapi import write_current_user
            from my_usermanager.adapters.local_identity import install_local_identity
            from my_usermanager.adapters.my_auth import MY_AUTH_PROVIDER
            from my_usermanager.models import ExternalIdentity, Scope, User
            from my_usermanager.sessions import SessionPrincipal

            conn = sqlite3.connect(Path("auth.db"), check_same_thread=False)
            app = FastAPI()
            identity = install_local_identity(app, database=conn,
                rp_id="localhost", origin="http://localhost",
                session_secret="test-secret")
            issuer = "https://idp.example.test"
            user = identity.manager.users.create(
                User("alice", "alice", display_name="Alice",
                    external_identities=frozenset({
                        ExternalIdentity(MY_AUTH_PROVIDER, "alice"),
                        ExternalIdentity(issuer, "person-1")}),
                )
            )
            grants_store = identity.manager.grants
            _ = grants_store.add_role_grant("alice", "admin", Scope.global_())
            _ = conn.execute("INSERT INTO passkey_users"
                "(user_id, user_handle, name, display_name) VALUES (?, ?, ?, ?)",
                ("alice", "YWxpY2U", "alice", "Alice"))
            conn.commit()

            def seed_session(request: Request) -> dict[str, bool]:
                _ = write_current_user(
                    request,
                    SessionPrincipal(user_id="alice", username="alice",
                        display_name="Alice", roles=frozenset({"admin"}),
                        claims={"is_admin": True},
                        external_identities=user.external_identities),
                )
                return {"ok": True}

            _ = app.add_api_route("/__login", seed_session, methods=["POST"])
            client = TestClient(app)
            assert client.post("/__login").json() == {"ok": True}

            linked = client.get("/account")
            assert linked.status_code == 200
            assert "Local passkeys" in linked.text
            assert "/account/passkeys" in linked.text
            assert 'action="/logout"' in linked.text
            passkeys = client.get("/account/passkeys")
            assert passkeys.status_code == 200

            remaining = frozenset({ExternalIdentity(issuer, "person-1")})
            unlinked = identity.manager.users.update(
                replace(user, external_identities=remaining))
            assert unlinked.external_identities == remaining
            still = identity.manager.users.get("alice")
            assert still is not None
            assert still.user_id == "alice"
            grants = identity.manager.grants.list_grants_for_user("alice")
            assert any(grant.role_name == "admin" for grant in grants)

            account = client.get("/account")
            assert account.status_code == 200
            assert "Local passkeys" not in account.text
            assert 'href="/account/passkeys"' not in account.text
            assert "Credentials" not in account.text
            assert 'action="/logout"' in account.text
            detached = client.get("/account/passkeys", follow_redirects=False)
            assert detached.status_code in {401, 403, 404, 302, 303}
            assert detached.status_code != 200
            """
        ),
        cwd=tmp_path,
    )
