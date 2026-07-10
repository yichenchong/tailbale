"""Service response shaping and edge-name derivation helpers.

This module is intentionally transport- and side-effect-light: it owns the wire
response mapping and deterministic service naming rules without lifecycle locks,
DB write sections, event emission, or edge orchestration.
"""

import re

from sqlalchemy.orm import Session

from app.models.certificate import Certificate
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import ServiceResponse, ServiceStatusResponse
from app.services.service_fields import RESPONSE_PASSTHROUGH_FIELDS
from app.timeutil import iso

# Tailscale passes ts_hostname to `tailscale up --hostname=`, which is a single
# DNS label limited to 63 chars (RFC 1035 §3.1). Longer values are silently
# truncated/rejected, so the live MagicDNS hostname would diverge from the
# persisted ts_hostname — risking collisions and cert-hostname confusion.
# ts_hostname is f"edge-{slug}" (5-char prefix), so the slug must stay within
# 63 - len("edge-") = 58 chars.
_MAX_SLUG_LEN = 63 - len("edge-")  # 58
# Cap the *base* slug at a round 50 chars — comfortably below _MAX_SLUG_LEN and
# leaving 8 chars of headroom for the "-{n}" uniqueness suffix appended on
# collisions, so even suffixed slugs stay within the 58-char budget.
_MAX_BASE_SLUG_LEN = 50


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "service"


def edge_container_name(slug: str) -> str:
    return f"edge_{slug}"


def network_name(slug: str) -> str:
    return f"edge_net_{slug}"


def ts_hostname(slug: str) -> str:
    return f"edge-{slug}"


def derive_edge_names(slug: str) -> tuple[str, str, str]:
    """Return ``(edge_container_name, network_name, ts_hostname)`` for *slug*."""
    return edge_container_name(slug), network_name(slug), ts_hostname(slug)


def unique_slug(db: Session, name: str) -> str:
    """Return a slug derived from *name* that doesn't collide with edge names."""
    base = slugify(name)[:_MAX_BASE_SLUG_LEN].rstrip("-") or "service"
    slug = base
    suffix = 2
    while (
        db.query(Service)
        .filter(
            (Service.edge_container_name == edge_container_name(slug))
            | (Service.network_name == network_name(slug))
            | (Service.ts_hostname == ts_hostname(slug))
        )
        .first()
    ):
        marker = f"-{suffix}"
        # Trim the base further so a multi-digit suffix can't overflow the budget.
        slug = f"{base[: _MAX_SLUG_LEN - len(marker)].rstrip('-')}{marker}"
        suffix += 1
    return slug


def to_response(
    svc: Service,
    status: ServiceStatus | None,
    cert: Certificate | None = None,
) -> ServiceResponse:
    """Shape a service (+ optional status/cert) into its API response model."""
    status_resp = None
    if status:
        status_resp = ServiceStatusResponse(
            phase=status.phase,
            message=status.message,
            tailscale_ip=status.tailscale_ip,
            edge_container_id=status.edge_container_id,
            last_reconciled_at=iso(status.last_reconciled_at),
            health_checks=status.health_checks,
            cert_expires_at=iso(cert.expires_at if cert else None),
            probe_retry_at=iso(status.probe_retry_at),
            probe_retry_attempt=status.probe_retry_attempt,
            last_probe_at=iso(status.last_probe_at),
        )
    return ServiceResponse(
        status=status_resp,
        created_at=svc.created_at.isoformat(),
        updated_at=svc.updated_at.isoformat(),
        **{field: getattr(svc, field) for field in RESPONSE_PASSTHROUGH_FIELDS},
    )
