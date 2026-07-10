"""HTTPS edge probe helpers for per-service health checks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import docker

from app.edge.container_manager import find_edge_container

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)


def summarize_probe_output(output: bytes | str | None, limit: int = 200) -> str:
    if output is None:
        return ""
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[: max(limit, 0)]
    return text[: limit - 3] + "..."


def probe_path(service: Service) -> str:
    path = getattr(service, "healthcheck_path", None) or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def log_https_probe_failure(
    service: Service,
    reason: str,
    *,
    tailscale_ip: str | None,
    container_status: str | None = None,
    exit_code: int | None = None,
    http_code: str | None = None,
    output: bytes | str | None = None,
) -> None:
    details: list[str] = []
    if tailscale_ip:
        details.append(f"tailscale_ip={tailscale_ip}")
    if container_status:
        details.append(f"container_status={container_status}")
    if exit_code is not None:
        details.append(f"exit_code={exit_code}")
    if http_code:
        details.append(f"http_code={http_code}")
    rendered_output = summarize_probe_output(output)
    if rendered_output:
        details.append(f"output={rendered_output!r}")

    detail_str = f" ({', '.join(details)})" if details else ""
    logger.warning(
        "HTTPS probe failed for %s (%s): %s%s",
        service.hostname,
        service.edge_container_name,
        reason,
        detail_str,
    )


def probe_failure_reason(
    exit_code: int, output: bytes | str | None
) -> tuple[str, str | None] | None:
    """Classify a curl HTTPS-probe exec result; ``None`` means healthy.

    Single source for the five-way classification split out of
    ``check_https_probe`` so the decision is testable without a live container
    and cannot drift from the boolean view in ``classify_probe_result``. On
    failure returns ``(reason, http_code)`` where *reason* is the log message and
    *http_code* is the parsed status (``None`` when there is no meaningful code to
    surface). Branches, in order:

    * non-zero curl exit — connection/TLS failure (curl exits 0 for any HTTP
      response, non-zero only for network/TLS errors)
    * status not exactly three digits (covers empty/truncated output)
    * ``"000"`` — curl connected but received no HTTP response
    * ``5xx`` — Caddy served but the upstream is broken
    """
    if exit_code != 0:
        return "curl returned non-zero", None
    raw_output = (
        output.decode("utf-8", errors="replace")
        if isinstance(output, bytes)
        else (output or "")
    )
    raw = raw_output.strip()
    http_code = raw[-3:] if len(raw) >= 3 else raw
    if len(http_code) != 3 or not http_code.isdigit():
        return "curl did not return a valid HTTP status", None
    if http_code == "000":
        return "no HTTP response received", http_code
    if http_code.startswith("5"):
        return "upstream returned 5xx", http_code
    return None


def classify_probe_result(exit_code: int, output: bytes | str | None) -> bool:
    """Return ``True`` iff the curl probe indicates Caddy is serving HTTPS.

    Boolean view of :func:`probe_failure_reason` (a passing probe is one with no
    failure reason). A 2xx/3xx/4xx response counts as serving — a 4xx means the
    upstream requires auth, not that Caddy is down.
    """
    return probe_failure_reason(exit_code, output) is None


def check_https_probe(
    service: Service,
    tailscale_ip: str | None,
    client: docker.DockerClient | None = None,
) -> bool:
    """Verify that Caddy inside the edge container is serving HTTPS.

    The probe runs ``curl`` **inside the edge container** rather than
    connecting from the orchestrator. This avoids the problem where the
    orchestrator container can't reach Tailscale IPs (only edge containers
    are on the tailnet).

    curl is used instead of wget because the edge container's Alpine-based
    BusyBox wget does not use exit code 8 for HTTP errors (it returns 1 for
    all failures), making it impossible to distinguish 4xx (acceptable —
    upstream may require auth) from connection failures. curl exits 0 for
    any HTTP response and non-zero only for network/TLS failures.

    A ``Host`` header matching the configured hostname is sent so Caddy
    routes the request through its reverse_proxy rather than returning 421
    for the unmatched ``localhost`` default.
    """
    if not tailscale_ip:
        log_https_probe_failure(service, "missing Tailscale IP", tailscale_ip=None)
        return False

    if not client:
        log_https_probe_failure(service, "Docker client unavailable", tailscale_ip=tailscale_ip)
        return False

    try:
        container = find_edge_container(
            client, service.id, service.edge_container_name, tolerate_lookup_errors=True
        )
        if container is None:
            log_https_probe_failure(
                service,
                "edge container not found",
                tailscale_ip=tailscale_ip,
            )
            return False
        if container.status != "running":
            log_https_probe_failure(
                service,
                "edge container not running",
                tailscale_ip=tailscale_ip,
                container_status=container.status,
            )
            return False

        exit_code, output = container.exec_run(
            [
                "curl",
                "--silent",
                "--insecure",
                "--max-time",
                "5",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                f"Host: {service.hostname}",
                f"https://localhost:443{probe_path(service)}",
            ],
            environment={"HOME": "/tmp"},
        )

        failure = probe_failure_reason(exit_code, output)
        if failure is None:
            return True
        reason, http_code = failure
        log_https_probe_failure(
            service,
            reason,
            tailscale_ip=tailscale_ip,
            container_status=container.status,
            exit_code=exit_code or None,
            http_code=http_code,
            output=output,
        )
        return False

    except Exception:
        logger.warning("HTTPS probe exec failed for %s", service.edge_container_name, exc_info=True)
        return False
