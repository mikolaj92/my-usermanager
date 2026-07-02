from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def test_fastapi_session_dependencies_round_trip_signed_cookie_session() -> None:
    code = dedent(
        """
        import warnings
        from typing import Annotated

        from fastapi import Depends, FastAPI, Request
        from starlette.exceptions import StarletteDeprecationWarning

        warnings.filterwarnings(
            "ignore",
            category=StarletteDeprecationWarning,
            message="Using `httpx` with `starlette.testclient` is deprecated*",
        )

        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware

        from my_usermanager import SessionPrincipal
        from my_usermanager.adapters.fastapi import (
            clear_current_user,
            current_user,
            require_user,
            require_user_dependency,
            write_current_user,
        )

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")
        custom_require_user = require_user_dependency(key="custom_principal")

        @app.post("/login")
        def login(request: Request) -> dict[str, bool]:
            write_current_user(
                request,
                SessionPrincipal(user_id="user_123", username="alice"),
            )
            return {"ok": True}

        @app.get("/me")
        def me(
            user: Annotated[SessionPrincipal | None, Depends(current_user)],
        ) -> dict[str, str | None]:
            return {"user_id": None if user is None else user.user_id}

        @app.get("/required")
        def required(
            user: Annotated[SessionPrincipal, Depends(require_user)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.post("/custom-login")
        def custom_login(request: Request) -> dict[str, bool]:
            write_current_user(
                request,
                SessionPrincipal(user_id="custom_user"),
                key="custom_principal",
            )
            return {"ok": True}

        @app.get("/custom-required")
        def custom_required(
            user: Annotated[SessionPrincipal, Depends(custom_require_user)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.post("/logout")
        def logout(request: Request) -> dict[str, bool]:
            clear_current_user(request)
            return {"ok": True}

        client = TestClient(app)

        assert client.get("/me").json() == {"user_id": None}
        assert client.get("/required").status_code == 401

        assert client.post("/login").json() == {"ok": True}
        assert client.get("/me").json() == {"user_id": "user_123"}
        assert client.get("/required").json() == {"user_id": "user_123"}

        assert client.post("/custom-login").json() == {"ok": True}
        assert client.get("/custom-required").json() == {"user_id": "custom_user"}

        assert client.post("/logout").json() == {"ok": True}
        assert client.get("/me").json() == {"user_id": None}
        """,
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.stdout == ""
    assert completed.stderr == ""
