"""Shared helpers and patch targets for reconciler unit tests."""

# Patch at source modules: reconciler imports them via the module-reference
# pattern (e.g. ``secrets.read_secret``), so the attribute resolves on the source
# module at call time and patching the source still takes effect.
_P_SECRET = "app.secrets.read_secret"
_P_RENDER = "app.edge.config_renderer.render_caddyfile"
_P_WRITE = "app.edge.config_renderer.write_caddyfile"
_P_CERT = "app.certs.renewal_task.process_service_cert"
_P_NETWORK = "app.edge.network_manager.ensure_network"
_P_CREATE_EDGE = "app.edge.container_manager.create_edge_container"
_P_FIND_EDGE = "app.edge.container_manager._find_edge_container"
_P_START = "app.edge.container_manager.start_edge"
_P_TS_IP = "app.edge.tailscale_ops.detect_tailscale_ip"
_P_RELOAD = "app.edge.caddy_admin.reload_caddy"
_P_HEALTH = "app.health.health_checker.run_health_checks"
_P_AGGREGATE = "app.health.health_checker.aggregate_status"
_P_DNS = "app.adapters.dns_reconciler.reconcile_dns"
