# ruff: noqa: E402
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from app.logging_config import configure_logging

configure_logging()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import app.models
from app.config import settings
from app.database import Base, engine
from app.routers.auth import router as auth_router
from app.routers.connection_tests import router as connection_tests_router
from app.routers.dashboard import router as dashboard_router
from app.routers.developer import router as developer_router
from app.routers.discovery import router as discovery_router
from app.routers.events import router as events_router
from app.routers.jobs import router as jobs_router
from app.routers.profiles import router as profiles_router
from app.routers.service_actions import router as service_actions_router
from app.routers.services import router as services_router
from app.routers.settings import router as settings_router
from app.services.errors import ServiceError
from app.version import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure data directories and database tables exist
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal, run_migrations
    run_migrations()

    # Recover jobs left "running" by a previous crash so they can be retried
    # or dismissed again (the retry/dismiss endpoints reject "running").
    from app.routers.jobs import reset_stale_running_jobs
    with SessionLocal() as startup_db:
        reset_stale_running_jobs(startup_db)

    # Build the edge image in the background so it never blocks startup: ASGI
    # holds off accepting connections (including /api/health) until lifespan
    # startup returns, and a fresh build can take minutes. The reconcile and
    # recreate paths also call ensure_edge_image lazily, so this is best-effort.
    async def _build_edge_image_bg() -> None:
        from app.edge.image_builder import ensure_edge_image
        try:
            await asyncio.to_thread(ensure_edge_image)
        except Exception:
            logging.getLogger(__name__).warning(
                "Could not build edge image at startup — will retry on first service reconcile",
                exc_info=True,
            )

    # Start background tasks
    from app.certs.renewal_task import cert_renewal_loop
    from app.events.retention_task import retention_loop
    from app.reconciler.reconcile_loop import health_check_loop, reconcile_loop

    image_task = asyncio.create_task(_build_edge_image_bg())
    renewal_task = asyncio.create_task(cert_renewal_loop())
    reconcile_task = asyncio.create_task(reconcile_loop())
    health_task = asyncio.create_task(health_check_loop())
    retention_task = asyncio.create_task(retention_loop())
    yield
    # Shutdown: cancel every background task, then await them all so none leak.
    # Cancel ALL first, then gather with return_exceptions so a task that already
    # exited with an error (or is mid-cancellation) can't short-circuit awaiting
    # the rest — every task is cancelled and reaped regardless of its state.
    background_tasks = (image_task, renewal_task, reconcile_task, health_task, retention_task)
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)


app = FastAPI(
    title="tailBale",
    description="Tailscale + Cloudflare edge orchestrator for Docker hosts",
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(ServiceError)
async def _service_error_handler(request, exc: ServiceError) -> JSONResponse:
    """Map every service-layer domain exception to its canonical HTTP response.

    The service layer (:mod:`app.services`) raises transport-agnostic
    :class:`~app.services.errors.ServiceError` subclasses instead of FastAPI
    ``HTTPException`` (AR7). This single handler translates them to the EXACT same
    status code + ``{"detail": ...}`` body the routers used to raise inline —
    404 'Service not found', 409 hostname-in-use / disabled, 422 hostname-suffix,
    400 missing Tailscale key, 502 hostname-change DNS failure / upstream-API
    failure (Cloudflare, Docker log proxy), 503 Docker-unavailable — so the
    observable HTTP behavior is unchanged. Each exception carries its own
    ``status_code`` + ``detail``, so no per-type branching is needed here.
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

def _cors_middleware_options(cors_origins: str) -> dict[str, object] | None:
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    if not origins:
        return None
    if "*" in origins:
        origins = ["*"]
        allow_credentials = False
    else:
        allow_credentials = True
    return {
        "allow_origins": origins,
        "allow_credentials": allow_credentials,
        "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": ["Authorization", "Content-Type"],
    }


_cors_options = _cors_middleware_options(settings.cors_origins)
if _cors_options is not None:
    app.add_middleware(CORSMiddleware, **_cors_options)


app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(developer_router)
app.include_router(connection_tests_router)
app.include_router(discovery_router)
app.include_router(services_router)
app.include_router(service_actions_router)
app.include_router(events_router)
app.include_router(dashboard_router)
app.include_router(profiles_router)
app.include_router(jobs_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version():
    return {"version": __version__}


# --- Static file serving for production (frontend SPA) ---
_static_dir = Path(__file__).resolve().parent.parent / "static"


def _spa_response(static_dir: Path, full_path: str) -> FileResponse:
    """Resolve a non-API request to a static file or the SPA index shell.

    Raises ``HTTPException(404)`` for the API namespace: an unmatched ``/api/*``
    path must 404, never fall through to the SPA shell (which would hand API
    clients a 200 + HTML body that fails to parse as JSON). The resolved-path
    containment check rejects ``..`` traversal out of ``static_dir``.
    """
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    file_path = (static_dir / full_path).resolve()
    if full_path and file_path.is_relative_to(static_dir.resolve()) and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(static_dir / "index.html")


if _static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve the React SPA for any non-API route."""
        return _spa_response(_static_dir, full_path)
