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


def test_fastapi_authorization_dependencies_support_policies_and_redirects() -> None:
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

        from my_usermanager import (
            GrantClaimsProjector,
            MemoryGrantStore,
            MemoryRoleStore,
            Permission,
            Scope,
            SessionPrincipal,
        )
        from my_usermanager.adapters.fastapi import (
            AuthorizationResponses,
            require_claim,
            require_owner_or_admin,
            require_permission,
            require_role,
            require_scoped_permission,
            write_current_user,
        )

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")

        roles = MemoryRoleStore()
        grants = MemoryGrantStore()
        projector = GrantClaimsProjector(roles=roles, grants=grants)
        _ = grants.add_permission_grant(
            "scoped_user",
            Permission("workflows.run"),
            Scope.scoped("workflow", "wf_1"),
        )

        admin_dep = require_role("admin")
        read_dep = require_permission("users.read")
        app_access_dep = require_claim(
            "has_app_access",
            responses=AuthorizationResponses.redirects(),
        )
        owner_dep = require_owner_or_admin(
            lambda request: request.path_params["user_id"],
        )
        wf1_dep = require_scoped_permission(
            "workflows.run",
            Scope.scoped("workflow", "wf_1"),
            projector=projector,
        )
        wf2_dep = require_scoped_permission(
            "workflows.run",
            Scope.scoped("workflow", "wf_2"),
            projector=projector,
        )

        @app.post("/login/{kind}")
        def login(kind: str, request: Request) -> dict[str, bool]:
            if kind == "admin":
                principal = SessionPrincipal(
                    user_id="admin_user",
                    roles=frozenset({"admin"}),
                    claims={"is_admin": True, "has_app_access": True},
                )
            elif kind == "reader":
                principal = SessionPrincipal(
                    user_id="reader",
                    permissions=frozenset({Permission("users.read")}),
                    claims={"has_app_access": False},
                )
            elif kind == "scoped":
                principal = SessionPrincipal(user_id="scoped_user")
            elif kind == "owner":
                principal = SessionPrincipal(user_id="owner_123")
            else:
                principal = SessionPrincipal(
                    user_id="noaccess",
                    claims={"has_app_access": False},
                )
            write_current_user(request, principal)
            return {"ok": True}

        @app.get("/admin")
        def admin(
            user: Annotated[SessionPrincipal, Depends(admin_dep)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.get("/read")
        def read(
            user: Annotated[SessionPrincipal, Depends(read_dep)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.get("/app")
        def app_page(
            user: Annotated[SessionPrincipal, Depends(app_access_dep)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.get("/users/{user_id}")
        def user_page(
            user: Annotated[SessionPrincipal, Depends(owner_dep)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.get("/workflow/wf1")
        def workflow_one(
            user: Annotated[SessionPrincipal, Depends(wf1_dep)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        @app.get("/workflow/wf2")
        def workflow_two(
            user: Annotated[SessionPrincipal, Depends(wf2_dep)],
        ) -> dict[str, str]:
            return {"user_id": user.user_id}

        client = TestClient(app)

        assert client.get("/admin").status_code == 401
        login_redirect = client.get("/app", follow_redirects=False)
        assert login_redirect.status_code == 303
        assert login_redirect.headers["location"] == "/login"

        assert client.post("/login/reader").json() == {"ok": True}
        assert client.get("/read").json() == {"user_id": "reader"}
        assert client.get("/admin").status_code == 403
        request_access = client.get("/app", follow_redirects=False)
        assert request_access.status_code == 303
        assert request_access.headers["location"] == "/request-access"

        assert client.post("/login/owner").json() == {"ok": True}
        assert client.get("/users/owner_123").json() == {"user_id": "owner_123"}
        assert client.get("/users/other_user").status_code == 403

        assert client.post("/login/scoped").json() == {"ok": True}
        assert client.get("/workflow/wf1").json() == {"user_id": "scoped_user"}
        assert client.get("/workflow/wf2").status_code == 403

        assert client.post("/login/admin").json() == {"ok": True}
        assert client.get("/admin").json() == {"user_id": "admin_user"}
        assert client.get("/users/other_user").json() == {"user_id": "admin_user"}
        assert client.get("/app").json() == {"user_id": "admin_user"}
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
