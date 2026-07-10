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
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.adapters.cloudflare_adapter import CloudflareAPIError, verify_zone
from app.edge.docker_client import docker_client, resolve_socket
from app.reconciler import reconcile_loop
from app.schemas.settings import ConnectionTestResult
from app.secrets import (
    TAILSCALE_AUTH_KEY,
    TS_AUTHKEY_PREFIX,
    cloudflare_credentials,
    is_valid_ts_auth_key,
    read_secret,
)
from app.services.errors import UpstreamApiError

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
