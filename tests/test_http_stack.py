"""Exercise the optional HTTPX2 TestClient stack in a fresh interpreter."""

import subprocess
import sys
from textwrap import dedent


def test_httpx2_transport_preserves_error_responses() -> None:
    """Dependency installation and TestClient selection must agree."""
    code = dedent("""
        import importlib.util
        assert importlib.util.find_spec("httpx2") is not None
        import httpx2
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        app = FastAPI()
        @app.get("/denied")
        def denied():
            raise HTTPException(status_code=403, detail="denied")

        with TestClient(app) as client:
            assert isinstance(client, httpx2.Client)
            response = client.get("/denied")
        assert response.status_code == 403
        assert response.json() == {"detail": "denied"}
    """)
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
