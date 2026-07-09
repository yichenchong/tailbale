import asyncio
import logging

import app.database as database_module
from app import config as config_module
from app.certs import renewal_task
from app.edge import image_builder
from app.events import retention_task
from app.reconciler import reconcile_loop
from app.routers import jobs as jobs_router

logger = logging.getLogger(__name__)


def prepare_database() -> None:
    """Ensure runtime directories, tables, and additive migrations are ready."""
    config_module.settings.ensure_dirs()
    database_module.Base.metadata.create_all(bind=database_module.engine)
    database_module.run_migrations()


def recover_stale_running_jobs() -> None:
    """Recover jobs left running by a previous crash before API traffic starts."""
    with database_module.SessionLocal() as startup_db:
        jobs_router.reset_stale_running_jobs(startup_db)


async def build_edge_image_bg() -> None:
    """Best-effort startup edge-image build; reconcile paths retry lazily."""
    try:
        await asyncio.to_thread(image_builder.ensure_edge_image)
    except Exception:
        logger.warning(
            "Could not build edge image at startup — will retry on first service reconcile",
            exc_info=True,
        )


def create_background_tasks() -> tuple[asyncio.Task[object], ...]:
    """Start every long-running background loop owned by app lifespan."""
    return (
        asyncio.create_task(build_edge_image_bg()),
        asyncio.create_task(renewal_task.cert_renewal_loop()),
        asyncio.create_task(reconcile_loop.reconcile_loop()),
        asyncio.create_task(reconcile_loop.health_check_loop()),
        asyncio.create_task(retention_task.retention_loop()),
    )
