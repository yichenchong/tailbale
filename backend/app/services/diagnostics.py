"""Intentful infrastructure operations for the router layer (AR15).

Routers used to reach into the ``docker_client`` / ``resolve_socket`` primitives
re-exported by :mod:`app.services` and inline the Docker/Cloudflare/Tailscale
round-trips themselves. This module hosts the *application-level* operations
those endpoints actually want — "test the Docker connection", "fetch the main
container logs", "trigger a manual reconcile" — so the routers express intent
and this layer owns the infrastructure work + error signaling.

Each op performs the same infra work with the same socket-resolution policy
(:func:`app.edge.docker_client.resolve_socket`) and the same success/failure
shapes the routers produced before, raising domain exceptions from
:mod:`app.services.errors` (mapped centrally in :mod:`app.main`) where the
routers previously raised inline. Behavior is unchanged.
"""

from __future__ import annotations

import logging
import os

import docker
import requests
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.adapters.cloudflare_adapter import CloudflareAPIError, verify_zone
from app.edge.docker_client import docker_client, resolve_socket
from app.reconciler import reconcile_loop
from app.schemas.services import (
    ContainerPortInfo,
    DiscoveredContainer,
    DiscoveryResponse,
)
from app.schemas.settings import ConnectionTestResult
from app.secrets import (
    TAILSCALE_AUTH_KEY,
    TS_AUTHKEY_PREFIX,
    cloudflare_credentials,
    is_valid_ts_auth_key,
    read_secret,
)
from app.services.errors import DockerUnavailable, UpstreamApiError

logger = logging.getLogger(__name__)


def test_docker(db: Session) -> ConnectionTestResult:
    """Probe the Docker daemon and report the connection result.

    Resolves the socket via the shared policy and returns a
    ``ConnectionTestResult`` — success carries the server version, any failure
    degrades to ``success=False`` with ``str(exc)`` (never a raised error), so a
    connection test stays diagnostic.
    """
    try:
        with docker_client(resolve_socket(db)) as client:
            client.ping()
            info = client.info()
            return ConnectionTestResult(
                success=True,
                message=f"Connected to Docker {info.get('ServerVersion', 'unknown')}",
            )
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))


def test_cloudflare(db: Session) -> ConnectionTestResult:
    """Verify Cloudflare credentials against the configured zone.

    Sync by design: the caller (a FastAPI threadpool endpoint) keeps the
    blocking adapter call off the event loop while preserving the ~10s cap and
    the exact ``ConnectionTestResult`` messages the settings API asserts.
    """
    token, zone_id = cloudflare_credentials(db)
    if not token:
        return ConnectionTestResult(success=False, message="Cloudflare token not configured")
    if not zone_id:
        return ConnectionTestResult(success=False, message="Cloudflare zone ID not configured")

    try:
        zone_name = verify_zone(token, zone_id, timeout=10)
        return ConnectionTestResult(success=True, message=f"Connected to zone: {zone_name}")
    except CloudflareAPIError as e:
        errors = e.errors or []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        msg = first.get("message") or "Unexpected Cloudflare API response"
        return ConnectionTestResult(success=False, message=msg)
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))


def test_tailscale() -> ConnectionTestResult:
    """Validate the configured Tailscale auth key's format.

    Basic format check only — full validation happens when creating an edge.
    """
    token = read_secret(TAILSCALE_AUTH_KEY)
    if not token:
        return ConnectionTestResult(success=False, message="Tailscale auth key not configured")

    if is_valid_ts_auth_key(token):
        return ConnectionTestResult(
            success=True,
            message="Auth key format looks valid (full test on edge creation)",
        )
    return ConnectionTestResult(
        success=False,
        message=f"Auth key should start with '{TS_AUTHKEY_PREFIX}'",
    )


def _find_main_container(client: docker.DockerClient):
    containers = client.containers.list(all=True, filters={"label": "tailbale.main=true"})
    if containers:
        return containers[0]

    fallback_names = (
        "tailbale",
        "backend",
        "tailbale-tailbale-1",
        "tailbale-backend-1",
        os.environ.get("HOSTNAME"),
    )
    for name in fallback_names:
        if not name:
            continue
        try:
            return client.containers.get(name)
        except docker.errors.NotFound:
            continue

    raise HTTPException(status_code=404, detail="tailBale container not found")


def get_main_logs(db: Session, tail: int) -> dict:
    """Return the tailBale (main) container's recent logs.

    Locates the labeled main container (falling back to known names), reads the
    last *tail* timestamped lines, and returns ``{"container", "logs"}``. A
    missing container surfaces as an ``HTTPException`` 404; a read failure is
    logged server-side and mapped to :class:`UpstreamApiError` (502) so the
    client-facing detail never leaks ``str(exc)``.
    """
    try:
        with docker_client(resolve_socket(db)) as client:
            container = _find_main_container(client)
            output = container.logs(stdout=True, stderr=True, tail=tail, timestamps=True)
            logs = (
                output.decode("utf-8", errors="replace")
                if isinstance(output, bytes)
                else str(output)
            )
            return {
                "container": getattr(container, "name", None) or getattr(container, "id", "unknown"),
                "logs": logs,
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Could not read tailBale container logs", exc_info=True)
        raise UpstreamApiError("Could not read tailBale logs") from exc


def trigger_manual_reconcile(db: Session, service_id: str) -> dict:
    """Reconcile a single service off the request, returning the phase result.

    Resolves the Docker socket via the shared policy and runs
    :func:`app.reconciler.reconcile_loop.spawn_reconcile` (which owns its own
    session). Intended to be driven from the endpoint's worker thread; the
    caller maps any failure to the canonical edge-action HTTP status.
    """
    socket = resolve_socket(db)
    return reconcile_loop.spawn_reconcile(service_id, socket)


# Labels marking an edge runtime container the orchestrator creates and that
# must never be offered as an upstream to expose: edge containers carry
# ``tailbale.managed=true``. The orchestrator's own (main) container carries
# ``tailbale.main=true`` (docker-compose.*.yml) but is deliberately NOT hidden,
# so it can be wrapped as a service and reached under a custom domain
# (admin-UI self-exposure).
MANAGED_LABELS = {"tailbale.managed": "true"}


def _parse_ports(container) -> list[ContainerPortInfo]:
    """Extract port mappings from a Docker container."""
    ports: list[ContainerPortInfo] = []
    port_data = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
    exposed_ports = container.attrs.get("Config", {}).get("ExposedPorts") or {}
    port_specs = dict.fromkeys(exposed_ports, None)
    port_specs.update(port_data)
    for container_port, bindings in port_specs.items():
        # container_port looks like "80/tcp"
        port_num, _, proto = container_port.partition("/")
        host_port = None
        for binding in bindings or ():
            candidate = binding.get("HostPort")
            if candidate:
                host_port = candidate
                break
        ports.append(ContainerPortInfo(
            container_port=port_num,
            host_port=host_port,
            protocol=proto or "tcp",
        ))
    return ports


def _parse_networks(container) -> list[str]:
    """Extract network names from a Docker container."""
    networks = container.attrs.get("NetworkSettings", {}).get("Networks") or {}
    return list(networks.keys())


def _is_managed(container) -> bool:
    """True if the container is an orchestrator-created edge container
    (``tailbale.managed``). Such containers must never appear as exposure
    candidates. The main container (``tailbale.main``) is deliberately excluded
    here so the admin UI can be self-exposed as a service under a custom domain."""
    labels = container.labels or {}
    return any(labels.get(key) == value for key, value in MANAGED_LABELS.items())


def list_discoverable_containers(
    db: Session,
    *,
    running_only: bool = True,
    hide_managed: bool = True,
    search: str = "",
) -> DiscoveryResponse:
    """List Docker containers available for exposure.

    Opens a Docker client via the shared socket-resolution policy, hides
    orchestrator-owned (managed/main) containers and search misses, and returns
    a :class:`DiscoveryResponse`. An unreachable daemon — a ``DockerException``
    at connect/list time, or a raw ``requests.exceptions.ConnectionError`` when
    the daemon dies mid-call — degrades to an empty result rather than an error.
    """
    search_lower = search.strip().lower()
    try:
        with docker_client(resolve_socket(db)) as client:
            containers = client.containers.list(all=not running_only)
            result: list[DiscoveredContainer] = []
            for c in containers:
                if hide_managed and _is_managed(c):
                    continue

                name = c.name or ""
                image = c.attrs.get("Config", {}).get("Image") or "unknown"

                if search_lower and search_lower not in name.lower() and search_lower not in image.lower():
                    continue

                result.append(DiscoveredContainer(
                    id=c.id,
                    name=name,
                    image=image,
                    status=c.status,
                    state=c.attrs.get("State", {}).get("Status", c.status),
                    ports=_parse_ports(c),
                    networks=_parse_networks(c),
                    labels=c.labels or {},
                ))
            return DiscoveryResponse(containers=result, total=len(result))
    except (docker.errors.DockerException, requests.exceptions.ConnectionError):
        return DiscoveryResponse(containers=[], total=0)


def validate_upstream_container_port(db: Session, container_id: str, port: int) -> None:
    """Validate that the upstream container exists and the port is plausible.

    Raises HTTPException on invalid container/port input and DockerUnavailable
    when Docker is unreachable. Called from the CRUD router BEFORE the lifecycle
    lock so a slow/unreachable Docker never stalls other lifecycle ops.
    """
    try:
        with docker_client(resolve_socket(db)) as client:
            upstream = client.containers.get(container_id)
            _validate_upstream_port(upstream, port)
    except docker.errors.NotFound as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Upstream container '{container_id}' not found",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Cannot connect to Docker to validate upstream container %s", container_id
        )
        raise DockerUnavailable(
            "Cannot connect to Docker to validate upstream container"
        ) from exc


def _validate_upstream_port(container, requested_port: int) -> None:
    """Check that *requested_port* is plausible for *container*.

    We inspect the container's exposed ports (from its image/config) and, if
    there are any explicit port definitions, verify the requested port is among
    them.  If the container has *no* exposed ports at all we let it through —
    the user may know better.
    """
    try:
        # container.attrs["Config"]["ExposedPorts"] → {"80/tcp": {}, "443/tcp": {}}
        exposed = container.attrs.get("Config", {}).get("ExposedPorts") or {}
        # Also check host-published ports under NetworkSettings
        port_bindings = (
            container.attrs.get("HostConfig", {}).get("PortBindings") or {}
        )
        # Merge both sets of known ports
        known_ports: set[int] = set()
        for spec in list(exposed.keys()) + list(port_bindings.keys()):
            try:
                known_ports.add(int(spec.split("/")[0]))
            except (ValueError, IndexError):
                continue

        if known_ports and requested_port not in known_ports:
            available = ", ".join(str(p) for p in sorted(known_ports))
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Port {requested_port} is not exposed by container "
                    f"'{container.name}'. Available ports: {available}"
                ),
            )
    except HTTPException:
        raise
    except Exception:
        pass  # If we can't inspect ports, allow the request through
