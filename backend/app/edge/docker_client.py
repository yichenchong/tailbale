"""Single source of truth for Docker client construction and socket resolution.

Every Docker client in the app is built here and the Docker-socket resolution
policy lives here too, so the whole app talks to exactly one daemon-selection
rule:

* ``resolve_socket`` -> the configured ``docker_socket_path``, or ``None`` when
  it is unset/blank so the caller falls back to ``docker.from_env()`` (which
  honors ``DOCKER_HOST``).
* ``connect`` / ``docker_client`` build a client from that resolved value.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import docker

from app.settings_store import get_setting

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def resolve_socket(db: Session) -> str | None:
    """Resolve the Docker socket path from DB settings.

    Returns the configured path, or ``None`` when it is unset/blank so callers
    fall back to ``docker.from_env()`` (honoring ``DOCKER_HOST``). This is the
    single socket-resolution policy for the whole app.
    """
    return get_setting(db, "docker_socket_path").strip() or None


def connect(socket_path: str | None = None) -> docker.DockerClient:
    """Construct a Docker client.

    With *socket_path* set, connect to it directly; otherwise use
    ``from_env()`` so ``DOCKER_HOST`` is honored.
    """
    if socket_path:
        return docker.DockerClient(base_url=socket_path)
    return docker.DockerClient.from_env()


def close_client(client: docker.DockerClient | None) -> None:
    """Best-effort close of a Docker client; never raises."""
    if client is None:
        return
    close = getattr(client, "close", None)
    if close is not None:
        try:
            close()
        except Exception:
            logger.debug("Failed to close Docker client", exc_info=True)


@contextmanager
def docker_client(socket_path: str | None = None) -> Iterator[docker.DockerClient]:
    """Yield a Docker client, guaranteeing it is closed afterwards."""
    client = connect(socket_path)
    try:
        yield client
    finally:
        close_client(client)
