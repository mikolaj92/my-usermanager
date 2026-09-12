# pyright: reportUnannotatedClassAttribute=false, reportUnusedCallResult=false, reportUnusedFunction=false
# ruff: noqa: EM101, S106, TRY003
"""Durable OIDC flow storage is host SQLite, not RAM or a readable cookie."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from textwrap import dedent
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.middleware.sessions import SessionMiddleware

from my_usermanager.adapters.fastapi import current_user
from my_usermanager.adapters.oidc import OidcRelyingParty
from my_usermanager.adapters.oidc_fastapi import (
    build_oidc_callback_router,
    build_oidc_login_router,
)
from my_usermanager.adapters.sqlite import create_tables
from my_usermanager.adapters.sqlite_oidc_flows import (
    SQLiteOidcFlowStore,
    create_oidc_flow_tables,
)
from my_usermanager.adapters.sqlite_schema import inspect_sqlite_schema
from my_usermanager.models import ExternalIdentity, User
from my_usermanager.sessions import SessionPrincipal
from my_usermanager.subjects import oidc_external_identity

if TYPE_CHECKING:
    from collections.abc import Generator


class _IdentityStore:
    def __init__(self, user: User) -> None:
        self._user = user

    def resolve_external_identity(self, identity: ExternalIdentity) -> User | None:
        if identity in self._user.external_identities:
            return self._user
        return None

    def link_external_identity(
        self,
        *,
        user_id: str,
        identity: ExternalIdentity,
    ) -> User:
        del user_id, identity
        raise AssertionError("OIDC completion must not auto-link identities")


def _rsa_key() -> RSAKey:
    return RSAKey.generate_key(
        2048,
        parameters={"use": "sig", "alg": "RS256"},
        private=True,
        auto_kid=True,
    )


def _id_token(key: RSAKey, *, nonce: str) -> str:
    now = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    return jwt.encode(
        {"alg": "RS256", "kid": key.kid},
        {
            "iss": "https://auth.example.test",
            "aud": "app",
            "sub": "user-1",
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
            "auth_time": now,
        },
        key,
    )


def _run(code: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.fixture
def connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(":memory:")
    _ = conn.execute("PRAGMA foreign_keys = ON")
    create_oidc_flow_tables(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_sqlite_flow_survives_a_new_store_instance(
    connection: sqlite3.Connection,
) -> None:
    first = SQLiteOidcFlowStore(connection, ttl_seconds=30)
    started = first.start(now=1_000.0, binding="browser-1")

    second = SQLiteOidcFlowStore(connection, ttl_seconds=30)
    consumed = second.consume(started.state, now=1_010.0, binding="browser-1")

    assert consumed.nonce == started.nonce
    assert consumed.verifier == started.verifier
    with pytest.raises(PermissionError, match="authentication unavailable"):
        second.consume(started.state, now=1_011.0, binding="browser-1")


def test_sqlite_flow_rejects_wrong_or_missing_browser_binding(
    connection: sqlite3.Connection,
) -> None:
    store = SQLiteOidcFlowStore(connection, ttl_seconds=30)
    started = store.start(now=1_000.0, binding="browser-1")

    with pytest.raises(PermissionError, match="authentication unavailable"):
        store.consume(started.state, now=1_010.0, binding="browser-2")
    with pytest.raises(PermissionError, match="authentication unavailable"):
        store.consume(started.state, now=1_010.0, binding="")
    consumed = store.consume(started.state, now=1_010.0, binding="browser-1")
    assert consumed.state == started.state


def test_sqlite_flow_expires_and_does_not_store_raw_binding(
    connection: sqlite3.Connection,
) -> None:
    store = SQLiteOidcFlowStore(connection, ttl_seconds=30)
    started = store.start(now=1_000.0, binding="browser-secret")

    dumped = str(connection.execute("SELECT * FROM um_oidc_flows").fetchall())
    assert "browser-secret" not in dumped
    with pytest.raises(PermissionError, match="authentication unavailable"):
        store.consume(started.state, now=1_031.0, binding="browser-secret")


def test_oidc_flow_tables_keep_um_schema_inspect_current() -> None:
    connection = sqlite3.connect(":memory:")
    create_tables(connection)
    create_oidc_flow_tables(connection)
    assert inspect_sqlite_schema(connection) == "current"
    connection.close()


def test_oidc_flow_ddl_import_stays_free_of_oidc_runtime() -> None:
    _run(
        dedent(
            """
            import sys
            from my_usermanager.adapters.sqlite_oidc_flows import (
                create_oidc_flow_tables,
            )
            assert 'joserfc' not in sys.modules
            assert 'my_auth' not in sys.modules
            assert callable(create_oidc_flow_tables)
            """
        )
    )


def test_auth_database_initialize_creates_oidc_flow_table() -> None:
    _run(
        dedent(
            """
            import sqlite3
            from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
            from my_usermanager.adapters.sqlite_schema import inspect_sqlite_schema
            connection = sqlite3.connect(':memory:')
            SQLiteAuthDatabase(connection).initialize()
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert 'um_oidc_flows' in names
            assert inspect_sqlite_schema(connection) == 'current'
            connection.close()
            """
        )
    )


def test_oidc_http_flow_binds_cookie_and_rejects_replay_and_csrf() -> None:
    issuer = "https://auth.example.test"
    identity = oidc_external_identity(issuer=issuer, subject="user-1")
    user = User(
        user_id="local-1",
        username="alice",
        external_identities=frozenset({identity}),
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    _ = conn.execute("PRAGMA foreign_keys = ON")
    create_oidc_flow_tables(conn)
    flows = SQLiteOidcFlowStore(conn, ttl_seconds=300)
    key = _rsa_key()
    now = float(int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()))
    token = None

    def redeem_code(code: str, *, verifier: str) -> str:
        del code
        assert token is not None
        assert verifier
        return token

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    relying_party = OidcRelyingParty(
        issuer=issuer,
        audience="app",
        key=key,
        store=_IdentityStore(user),
        project=lambda item: SessionPrincipal(
            user_id=item.user_id,
            username=item.username,
        ),
    )
    app.include_router(
        build_oidc_login_router(
            relying_party=relying_party,
            flows=flows,
            authorization_endpoint=f"{issuer}/oauth/authorize",
            redirect_uri="https://app.example.test/oidc/callback",
            now=lambda: now,
        )
    )
    app.include_router(
        build_oidc_callback_router(
            relying_party=relying_party,
            flows=flows,
            redeem_code=redeem_code,
            now=lambda: now + 10,
        )
    )

    @app.get("/me")
    def me(request: Request) -> dict[str, str | None]:
        principal = current_user(request)
        return {"user_id": None if principal is None else principal.user_id}

    client = TestClient(app, base_url="https://app.example.test")
    started = client.get("/oidc/login", follow_redirects=False)
    assert started.status_code == 302
    cookie = started.cookies.get("oidc_flow")
    assert cookie
    set_cookie = started.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert cookie not in started.headers["location"]
    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert cookie != query["state"][0]
    assert cookie != query["nonce"][0]
    token = _id_token(key, nonce=query["nonce"][0])

    stolen = TestClient(app, base_url="https://app.example.test")
    csrf = stolen.get(
        "/oidc/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert csrf.status_code == 401

    ok = client.get(
        "/oidc/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert ok.status_code == 302
    assert client.get("/me").json() == {"user_id": "local-1"}

    replay = client.get(
        "/oidc/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert replay.status_code == 401
    conn.close()
