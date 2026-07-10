"""HTTPS probe helper tests."""

from unittest.mock import MagicMock

import docker.errors
import pytest

from app.health import probe
from app.models.service import Service


def _service(**overrides):
    defaults = {
        "name": "Test",
        "upstream_container_id": "c1",
        "upstream_container_name": "t",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "t.example.com",
        "base_domain": "example.com",
        "edge_container_name": "e",
        "network_name": "n",
        "ts_hostname": "ts",
    }
    defaults.update(overrides)
    service = Service(**defaults)
    service.id = overrides.get("id", "svc-probe")
    return service


def _client_with_edge(container):
    client = MagicMock()
    client.containers.get.return_value = container
    return client


def _running_edge(exit_code=0, output=b"200"):
    container = MagicMock()
    container.status = "running"
    container.exec_run.return_value = (exit_code, output)
    return container


class TestCheckHttpsProbe:
    def test_false_when_no_ip(self, caplog):
        assert probe.check_https_probe(_service(), None) is False
        assert "missing Tailscale IP" in caplog.text

    def test_false_when_no_client(self, caplog):
        assert probe.check_https_probe(_service(), "100.64.0.1", client=None) is False
        assert "Docker client unavailable" in caplog.text

    def test_edge_container_not_found(self, caplog):
        # find_edge_container returns None (named lookup NotFound + empty label
        # search): the probe must report the container-absent reason and False,
        # never crash or fall through to exec_run on a None container.
        client = MagicMock()
        client.containers.get.side_effect = docker.errors.NotFound("no such container")
        client.containers.list.return_value = []

        assert (
            probe.check_https_probe(_service(hostname="gone.example.com"), "100.64.0.1", client)
            is False
        )
        assert "edge container not found" in caplog.text
        client.containers.list.assert_called_once()

    def test_success(self):
        service = _service(hostname="probe.example.com")
        client = _client_with_edge(_running_edge(0, b"200"))

        assert probe.check_https_probe(service, "100.64.0.1", client=client) is True
        client.containers.get.assert_called_once_with("e")

    def test_runs_curl_in_edge_container(self):
        service = _service(hostname="tls.example.com", edge_container_name="edge_tls")
        container = _running_edge(0, b"200")
        client = _client_with_edge(container)

        probe.check_https_probe(service, "100.64.0.1", client=client)

        container.exec_run.assert_called_once()
        command = container.exec_run.call_args.args[0]
        assert "curl" in command
        assert "https://localhost:443/" in command
        assert "tls.example.com" in " ".join(command)

    def test_uses_configured_healthcheck_path(self):
        service = _service(
            hostname="tls.example.com",
            edge_container_name="edge_tls",
            healthcheck_path="readyz",
        )
        container = _running_edge(0, b"200")
        client = _client_with_edge(container)

        probe.check_https_probe(service, "100.64.0.1", client=client)

        command = container.exec_run.call_args.args[0]
        assert "https://localhost:443/readyz" in command

    def test_5xx_is_failure(self, caplog):
        client = _client_with_edge(_running_edge(0, b"502"))

        assert probe.check_https_probe(_service(hostname="fail.example.com"), "100.64.0.1", client) is False
        assert "upstream returned 5xx" in caplog.text
        assert "http_code=502" in caplog.text

    def test_4xx_is_success(self):
        client = _client_with_edge(_running_edge(0, b"401"))

        assert probe.check_https_probe(_service(hostname="auth.example.com"), "100.64.0.1", client) is True

    def test_connection_error_is_failure(self, caplog):
        client = _client_with_edge(_running_edge(7, b"curl: (7) Failed to connect"))

        assert probe.check_https_probe(_service(hostname="err.example.com"), "100.64.0.1", client) is False
        assert "curl returned non-zero" in caplog.text
        assert "exit_code=7" in caplog.text

    def test_container_not_running(self, caplog):
        container = MagicMock()
        container.status = "restarting"
        client = _client_with_edge(container)

        assert probe.check_https_probe(_service(), "100.64.0.1", client=client) is False
        assert "edge container not running" in caplog.text
        assert "container_status=restarting" in caplog.text

    def test_no_response_is_failure(self, caplog):
        client = _client_with_edge(_running_edge(0, b"000"))

        assert probe.check_https_probe(_service(), "100.64.0.1", client=client) is False
        assert "no HTTP response received" in caplog.text
        assert "http_code=000" in caplog.text

    def test_rejects_malformed_status(self, caplog):
        client = _client_with_edge(_running_edge(0, b"00"))

        assert probe.check_https_probe(_service(hostname="bad.example.com"), "100.64.0.1", client) is False
        assert "did not return a valid HTTP status" in caplog.text

    def test_recovers_via_label_search_after_transient_named_lookup_error(self):
        edge = _running_edge(0, b"200")
        client = MagicMock()
        client.containers.get.side_effect = docker.errors.APIError("daemon busy")
        client.containers.list.return_value = [edge]

        assert probe.check_https_probe(_service(id="svc-transient"), "100.64.0.5", client) is True

    def test_exec_error_is_swallowed_and_reported_false(self, caplog):
        # Defensive outer guard: check_https_probe runs inside run_health_checks'
        # try/finally, so if the in-container exec itself raises (e.g. a Docker
        # APIError mid-exec) the exception must NOT escape and crash the whole
        # health sweep for the service — it is logged and the probe reports False.
        container = MagicMock()
        container.status = "running"
        container.exec_run.side_effect = docker.errors.APIError("exec failed mid-flight")
        client = _client_with_edge(container)

        result = probe.check_https_probe(
            _service(hostname="exec.example.com"), "100.64.0.1", client
        )

        assert result is False
        assert "HTTPS probe exec failed" in caplog.text


class TestClassifyProbeResult:
    @pytest.mark.parametrize(
        ("name", "exit_code", "output", "expected"),
        [
            ("2xx", 0, b"200", True),
            ("3xx", 0, b"301", True),
            ("4xx", 0, b"401", True),
            ("nonzero_curl_exit", 7, b"curl: (7) Failed to connect", False),
            ("5xx", 0, b"502", False),
            ("no_response_000", 0, b"000", False),
            ("non_three_digit_status", 0, b"00", False),
            ("empty_output", 0, b"", False),
            ("none_output", 0, None, False),
            ("trailing_numeric_status", 0, b"xx200", True),
            ("trailing_non_digit_status", 0, b"200xx", False),
        ],
    )
    def test_classifies_curl_exit_and_http_status(self, name, exit_code, output, expected):
        assert name
        assert probe.classify_probe_result(exit_code, output) is expected


class TestProbeFailureReason:
    @pytest.mark.parametrize("output", [b"200", "200"])
    def test_accepts_successful_bytes_or_text_output(self, output):
        assert probe.probe_failure_reason(0, output) is None

    @pytest.mark.parametrize("output", [None, b"", ""])
    def test_accepts_empty_output_shapes_as_invalid_status(self, output):
        assert probe.probe_failure_reason(0, output) == (
            "curl did not return a valid HTTP status",
            None,
        )


class TestSummarizeProbeOutput:
    def test_none_output_is_empty_string(self):
        assert probe.summarize_probe_output(None) == ""

    def test_bytes_are_decoded_and_whitespace_collapsed(self):
        assert probe.summarize_probe_output(b"  curl:  (7)\n failed ") == "curl: (7) failed"

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [
            (0, ""),
            (1, "x"),
            (2, "xx"),
            (3, "xxx"),
            (4, "x..."),
        ],
    )
    def test_truncation_never_exceeds_limit_at_boundary(self, limit, expected):
        assert probe.summarize_probe_output("x" * 500, limit=limit) == expected

    def test_long_output_is_truncated_with_ellipsis(self):
        result = probe.summarize_probe_output("x" * 500, limit=200)
        assert len(result) == 200
        assert result == "x" * 197 + "..."
