"""Tests for edge Caddy admin operations."""

from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app.edge.caddy_admin import reload_caddy
from tests._edge_helpers import _ConnectStubMixin


class TestReloadCaddy(_ConnectStubMixin):
    @patch("app.edge.container_manager._find_edge_container")
    def test_reloads_caddy(self, mock_find):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"config reloaded")
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test")

        assert "reloaded" in result
        mock_container.exec_run.assert_called_once_with(
            "caddy reload --config /etc/caddy/Caddyfile --force"
        )

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_on_failure(self, mock_find):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"error: invalid config")
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="Caddy reload failed"):
            reload_caddy("svc_123", "edge_test")

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            reload_caddy("svc_123", "edge_test")

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_when_container_not_running(self, mock_find):
        """A non-running edge container can't be exec'd, so reload refuses early
        with a clear error instead of attempting (and failing) the exec."""

        mock_container = MagicMock()
        mock_container.status = "exited"
        # Would succeed if the guard were bypassed — proves the guard, not luck.
        mock_container.exec_run.return_value = (0, b"config reloaded")
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="cannot reload Caddy"):
            reload_caddy("svc_123", "edge_test")
        mock_container.exec_run.assert_not_called()

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_retries_when_container_is_restarting(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            docker.errors.APIError(
                '409 Client Error for http+docker://localhost/v1.47/containers/x/exec: '
                'Conflict ("Container x is restarting, wait until the container is running")'
            ),
            (0, b"config reloaded"),
        ]
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test", max_retries=2, retry_delay=0)

        assert "reloaded" in result
        assert mock_container.exec_run.call_count == 2
        mock_container.reload.assert_called()

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_when_container_never_stabilizes(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = docker.errors.APIError(
            '409 Client Error for http+docker://localhost/v1.47/containers/x/exec: '
            'Conflict ("Container x is restarting, wait until the container is running")'
        )
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="never reached a stable running container"):
            reload_caddy("svc_123", "edge_test", max_retries=2, retry_delay=0)

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_real_reload_failure_after_transient_conflict_is_not_masked(
        self, mock_find, mock_sleep
    ):
        """A genuine reload failure on a later attempt must surface the real
        error, not the transient "restarting" conflict from an earlier attempt."""

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            docker.errors.APIError(
                '409 Client Error for http+docker://localhost/v1.47/containers/x/exec: '
                'Conflict ("Container x is restarting, wait until the container is running")'
            ),
            (1, b"error: adapting config using caddyfile: invalid directive"),
        ]
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError) as excinfo:
            reload_caddy("svc_123", "edge_test", max_retries=2, retry_delay=0)

        message = str(excinfo.value)
        assert "Caddy reload failed (exit 1)" in message
        assert "invalid directive" in message
        assert "never reached a stable running container" not in message

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_retries_when_admin_api_connection_refused(self, mock_find, mock_sleep):
        """Caddy's admin API (:2019) may not be up immediately after the
        container starts: the reload exec returns non-zero with "connection
        refused". This is the documented reason reload_caddy retries, so it must
        retry and succeed once the API is ready instead of surfacing the
        transient error as a hard failure."""

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            (1, b"Error: dial tcp 127.0.0.1:2019: connect: connection refused"),
            (0, b"config reloaded"),
        ]
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test", max_retries=3, retry_delay=0)

        assert "reloaded" in result
        assert mock_container.exec_run.call_count == 2
