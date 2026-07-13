"""Canonical service field-name sets (AR17).

The same service attribute list is otherwise retyped across the request->model
copy in :mod:`app.services.create`, the model->response projection in
:mod:`app.services.mapping`, and the writable-field allowlist in
:mod:`app.services.update`. Centralizing the *mechanical* 1:1 sets here removes
that duplication so adding/removing a passthrough field is a single edit rather
than several hand-synced literals.

Only the mechanical field copies are driven from these sets. Fields that need
transformation (``created_at``/``updated_at`` isoformat, the computed ``status``
projection) or derivation (``base_domain``, ``edge_container_name``,
``network_name``, ``ts_hostname``) stay spelled out at their call sites.
"""

from __future__ import annotations

# Fields copied 1:1 from a ``ServiceCreate`` body onto a new ``Service`` row.
# Excludes ``base_domain`` (tracks the configured domain) and the derived edge
# identity fields (``edge_container_name``/``network_name``/``ts_hostname``),
# which create.py sets explicitly.
CREATE_COPY_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "enabled",
        "upstream_container_id",
        "upstream_container_name",
        "upstream_scheme",
        "upstream_port",
        "healthcheck_path",
        "hostname",
        "preserve_host_header",
        "custom_caddy_snippet",
        "app_profile",
        "additional_networks",
    }
)

# Fields copied 1:1 from a ``Service`` row onto its ``ServiceResponse``.
# Excludes the transformed ``created_at``/``updated_at`` (isoformat) and the
# computed ``status`` projection, which mapping.py builds explicitly.
RESPONSE_PASSTHROUGH_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "enabled",
        "upstream_container_id",
        "upstream_container_name",
        "upstream_scheme",
        "upstream_port",
        "healthcheck_path",
        "hostname",
        "base_domain",
        "edge_container_name",
        "network_name",
        "ts_hostname",
        "preserve_host_header",
        "custom_caddy_snippet",
        "app_profile",
        "additional_networks",
    }
)

# SECURITY BOUNDARY: the exhaustive, explicitly reviewed allowlist of service
# fields a ``ServiceUpdate`` may patch. Hostname (and its ``base_domain``) is
# deliberately absent — it is handled by the dedicated destructive
# hostname-change path, not the generic field patch. This list is authored by
# hand, NOT derived from the model: every entry is a conscious decision to let
# the update endpoint write that column, so it MUST stay an explicit tuple.
UPDATABLE_FIELDS: tuple[str, ...] = (
    "name",
    "upstream_scheme",
    "upstream_port",
    "healthcheck_path",
    "enabled",
    "preserve_host_header",
    "custom_caddy_snippet",
    "app_profile",
    "additional_networks",
)

# Fields whose change requires re-rendering the Caddyfile / re-probing health,
# so an update touching any of them schedules an immediate reconcile instead of
# waiting for the periodic loop.
CONFIG_AFFECTING_FIELDS: frozenset[str] = frozenset(
    {
        "upstream_port",
        "upstream_scheme",
        "preserve_host_header",
        "custom_caddy_snippet",
        "healthcheck_path",
        "additional_networks",
    }
)
