"""Build the tailbale-edge Docker image from the bundled build context."""

from __future__ import annotations

import logging
from pathlib import Path

import docker

logger = logging.getLogger(__name__)

EDGE_IMAGE = "tailbale-edge:latest"
# Relative to the app working directory inside the orchestrator container
_EDGE_CONTEXT = Path("/app/edge-image")


def edge_image_exists(socket_path: str | None = None) -> bool:
    """Check if the edge image is already available locally."""
    client = _get_client(socket_path)
    try:
        client.images.get(EDGE_IMAGE)
        return True
    except docker.errors.ImageNotFound:
        return False


def build_edge_image(socket_path: str | None = None) -> str:
    """Build the edge image from the bundled context. Returns the image ID."""
    if not _EDGE_CONTEXT.is_dir():
        raise RuntimeError(
            f"Edge image build context not found at {_EDGE_CONTEXT}. "
            "Ensure the orchestrator image was built with the edge/ directory."
        )

    client = _get_client(socket_path)
    logger.info("Building edge image %s from %s ...", EDGE_IMAGE, _EDGE_CONTEXT)
    image, build_logs = client.images.build(
        path=str(_EDGE_CONTEXT),
        tag=EDGE_IMAGE,
        rm=True,
    )
    for chunk in build_logs:
        if "stream" in chunk:
            line = chunk["stream"].strip()
            if line:
                logger.debug("[edge-build] %s", line)

    logger.info("Edge image built: %s (id=%s)", EDGE_IMAGE, image.id)
    return image.id


def ensure_edge_image(socket_path: str | None = None) -> None:
    """Build the edge image if it doesn't exist yet."""
    if edge_image_exists(socket_path):
        logger.debug("Edge image %s already exists", EDGE_IMAGE)
        return
    build_edge_image(socket_path)


def _get_client(socket_path: str | None = None) -> docker.DockerClient:
    if socket_path:
        return docker.DockerClient(base_url=socket_path)
    return docker.DockerClient.from_env()
