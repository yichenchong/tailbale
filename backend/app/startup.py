import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

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
    with database_module.session_scope() as startup_db:
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


@dataclass(frozen=True)
class BackgroundTaskSpec:
    """One lifespan-owned background task: a stable ``name`` plus a ``factory``
    that produces its coroutine when the task is started."""

    name: str
    factory: Callable[[], Coroutine[Any, Any, object]]


def _background_task_specs() -> tuple[BackgroundTaskSpec, ...]:
    """The full, ordered set of background tasks the app lifespan owns.

    The one-shot startup edge-image build comes first, then the four periodic
    loops. Each factory is invoked at task-start time so it reads the current
    (test-patchable) loop callable; the per-loop startup delays live inside those
    loop factories and are unchanged here.
    """
    return (
        BackgroundTaskSpec("edge-image-build", build_edge_image_bg),
        BackgroundTaskSpec("cert-renewal", lambda: renewal_task.cert_renewal_loop()),
        BackgroundTaskSpec("reconcile", lambda: reconcile_loop.reconcile_loop()),
        BackgroundTaskSpec("health-check", lambda: reconcile_loop.health_check_loop()),
        BackgroundTaskSpec("event-retention", lambda: retention_task.retention_loop()),
    )


def create_background_tasks() -> tuple[asyncio.Task[object], ...]:
    """Start every long-running background loop owned by app lifespan.

    Each task is NAMED (``asyncio.create_task(name=...)``) so it is identifiable
    in tracebacks and task dumps; the set, order, and one-shot-vs-periodic split
    are identical to the previous anonymous tuple.
    """
    return tuple(
        asyncio.create_task(spec.factory(), name=spec.name)
        for spec in _background_task_specs()
    )
