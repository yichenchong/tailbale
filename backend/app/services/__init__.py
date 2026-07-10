"""Service orchestration layer.

Decomposed (AR1) from the former ``service_ops`` god-module into cohesive
submodules. The public API — the transport-agnostic operations the routers and
other subsystems call — is re-exported here so callers can ``from app.services
import create_service`` without depending on the internal module layout. The
service layer raises domain exceptions from :mod:`app.services.errors` (AR7),
never FastAPI ``HTTPException``; :mod:`app.main` maps them to HTTP.
"""

# resolve_socket re-exported so routers depend on the services facade
# (routers -> services -> edge) rather than importing app.edge.docker_client
# directly (AR9). service_actions.py passes it to update_edge_job. The
# container-listing and upstream-validation Docker round-trips that used to
# need docker_client now live behind intentful ops in app.services.diagnostics
# (AR15), so docker_client is no longer re-exported here.
from app.edge.docker_client import resolve_socket
from app.services.cert_ops import renew_cert
from app.services.create import create_service
from app.services.delete import delete_service_record, disable_service
from app.services.edge_ops import (
    full_health_check,
    get_edge_logs,
    get_edge_version,
    get_enabled_service_for_edge_action,
    recreate_edge,
    reload_caddy_action,
    restart_edge_action,
    update_edge_job,
)
from app.services.errors import (
    DockerUnavailable,
    HostnameChangeError,
    HostnameInUse,
    HostnameSuffixInvalid,
    ServiceDisabled,
    ServiceError,
    ServiceNotFound,
    TailscaleAuthKeyMissing,
    UpstreamApiError,
)
from app.services.mapping import to_response
from app.services.update import update_service

__all__ = [
    "DockerUnavailable",
    "HostnameChangeError",
    "HostnameInUse",
    "HostnameSuffixInvalid",
    "ServiceDisabled",
    # Domain exceptions
    "ServiceError",
    "ServiceNotFound",
    "TailscaleAuthKeyMissing",
    "UpstreamApiError",
    # Operations
    "create_service",
    "delete_service_record",
    "disable_service",
    "full_health_check",
    "get_edge_logs",
    "get_edge_version",
    "get_enabled_service_for_edge_action",
    "recreate_edge",
    "reload_caddy_action",
    "renew_cert",
    "resolve_socket",
    "restart_edge_action",
    "to_response",
    "update_edge_job",
    "update_service",
]
