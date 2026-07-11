"""Generate Caddyfile configuration for edge containers."""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from app.fsutil import fsync_directory_strict, fsync_file

if TYPE_CHECKING:
    from app.models.service import Service


def render_snippet_block(snippet: str) -> str:
    r"""Embed a custom Caddy snippet into a per-service site block.

    Strips edge whitespace, then indents each line with one tab under a leading
    newline. ``str.splitlines()`` treats every Python line break (``\r``,
    ``\v``, ``\f``, ``\x1c``-``\x1e``, ``\x85``, ``\u2028``, ``\u2029`` …) as a
    line boundary and discards it; re-joining the pieces with ``\n`` collapses
    them all to ``\n``, which is exactly what Caddy then tokenizes.

    Single source of truth for snippet embedding: the snippet-containment
    validator (``edge/caddy_snippet.py`` ``validate_caddy_snippet``) lexes the
    output of THIS function, so the validated bytes can never diverge from what
    is rendered into the Caddyfile.
    """
    return "\n" + "\n".join(f"\t{line}" for line in snippet.strip().splitlines())


def render_caddyfile(service: Service) -> str:
    """Generate a deterministic Caddyfile from a service's desired state.

    The generated config follows the spec in section 22.1:
    - auto_https off (certs are mounted from the orchestrator)
    - TLS with file-based certs
    - reverse_proxy to upstream container via Docker DNS
    """
    preserve_host_block = ""
    if not service.preserve_host_header:
        # Caddy preserves the original Host by default. When the user opts OUT,
        # rewrite Host to the upstream container address so the app sees its own name.
        preserve_host_block = "\t\theader_up Host {upstream_hostport}"

    custom_snippet = ""
    if service.custom_caddy_snippet:
        custom_snippet = render_snippet_block(service.custom_caddy_snippet)

    upstream = f"{service.upstream_container_name}:{service.upstream_port}"

    # Caddy's reverse_proxy defaults to plain HTTP, so we only emit a scheme
    # when the upstream speaks HTTPS.  Caddy rejects an explicit scheme that
    # conflicts with the conventional port — both ``http://<host>:443`` and
    # ``https://<host>:80`` — so HTTP upstreams always use the bare
    # ``host:port`` (which dials plain HTTP on any port, avoiding the :443
    # conflict), and HTTPS upstreams use the ``https://`` prefix except on
    # port 80, where that prefix conflicts.  There we dial the bare address
    # and force TLS to the upstream via ``transport http { tls }`` instead.
    tls_transport = service.upstream_scheme == "https" and service.upstream_port == 80
    if service.upstream_scheme == "https" and not tls_transport:
        upstream_addr = f"https://{upstream}"
    else:
        upstream_addr = upstream

    # Caddy expects tab indentation (caddy fmt standard).
    lines = [
        "{",
        "\tauto_https off",
        "}",
        "",
        f"https://{service.hostname} {{",
        "\ttls /certs/current/fullchain.pem /certs/current/privkey.pem",
        "",
        f"\treverse_proxy {upstream_addr} {{",
    ]

    if preserve_host_block:
        lines.append(preserve_host_block)

    lines.extend([
        "\t\theader_up X-Forwarded-Proto https",
        "\t\theader_up X-Real-IP {remote_host}",
    ])

    if tls_transport:
        # HTTPS upstream on port 80: the ``https://`` scheme prefix would
        # conflict with the conventional HTTP port, so force TLS to the
        # upstream via the transport directive instead.
        lines.extend([
            "\t\ttransport http {",
            "\t\t\ttls",
            "\t\t}",
        ])

    lines.append("\t}")

    if custom_snippet:
        lines.append(custom_snippet)

    lines.append("}")
    lines.append("")  # trailing newline

    return "\n".join(lines)


def write_caddyfile(service: Service, generated_dir: str | Path) -> Path:
    """Write the Caddyfile for a service to disk. Returns the file path."""
    generated_dir = Path(generated_dir)
    service_dir = generated_dir / service.id
    service_dir.mkdir(parents=True, exist_ok=True)
    caddyfile_path = service_dir / "Caddyfile"

    content = render_caddyfile(service)

    # Reclaim stale temp files orphaned by a previous writer that was hard-killed
    # (SIGKILL / power loss) between creating its temp and the atomic rename: the
    # handled-exception path below unlinks its own temp and a clean run renames it
    # away, so any ``.Caddyfile.*.tmp`` still present is a crash orphan. This runs
    # only under the per-service reconcile lock, so no concurrent writer owns an
    # in-flight temp in this dir. Best-effort — a sweep failure must not block the
    # write itself.
    for stale in service_dir.glob(".Caddyfile.*.tmp"):
        with contextlib.suppress(OSError):
            stale.unlink()

    # Write atomically: unique temp file then rename. The temp name embeds
    # pid + thread id + a uuid so two concurrent writers to the same service
    # dir can never race on one temp path (the per-service reconcile lock
    # already serializes callers; this is defensive, matching the cert write).
    tmp_path = service_dir / (
        f".Caddyfile.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        tmp_path.write_text(content, encoding="utf-8")
        fsync_file(tmp_path)
        tmp_path.replace(caddyfile_path)
        fsync_directory_strict(service_dir)
    except Exception:
        with contextlib.suppress(Exception):
            tmp_path.unlink(missing_ok=True)
        raise

    return caddyfile_path
