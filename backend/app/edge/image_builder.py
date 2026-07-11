"""Build the tailbale-edge Docker image from the bundled build context."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import docker

from app.edge.docker_client import docker_client
from app.version import __version__

logger = logging.getLogger(__name__)


EDGE_IMAGE = "tailbale-edge:latest"
# Absolute path inside the orchestrator container (WORKDIR /app) where the
# Dockerfile bundles the edge/ build context (COPY edge/ ./edge-image/).
_EDGE_CONTEXT = Path("/app/edge-image")
# Serializes ensure_edge_image so a startup build and a concurrent lazy build
# (reconcile/recreate) cannot both rebuild the same tag at once.
_BUILD_LOCK = threading.Lock()


def _remove_image_if_present(
    client: docker.DockerClient,
    image_id: str | None,
    *,
    reason: str,
) -> None:
    if not image_id:
        return
    try:
        client.images.remove(image=image_id, force=True)
        logger.info("Removed superseded edge image %s (%s)", image_id, reason)
    except docker.errors.ImageNotFound:
        return
    except docker.errors.APIError:
        logger.warning("Failed to remove superseded edge image %s", image_id, exc_info=True)


def _build_edge_image(socket_path: str | None = None) -> str:
    """Build the edge image from the bundled context. Returns the image ID."""
    if not _EDGE_CONTEXT.is_dir():
        raise RuntimeError(
            f"Edge image build context not found at {_EDGE_CONTEXT}. "
            "Ensure the orchestrator image was built with the edge/ directory."
        )

    with docker_client(socket_path) as client:
        logger.info("Building edge image %s from %s ...", EDGE_IMAGE, _EDGE_CONTEXT)
        image, build_logs = client.images.build(
            path=str(_EDGE_CONTEXT),
            tag=EDGE_IMAGE,
            rm=True,
            labels={"tailbale.version": __version__},
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].strip()
                if line:
                    logger.debug("[edge-build] %s", line)

        logger.info("Edge image built: %s (id=%s)", EDGE_IMAGE, image.id)
        return image.id


def ensure_edge_image(socket_path: str | None = None) -> None:
    """Build the edge image if it doesn't exist or is outdated.

    Checks the ``tailbale.version`` label on the existing image.  If it
    doesn't match the current orchestrator version, the image is rebuilt
    so that containers created from it carry the correct code.
    """
    # Serialize so a startup build and a concurrent lazy build (reconcile or
    # recreate, which also call ensure_edge_image) cannot both rebuild at once.
    # The loser of the lock re-checks the version label and returns early.
    with _BUILD_LOCK, docker_client(socket_path) as client:
        previous_image_id: str | None = None
        try:
            image = client.images.get(EDGE_IMAGE)
            previous_image_id = image.id
            image_version = (image.labels or {}).get("tailbale.version")
            if image_version == __version__:
                logger.info("Edge image %s already at version %s", EDGE_IMAGE, __version__)
                return
            logger.info(
                "Edge image version mismatch (image=%s, orchestrator=%s), rebuilding...",
                image_version, __version__,
            )
        except docker.errors.ImageNotFound:
            logger.info("Edge image %s not found, building...", EDGE_IMAGE)

        new_image_id = _build_edge_image(socket_path)
        if previous_image_id and previous_image_id != new_image_id:
            _remove_image_if_present(
                client,
                previous_image_id,
                reason=f"replaced by {new_image_id}",
            )
