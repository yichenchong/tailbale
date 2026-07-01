"""Tests for app wiring in main.py: the SPA catch-all and the CORS option builder.

The SPA catch-all is registered only when a built ``static/`` dir exists, so its
decision logic lives in the pure helper ``_spa_response`` which we exercise
directly. The load-bearing invariant: an unmatched ``/api/*`` path must 404, never
fall through to the HTML index shell (which would hand API clients a 200 + HTML
body that fails to parse as JSON), and ``..`` must never escape the static root.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import _cors_middleware_options, _spa_response


@pytest.fixture()
def static_dir(tmp_path):
    sd = tmp_path / "static"
    (sd / "assets").mkdir(parents=True)
    (sd / "index.html").write_text("<!doctype html><title>app</title>")
    (sd / "favicon.ico").write_text("icon-bytes")
    (sd / "assets" / "app.js").write_text("console.log(1)")
    # A sensitive file OUTSIDE the static root, used by the traversal test.
    (tmp_path / "secret.txt").write_text("top secret")
    return sd


def _served(resp) -> Path:
    return Path(resp.path)


class TestSpaResponse:
    @pytest.mark.parametrize("api_path", ["api", "api/", "api/unknown", "api/services/x"])
    def test_api_namespace_404s_instead_of_serving_shell(self, static_dir, api_path):
        with pytest.raises(HTTPException) as exc:
            _spa_response(static_dir, api_path)
        assert exc.value.status_code == 404

    def test_non_api_path_named_apiclient_is_served_as_spa(self, static_dir):
        # Only the exact "api" segment / "api/" prefix is special — a path that
        # merely starts with "api" (no slash) is a normal SPA route.
        assert _served(_spa_response(static_dir, "apidocs")) == static_dir / "index.html"

    def test_serves_real_top_level_file(self, static_dir):
        assert _served(_spa_response(static_dir, "favicon.ico")) == (static_dir / "favicon.ico").resolve()

    def test_serves_real_nested_asset(self, static_dir):
        assert _served(_spa_response(static_dir, "assets/app.js")) == (static_dir / "assets" / "app.js").resolve()

    def test_unknown_client_route_falls_back_to_index(self, static_dir):
        assert _served(_spa_response(static_dir, "dashboard/services")) == static_dir / "index.html"

    def test_root_path_serves_index(self, static_dir):
        assert _served(_spa_response(static_dir, "")) == static_dir / "index.html"

    def test_directory_request_serves_index_not_a_listing(self, static_dir):
        # "assets" exists but is a directory, not a file -> index fallback.
        assert _served(_spa_response(static_dir, "assets")) == static_dir / "index.html"

    def test_dotdot_traversal_cannot_escape_static_root(self, static_dir):
        # The sibling secret.txt exists and is a file, but resolves outside the
        # static root, so it must NOT be served — index shell instead.
        assert _served(_spa_response(static_dir, "../secret.txt")) == static_dir / "index.html"


class TestCorsMiddlewareOptions:
    def test_blank_disables_cors(self):
        assert _cors_middleware_options("") is None

    def test_whitespace_only_disables_cors(self):
        assert _cors_middleware_options("   ,  ,") is None

    def test_lone_wildcard_disables_credentials(self):
        opts = _cors_middleware_options("*")
        assert opts is not None
        assert opts["allow_origins"] == ["*"]
        assert opts["allow_credentials"] is False

    def test_explicit_origins_enable_credentials(self):
        opts = _cors_middleware_options("https://ui.example")
        assert opts is not None
        assert opts["allow_origins"] == ["https://ui.example"]
        assert opts["allow_credentials"] is True


class TestLifespanBackgroundTasks:
    """The lifespan must launch every background loop: cert renewal, the slow
    full reconcile, the fast health sweep, and event retention."""

    def test_starts_reconcile_health_renewal_and_retention_loops(
        self, db_engine, tmp_data_dir, monkeypatch
    ):
        from sqlalchemy.orm import sessionmaker

        import app.certs.renewal_task as renewal_mod
        import app.database as database_module
        import app.edge.image_builder as image_builder_mod
        import app.events.retention_task as retention_mod
        import app.main as main_module
        import app.reconciler.reconcile_loop as loop_mod
        import app.routers.jobs as jobs_mod

        # Point the lifespan's table-creation + startup session at the test engine
        # and neutralize the heavy/external startup steps.
        monkeypatch.setattr(main_module, "engine", db_engine)
        monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=db_engine))
        monkeypatch.setattr(database_module, "run_migrations", lambda *a, **k: None)
        monkeypatch.setattr(jobs_mod, "reset_stale_running_jobs", lambda *a, **k: None)
        monkeypatch.setattr(image_builder_mod, "ensure_edge_image", lambda *a, **k: None)

        started: list[str] = []

        def _make(name):
            def factory():
                started.append(name)

                async def _idle():
                    await asyncio.Event().wait()

                return _idle()

            return factory

        monkeypatch.setattr(renewal_mod, "cert_renewal_loop", _make("renewal"))
        monkeypatch.setattr(retention_mod, "retention_loop", _make("retention"))
        monkeypatch.setattr(loop_mod, "reconcile_loop", _make("reconcile"))
        monkeypatch.setattr(loop_mod, "health_check_loop", _make("health"))

        async def _drive():
            async with main_module.lifespan(main_module.app):
                pass

        asyncio.run(_drive())

        assert sorted(started) == ["health", "reconcile", "renewal", "retention"]

    def test_shutdown_cancels_all_tasks_even_if_one_loop_already_failed(
        self, db_engine, tmp_data_dir, monkeypatch
    ):
        """Shutdown must cancel/await EVERY background task, even when one loop has
        already exited with an exception. Pre-fix the sequential ``await task``
        re-raised that exception, short-circuiting the loop so the remaining tasks
        were never cancelled (a leak) and the shutdown itself raised."""
        from sqlalchemy.orm import sessionmaker

        import app.certs.renewal_task as renewal_mod
        import app.database as database_module
        import app.edge.image_builder as image_builder_mod
        import app.events.retention_task as retention_mod
        import app.main as main_module
        import app.reconciler.reconcile_loop as loop_mod
        import app.routers.jobs as jobs_mod

        monkeypatch.setattr(main_module, "engine", db_engine)
        monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=db_engine))
        monkeypatch.setattr(database_module, "run_migrations", lambda *a, **k: None)
        monkeypatch.setattr(jobs_mod, "reset_stale_running_jobs", lambda *a, **k: None)
        monkeypatch.setattr(image_builder_mod, "ensure_edge_image", lambda *a, **k: None)

        cancelled: list[str] = []

        def _make_idle(name):
            def factory():
                async def _idle():
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.append(name)
                        raise

                return _idle()

            return factory

        def _make_failing():
            def factory():
                async def _boom():
                    raise RuntimeError("loop crashed")

                return _boom()

            return factory

        # cert_renewal_loop exits with an exception almost immediately; the rest idle.
        monkeypatch.setattr(renewal_mod, "cert_renewal_loop", _make_failing())
        monkeypatch.setattr(retention_mod, "retention_loop", _make_idle("retention"))
        monkeypatch.setattr(loop_mod, "reconcile_loop", _make_idle("reconcile"))
        monkeypatch.setattr(loop_mod, "health_check_loop", _make_idle("health"))

        async def _drive():
            async with main_module.lifespan(main_module.app):
                # Let the failing loop run to its exception before shutdown begins.
                await asyncio.sleep(0.05)

        # Must NOT raise: shutdown has to survive a task that already errored...
        asyncio.run(_drive())
        # ...and every still-running loop must have been cancelled (no leak).
        assert sorted(cancelled) == ["health", "reconcile", "retention"]
