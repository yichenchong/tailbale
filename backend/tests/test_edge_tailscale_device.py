"""Tests for edge Tailscale device control-plane helpers."""

import json
from unittest.mock import MagicMock, patch

from app.edge.tailscale_device import _delete_tailscale_device, _get_tailscale_node_id


class TestTailscaleDeviceControlPlane:
    """Direct coverage of the control-plane leaf, otherwise exercised only
    indirectly via remove_edge (which stubs both functions out)."""

    def test_get_node_id_returns_self_id(self):

        container = MagicMock()
        container.exec_run.return_value = (0, json.dumps({"Self": {"ID": "n123abc"}}).encode())
        assert _get_tailscale_node_id(container) == "n123abc"

    def test_get_node_id_none_on_nonzero_exit(self):

        container = MagicMock()
        container.exec_run.return_value = (1, b"tailscale not up")
        assert _get_tailscale_node_id(container) is None

    def test_get_node_id_none_when_self_missing(self):

        container = MagicMock()
        container.exec_run.return_value = (0, json.dumps({}).encode())
        assert _get_tailscale_node_id(container) is None

    def test_get_node_id_none_on_invalid_json(self):

        container = MagicMock()
        container.exec_run.return_value = (0, b"not json{{{")
        assert _get_tailscale_node_id(container) is None

    @patch("app.edge.tailscale_device.httpx2.delete")
    def test_delete_device_returns_true_on_success(self, mock_delete):

        resp = MagicMock()
        resp.is_success = True
        mock_delete.return_value = resp

        assert _delete_tailscale_device("n123", "tskey-api-x") is True
        # Basic auth: API key as username, empty password.
        assert mock_delete.call_args.kwargs["auth"] == ("tskey-api-x", "")

    @patch("app.edge.tailscale_device.httpx2.delete")
    def test_delete_device_returns_false_on_http_error(self, mock_delete):

        resp = MagicMock()
        resp.is_success = False
        resp.status_code = 404
        resp.text = "device not found"
        mock_delete.return_value = resp

        assert _delete_tailscale_device("n123", "tskey-api-x") is False

    @patch("app.edge.tailscale_device.httpx2.delete")
    def test_delete_device_returns_false_on_exception(self, mock_delete):

        mock_delete.side_effect = RuntimeError("network down")
        assert _delete_tailscale_device("n123", "tskey-api-x") is False
