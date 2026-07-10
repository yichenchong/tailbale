"""Shared helpers for edge container tests."""

from unittest.mock import MagicMock, patch

import pytest


def _make_service(**overrides):
    """Create a mock Service object."""
    svc = MagicMock()
    svc.id = overrides.get("id", "svc_abc123")
    svc.name = overrides.get("name", "Nextcloud")
    svc.hostname = overrides.get("hostname", "nextcloud.example.com")
    svc.upstream_container_name = overrides.get("upstream_container_name", "nextcloud")
    svc.upstream_port = overrides.get("upstream_port", 80)
    svc.upstream_scheme = overrides.get("upstream_scheme", "http")
    svc.preserve_host_header = overrides.get("preserve_host_header", True)
    svc.custom_caddy_snippet = overrides.get("custom_caddy_snippet")
    svc.edge_container_name = overrides.get("edge_container_name", "edge_nextcloud")
    svc.network_name = overrides.get("network_name", "edge_net_nextcloud")
    svc.ts_hostname = overrides.get("ts_hostname", "edge-nextcloud")
    return svc


class _ConnectStubMixin:
    """Stub the under-test-blocked ``connect`` with a throwaway client.

    These lifecycle helpers route through ``_find_edge_container_for_use``, which
    opens a Docker client via ``connect`` directly (the masking fallback is gone).
    The conftest blocks real Docker access and the tests mock the container
    *lookup*, so a stand-in client is all that's needed to flow through and close.
    """

    @pytest.fixture(autouse=True)
    def _stub_connect(self):
        with patch("app.edge.container_session.connect", return_value=MagicMock()):
            yield
