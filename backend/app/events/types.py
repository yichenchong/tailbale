"""Typed catalogue of every event ``kind`` the app emits (AR14).

Single source of truth for event-kind identifiers. Emitters reference these
constants (``EventKind.SERVICE_CREATED``) instead of bare string literals, so a
rename is a mechanical, greppable, type-checked change and the kind universe
lives in exactly one place. ``EVENT_KINDS`` — the runtime registry served by
``GET /api/events/kinds`` and used by :func:`app.events.event_emitter.emit_event`
as a drift canary — is DERIVED from these constants, so registering a new kind
is a single edit here (add a class attribute) rather than a literal plus a
hand-maintained mirror set.

Values are plain ``str`` (not an ``Enum``) so they are stored verbatim in the
``Event.kind`` column and compare/serialize byte-for-byte identically to the
historical string literals — this catalogue is a behavior-preserving relocation
of the kind strings, nothing more.
"""

from __future__ import annotations


class EventKind:
    """String constants for every emitted event kind, grouped by subsystem."""

    # Service lifecycle (services/create.py, update.py, delete.py)
    SERVICE_CREATED = "service_created"
    SERVICE_UPDATED = "service_updated"
    SERVICE_DISABLED = "service_disabled"
    SERVICE_DELETED = "service_deleted"
    SERVICE_SNIPPET_CHANGED = "service_snippet_changed"

    # Edge container / proxy lifecycle (services/edge_ops.py; reconciler
    # steps.py dicts emitted via status.py)
    EDGE_STARTED = "edge_started"
    EDGE_RESTARTED = "edge_restarted"
    EDGE_RECREATED = "edge_recreated"
    EDGE_UPDATED = "edge_updated"
    CADDY_RELOADED = "caddy_reloaded"
    TAILSCALE_IP_ACQUIRED = "tailscale_ip_acquired"

    # Certificates (certs/renewal_task.py)
    CERT_ISSUED = "cert_issued"
    CERT_RENEWED = "cert_renewed"
    CERT_FAILED = "cert_failed"

    # DNS records (adapters/dns_reconciler.py, reconciler/steps.py)
    DNS_CREATED = "dns_created"
    DNS_UPDATED = "dns_updated"
    DNS_REMOVED = "dns_removed"
    DNS_UPDATE_FAILED = "dns_update_failed"
    DNS_CLEANUP_FAILED = "dns_cleanup_failed"
    DNS_DUPLICATE_REMOVED = "dns_duplicate_removed"

    # Orphaned-DNS cleanup jobs (services/delete.py, routers/jobs.py)
    DNS_ORPHAN_CREATED = "dns_orphan_created"
    DNS_ORPHAN_RESOLVED = "dns_orphan_resolved"
    DNS_ORPHAN_RETRY_FAILED = "dns_orphan_retry_failed"
    DNS_ORPHAN_DISMISSED = "dns_orphan_dismissed"

    # Reconciliation (reconciler/probe_retry.py, reconciler/steps.py,
    # reconciler/reconciler.py)
    PROBE_RETRY_PHASE_CHANGE = "probe_retry_phase_change"
    RECONCILE_COMPLETED = "reconcile_completed"
    RECONCILE_FAILED = "reconcile_failed"


# Derived registry: every ``str`` class attribute of EventKind. Adding a kind is
# a single edit above; this set (and thus the /api/events/kinds endpoint and the
# emit_event drift canary) updates automatically.
EVENT_KINDS: frozenset[str] = frozenset(
    value
    for name, value in vars(EventKind).items()
    if not name.startswith("_") and isinstance(value, str)
)
