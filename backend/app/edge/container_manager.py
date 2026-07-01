"""Edge container lifecycle management."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.types import Mount

from app.edge.docker_client import close_client, connect, docker_client
from app.edge.image_builder import EDGE_IMAGE, ensure_edge_image
from app.version import __version__

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)


def container_service_id(container: docker.models.containers.Container) -> str | None:
    """Return the tailBale service id label from a Docker container."""
    labels = getattr(container, "labels", None)
    if not isinstance(labels, dict):
        return None
    return labels.get("tailbale.service_id")


def is_container_for_service(
    container: docker.models.containers.Container,
    service_id: str,
) -> bool:
    label = container_service_id(container)
    return label in (None, service_id)


def find_edge_container(
    client: docker.DockerClient,
    service_id: str,
    edge_container_name: str,
) -> docker.models.containers.Container | None:
    """Locate an edge container on an already-open *client*.

    Tries the named container first (ignoring any whose service-id label points
    at a different service), then falls back to a label search so Docker
    ID/name changes still resolve. This is the single lookup implementation
    shared by the edge lifecycle helpers and the health checker.
    """
    try:
        container = client.containers.get(edge_container_name)
        if is_container_for_service(container, service_id):
            return container
        logger.warning(
            "Ignoring container named %s because it belongs to service %s, not %s",
            edge_container_name,
            container_service_id(container),
            service_id,
        )
    except docker.errors.NotFound:
        pass

    # Fallback: search by label. This also handles Docker ID/name changes.
    containers = client.containers.list(
        all=True, filters={"label": f"tailbale.service_id={service_id}"}
    )
    return containers[0] if isinstance(containers, list) and containers else None


def _find_edge_container(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    client: docker.DockerClient | None = None,
) -> docker.models.containers.Container | None:
    """Find existing edge container by name or service_id label."""
    if client is not None:
        return find_edge_container(client, service_id, edge_container_name)
    with docker_client(socket_path) as client:
        return find_edge_container(client, service_id, edge_container_name)


def _find_edge_container_for_use(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
) -> tuple[docker.DockerClient | None, docker.models.containers.Container | None]:
    """Find an edge container and keep its Docker client open for follow-up calls."""
    client = connect(socket_path)
    try:
        return client, _find_edge_container(service_id, edge_container_name, socket_path, client)
    except Exception:
        close_client(client)
        raise


def create_edge_container(
    service: Service,
    ts_authkey: str,
    generated_dir: str | Path,
    certs_dir: str | Path,
    tailscale_state_dir: str | Path,
    socket_path: str | None = None,
    edge_image: str = EDGE_IMAGE,
) -> str:
    """Create an edge container for a service. Returns the container ID.

    The *_dir paths must be resolvable by the **Docker host**.  When tailBale
    runs inside a container that shares the host's Docker socket, these must
    be the host-side paths (see ``HOST_DATA_DIR``).  The caller (reconciler)
    is responsible for creating directories and writing files at the internal
    (container-local) equivalents before calling this function.
    """
    generated_dir = Path(generated_dir)
    certs_dir = Path(certs_dir)
    tailscale_state_dir = Path(tailscale_state_dir)

    ensure_edge_image(socket_path)
    with docker_client(socket_path) as client:
        # Prepare mount paths (these are host-side paths for Docker bind mounts).
        # The Caddyfile directory is mounted (not the file itself) to avoid
        # stale-inode issues when the file is atomically replaced on the host.
        caddy_config_dir = str(generated_dir / service.id)
        cert_dir = str(certs_dir / service.hostname)
        ts_state_dir = str(tailscale_state_dir / service.edge_container_name)

        mounts = [
            Mount(target="/etc/caddy", source=caddy_config_dir, type="bind", read_only=True),
            Mount(target="/certs", source=cert_dir, type="bind", read_only=True),
            Mount(target="/var/lib/tailscale", source=ts_state_dir, type="bind"),
        ]

        environment = {
            "TS_AUTHKEY": ts_authkey,
            "TS_HOSTNAME": service.ts_hostname,
        }

        labels = {
            "tailbale.managed": "true",
            "tailbale.service_id": service.id,
            "tailbale.version": __version__,
        }

        container = client.containers.create(
            image=edge_image,
            name=service.edge_container_name,
            mounts=mounts,
            environment=environment,
            labels=labels,
            network=service.network_name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
        )

        logger.info(
            "Created edge container %s (id=%s) for service %s",
            service.edge_container_name,
            container.id,
            service.id,
        )
        return container.id


def start_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Start an existing edge container."""
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
        if not container:
            raise RuntimeError(f"Edge container not found for service {service_id}")
        container.start()
        logger.info("Started edge container %s", edge_container_name)
    finally:
        if client is not None:
            close_client(client)


def stop_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Stop an edge container."""
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
        if not container:
            logger.info("Edge container not found for service %s, nothing to stop", service_id)
            return
        container.stop(timeout=10)
        logger.info("Stopped edge container %s", edge_container_name)
    finally:
        if client is not None:
            close_client(client)


def restart_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Restart an edge container."""
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
        if not container:
            raise RuntimeError(f"Edge container not found for service {service_id}")
        container.restart(timeout=10)
        logger.info("Restarted edge container %s", edge_container_name)
    finally:
        if client is not None:
            close_client(client)


def _get_tailscale_node_id(
    container: docker.models.containers.Container,
) -> str | None:
    """Extract the Tailscale node ID from inside a running edge container."""
    try:
        exit_code, output = container.exec_run(
            "tailscale status --json",
            environment={"TS_SOCKET": "/var/run/tailscale/tailscaled.sock"},
        )
        if exit_code != 0:
            return None
        status = json.loads(output.decode("utf-8", errors="replace"))
        return status.get("Self", {}).get("ID")
    except Exception:
        return None


def _delete_tailscale_device(node_id: str, api_key: str) -> bool:
    """Delete a device from the tailnet via the Tailscale API.

    Uses ``DELETE /api/v2/device/{nodeId}`` with Basic auth.
    Returns True on success (200/2xx), False on any failure.
    """
    import httpx2

    try:
        resp = httpx2.delete(
            f"https://api.tailscale.com/api/v2/device/{node_id}",
            auth=(api_key, ""),
            timeout=10.0,
        )
        if resp.is_success:
            logger.info("Deleted Tailscale device %s via API", node_id)
            return True
        logger.warning(
            "Tailscale API device deletion returned %d for node %s: %s",
            resp.status_code, node_id, resp.text[:200],
        )
        return False
    except Exception:
        logger.info("Tailscale API device deletion failed for node %s", node_id, exc_info=True)
        return False


def remove_edge(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    *,
    delete_device: bool = True,
    raise_on_error: bool = False,
) -> None:
    """Force-remove an edge container.

    The container is force-removed (``remove(force=True)``) — there is no
    graceful stop first.

    When ``delete_device`` is true and a running container exposes a Tailscale
    node ID, a best-effort call to the Tailscale API deletes the device from the
    tailnet so it doesn't linger as an offline machine in the admin console.
    Device deletion happens only when a Tailscale API key is configured;
    otherwise it is skipped. Pass ``delete_device=False`` to preserve the node's
    tailnet identity/IP across a container swap (the TS state dir is kept).

    When ``raise_on_error`` is true a docker ``APIError`` from the removal is
    re-raised, so callers like ``recreate_edge`` fail cleanly instead of hitting
    a later opaque 409 name conflict; by default it is logged and swallowed
    (best-effort).
    """
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
        if not container:
            logger.info("Edge container not found for service %s, nothing to remove", service_id)
            return

        # Best-effort: remove the Tailscale device via API before destroying the
        # container. Skipped when delete_device is False to preserve the node's
        # tailnet identity/IP across a container swap.
        if delete_device and container.status == "running":
            node_id = _get_tailscale_node_id(container)
            if node_id:
                from app.secrets import TAILSCALE_API_KEY, read_secret

                api_key = read_secret(TAILSCALE_API_KEY)
                if api_key:
                    _delete_tailscale_device(node_id, api_key)
                else:
                    logger.info(
                        "Tailscale API key not configured — skipping device removal for %s",
                        edge_container_name,
                    )
            else:
                logger.info("Could not get Tailscale node ID for %s", edge_container_name)

        try:
            container.remove(force=True)
            logger.info("Removed edge container %s", edge_container_name)
        except docker.errors.NotFound:
            logger.info("Edge container %s already removed", edge_container_name)
            return
        except docker.errors.APIError:
            logger.warning("Failed to remove edge container %s", edge_container_name, exc_info=True)
            if raise_on_error:
                raise
            return
    finally:
        if client is not None:
            close_client(client)


def recreate_edge(
    service: Service,
    ts_authkey: str,
    generated_dir: str | Path,
    certs_dir: str | Path,
    tailscale_state_dir: str | Path,
    socket_path: str | None = None,
    edge_image: str = EDGE_IMAGE,
) -> str:
    """Remove existing edge and create + start a new one. Returns new container ID."""
    remove_edge(
        service.id, service.edge_container_name, socket_path,
        delete_device=False, raise_on_error=True,
    )
    container_id = create_edge_container(
        service, ts_authkey, generated_dir, certs_dir, tailscale_state_dir,
        socket_path, edge_image,
    )
    start_edge(service.id, service.edge_container_name, socket_path)
    return container_id


def get_edge_version(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
) -> str | None:
    """Read the tailbale.version label from an edge container.

    Returns the version string, or None if the container doesn't exist
    or has no version label.
    """
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        return None
    labels = getattr(container, "labels", None)
    if not isinstance(labels, dict):
        return None
    return labels.get("tailbale.version")


def get_edge_logs(
    service_id: str,
    edge_container_name: str,
    tail: int = 100,
    socket_path: str | None = None,
) -> str:
    """Fetch recent logs from an edge container."""
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
        if not container:
            return ""
        return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
    finally:
        if client is not None:
            close_client(client)


def _wait_for_running(
    container: docker.models.containers.Container,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
) -> bool:
    """Wait until a container reaches the 'running' state.

    Docker rejects ``exec`` calls when a container is restarting or paused.
    Returns True if the container is running, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container.reload()  # refresh attrs from daemon
        if container.status == "running":
            return True
        if container.status in ("exited", "dead", "removing"):
            logger.warning("Container %s is %s — not waiting further", container.name, container.status)
            return False
        time.sleep(poll_interval)
    logger.warning("Timed out waiting for container %s to be running (status=%s)", container.name, container.status)
    return False


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
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
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
        for attempt in range(max_retries):
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
                    time.sleep(retry_delay)
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
                time.sleep(retry_delay)
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
    finally:
        if client is not None:
            close_client(client)


def detect_tailscale_ip(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    max_retries: int = 10,
    retry_delay: float = 2.0,
) -> str | None:
    """Detect the Tailscale IPv4 address assigned to an edge container.

    Retries with backoff since Tailscale auth can take a few seconds.
    Returns the IP string or None if detection fails.
    """
    client, container = _find_edge_container_for_use(service_id, edge_container_name, socket_path)
    try:
        if not container:
            return None

        # Wait for the container to be running before attempting exec.
        if not _wait_for_running(container):
            logger.warning(
                "Container %s not running (status=%s), skipping IP detection",
                edge_container_name, container.status,
            )
            return None

        for attempt in range(max_retries):
            try:
                # Re-check state on each retry — container may have restarted.
                container.reload()
                if container.status != "running" and not _wait_for_running(container, timeout=10.0):
                    logger.info("Container %s left running state on attempt %d", edge_container_name, attempt + 1)
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    continue

                ts_env = {"TS_SOCKET": "/var/run/tailscale/tailscaled.sock"}

                # Try `tailscale ip -4` first
                exit_code, output = container.exec_run(
                    "tailscale ip -4", environment=ts_env,
                )
                if exit_code == 0:
                    ip = output.decode("utf-8", errors="replace").strip()
                    if ip and ip.startswith("100."):
                        logger.info("Detected Tailscale IP %s for %s", ip, edge_container_name)
                        return ip

                # Fallback: parse `tailscale status --json`
                exit_code, output = container.exec_run(
                    "tailscale status --json", environment=ts_env,
                )
                if exit_code == 0:
                    status = json.loads(output.decode("utf-8", errors="replace"))
                    ts_ips = status.get("Self", {}).get("TailscaleIPs", [])
                    for addr in ts_ips:
                        if addr.startswith("100."):
                            logger.info("Detected Tailscale IP %s for %s (via status)", addr, edge_container_name)
                            return addr
            except Exception:
                logger.info("Attempt %d failed for %s", attempt + 1, edge_container_name, exc_info=True)

            if attempt < max_retries - 1:
                time.sleep(retry_delay)

        logger.warning("Failed to detect Tailscale IP for %s after %d attempts", edge_container_name, max_retries)
        return None
    finally:
        if client is not None:
            close_client(client)
