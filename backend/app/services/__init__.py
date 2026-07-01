"""Service orchestration layer.

Decomposed (AR1) from the former ``service_ops`` god-module into cohesive
submodules. The public API — the transport-agnostic operations the routers and
other subsystems call — is re-exported here so callers can ``from app.services
import create_service`` without depending on the internal module layout. The
service layer raises domain exceptions from :mod:`app.services.errors` (AR7),
never FastAPI ``HTTPException``; :mod:`app.main` maps them to HTTP.
"""

from app.services.cert_ops import renew_cert
from app.services.crud import (
    create_service,
    delete_service_record,
    disable_service,
    to_response,
    update_service,
)
from app.services.edge_ops import (
    get_enabled_service_for_edge_action,
    recreate_edge,
    update_edge_job,
)
from app.services.errors import (
    HostnameChangeError,
    HostnameInUse,
    HostnameSuffixInvalid,
    ServiceDisabled,
    ServiceError,
    ServiceNotFound,
    TailscaleAuthKeyMissing,
)

__all__ = [
    "HostnameChangeError",
    "HostnameInUse",
    "HostnameSuffixInvalid",
    "ServiceDisabled",
    # Domain exceptions
    "ServiceError",
    "ServiceNotFound",
    "TailscaleAuthKeyMissing",
    # Operations
    "create_service",
    "delete_service_record",
    "disable_service",
    "get_enabled_service_for_edge_action",
    "recreate_edge",
    "renew_cert",
    "to_response",
    "update_edge_job",
    "update_service",
]
