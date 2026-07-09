"""Service CRUD lifecycle facade.

The lifecycle operations are implemented in cohesive modules:
:mod:`app.services.create`, :mod:`app.services.update`, and
:mod:`app.services.delete`. This module remains as the historical CRUD import
surface for callers that import ``app.services.crud`` directly, while the package
facade in :mod:`app.services` re-exports the same public functions.
"""

from app.services.create import create_service
from app.services.delete import delete_service_record, disable_service
from app.services.mapping import to_response
from app.services.update import update_service

__all__ = [
    "create_service",
    "delete_service_record",
    "disable_service",
    "to_response",
    "update_service",
]
