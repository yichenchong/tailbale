"""Thin re-export shim for the service orchestration layer.

The former 716-line god-module was decomposed (AR1) into a ``services/`` package:

* :mod:`app.services.crud`     — create / update / disable / delete + response
                                 mapping (``to_response``) + slug helpers + the
                                 tier-1 ``_SERVICE_LIFECYCLE_MUTEX`` acquisition.
* :mod:`app.services.edge_ops` — ``recreate_edge`` / ``update_edge_job`` and the
                                 ``get_enabled_service_for_edge_action`` guard.
* :mod:`app.services.cert_ops` — ``renew_cert`` decision.
* :mod:`app.services.errors`   — transport-agnostic domain exceptions.

This module preserves every previously-public import path
(``app.services.service_ops.<name>``) so out-of-scope importers keep working. New
code SHOULD import from the specific submodule (or ``app.services``) instead.

Names are re-exported (not re-implemented) so patching ``app.services.crud.X`` /
``app.services.edge_ops.X`` still takes effect when a caller reaches these through
``service_ops`` — the functions live in exactly one place.
"""

from app.services.cert_ops import renew_cert
from app.services.crud import (
    _delete_service_record_locked,
    _mark_status_disabled,
    _reconcile_in_background,
    _slugify,
    _unique_slug,
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

# Backward-compatible aliases for the pre-AR4 private names. Out-of-scope
# importers (routers/settings.py reset-all, existing tests) reference these at
# their old spelling; keep the aliases so the boundary rename does not break them.
_to_response = to_response
_delete_service_record = delete_service_record
_get_enabled_service_for_edge_action = get_enabled_service_for_edge_action

__all__ = [
    "_delete_service_record",
    "_delete_service_record_locked",
    "_get_enabled_service_for_edge_action",
    "_mark_status_disabled",
    "_reconcile_in_background",
    "_slugify",
    # Backward-compatible private aliases.
    "_to_response",
    "_unique_slug",
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
