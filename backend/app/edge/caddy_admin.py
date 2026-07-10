"""Caddy admin-API operations for edge containers.

Split out of ``container_manager`` (AR-R3-15): the ``caddy reload`` exec and its
retry/backoff logic are a distinct concern from container lifecycle. The shared
client-lifecycle primitive :func:`~app.edge.container_session.edge_container` and
the container-state helper
:func:`~app.edge.container_session._wait_for_running` are imported from the
``container_session`` leaf (which owns the container primitives and does not
import this module — the dependency stays one-way and acyclic).
"""

from __future__ import annotations

import logging

import docker

from app.backoff import retry_sync
from app.edge.container_session import _wait_for_running, edge_container

logger = logging.getLogger(__name__)


def _is_retryable_exec_conflict(exc: Exception) -> bool:
    """Return True when Docker rejects exec because the container is mid-restart."""
    if not isinstance(exc, docker.errors.APIError):
        return False

    message = str(exc).lower()
    if "wait until the container is running" in message:
        return True
    return "is restarting" in message or "is paused" in message


def reload_caddy(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    max_retries: int = 5,
    retry_delay: float = 2.0,
) -> str:
    """Execute ``caddy reload`` inside the edge container. Returns exec output.

    Retries several times because Caddy's admin API (:2019) may not be
    ready immediately after the container starts.
    """
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            raise RuntimeError(f"Edge container not found for service {service_id}")

        if not _wait_for_running(container):
            raise RuntimeError(
                f"Edge container {edge_container_name} is not running "
                f"(status={container.status}), cannot reload Caddy"
            )

        last_result = ""
        last_error: docker.errors.APIError | None = None
        exit_code: int | None = None
        for attempt in retry_sync(max_retries, retry_delay):
            try:
                exit_code, output = container.exec_run(
                    "caddy reload --config /etc/caddy/Caddyfile --force"
                )
            except docker.errors.APIError as exc:
                if not _is_retryable_exec_conflict(exc):
                    raise

                last_error = exc
                if attempt < max_retries - 1:
                    logger.info(
                        "Container %s rejected Caddy reload while restarting, retrying (%d/%d)...",
                        edge_container_name, attempt + 1, max_retries,
                    )
                    _wait_for_running(container, timeout=10.0, poll_interval=0.5)
                    continue
                break

            last_result = output.decode("utf-8", errors="replace")
            # A real exec result supersedes any earlier transient restart
            # conflict, so the final error reflects the actual reload failure
            # (e.g. a bad config) instead of the stale "never stabilized" path.
            last_error = None
            if exit_code == 0:
                logger.info("Reloaded Caddy in edge container %s", edge_container_name)
                return last_result

            # "connection refused" means the admin API isn't up yet — retry
            if "connection refused" in last_result and attempt < max_retries - 1:
                logger.info(
                    "Caddy admin API not ready in %s, retrying (%d/%d)...",
                    edge_container_name, attempt + 1, max_retries,
                )
                continue

            # Any other failure — don't retry
            break

        if last_error is not None:
            raise RuntimeError(
                f"Caddy reload never reached a stable running container for {edge_container_name}: {last_error}"
            ) from last_error

        if exit_code is None:
            raise RuntimeError("Caddy reload failed: no reload attempts were made")
        raise RuntimeError(f"Caddy reload failed (exit {exit_code}): {last_result}")
