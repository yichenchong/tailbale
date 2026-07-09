"""CORS option and preflight behavior for main.py."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import _cors_middleware_options


class TestCorsOptions:
    def _preflight(self, options, origin: str = "https://evil.example"):
        app = FastAPI()
        app.add_middleware(CORSMiddleware, **options)

        @app.get("/api/probe")
        async def probe():
            return {"ok": True}

        with TestClient(app) as client:
            return client.options(
                "/api/probe",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )

    def test_empty_cors_origins_disables_middleware(self):
        assert _cors_middleware_options("") is None

    def test_explicit_cors_origins_allow_credentials(self):
        options = _cors_middleware_options(" https://ui.example , https://admin.example ")
        assert options is not None
        assert options["allow_origins"] == ["https://ui.example", "https://admin.example"]
        assert options["allow_credentials"] is True

        resp = self._preflight(options, origin="https://ui.example")
        assert resp.headers["access-control-allow-origin"] == "https://ui.example"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_wildcard_cors_origin_disables_credentials_even_when_mixed(self):
        options = _cors_middleware_options("*, https://ui.example")
        assert options is not None
        assert options["allow_origins"] == ["*"]
        assert options["allow_credentials"] is False

        resp = self._preflight(options)
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in resp.headers
