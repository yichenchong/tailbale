import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
import app.models
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.discovery import router as discovery_router
from app.routers.events import router as events_router
from app.routers.profiles import router as profiles_router
from app.routers.services import router as services_router
from app.routers.settings import router as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure data directories and database tables exist
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)

    # Ensure edge image is built (runs in thread to avoid blocking startup)
    from app.edge.image_builder import ensure_edge_image
    try:
        await asyncio.to_thread(ensure_edge_image)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Could not build edge image at startup — will retry on first service reconcile",
            exc_info=True,
        )

    # Start background tasks
    from app.certs.renewal_task import cert_renewal_loop
    from app.reconciler.reconcile_loop import reconcile_loop

    renewal_task = asyncio.create_task(cert_renewal_loop())
    reconcile_task = asyncio.create_task(reconcile_loop())
    yield
    # Shutdown: cancel background tasks
    for task in (renewal_task, reconcile_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="tailBale",
    description="UnRAID + Tailscale + Cloudflare edge orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(discovery_router)
app.include_router(services_router)
app.include_router(events_router)
app.include_router(dashboard_router)
app.include_router(profiles_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# --- Static file serving for production (frontend SPA) ---
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the React SPA for any non-API route."""
        file_path = (_static_dir / full_path).resolve()
        if full_path and file_path.is_relative_to(_static_dir.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_static_dir / "index.html")
