"""Generate Caddyfile configuration for edge containers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.service import Service


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
        preserve_host_block = "    header_up Host {upstream_hostport}"

    custom_snippet = ""
    if service.custom_caddy_snippet:
        # Indent each line of the custom snippet
        lines = service.custom_caddy_snippet.strip().splitlines()
        custom_snippet = "\n" + "\n".join(f"  {line}" for line in lines)

    upstream = f"{service.upstream_container_name}:{service.upstream_port}"

    lines = [
        "{",
        "  auto_https off",
        "}",
        "",
        f"https://{service.hostname} {{",
        "  tls /certs/fullchain.pem /certs/privkey.pem",
        "",
        f"  reverse_proxy {service.upstream_scheme}://{upstream} {{",
    ]

    if preserve_host_block:
        lines.append(preserve_host_block)

    lines.extend([
        "    header_up X-Forwarded-Proto https",
        "    header_up X-Forwarded-Host {host}",
        "    header_up X-Real-IP {remote_host}",
        "  }",
    ])

    if custom_snippet:
        lines.append(custom_snippet)

    lines.append("}")
    lines.append("")  # trailing newline

    return "\n".join(lines)


def write_caddyfile(service: Service, generated_dir: Path) -> Path:
    """Write the Caddyfile for a service to disk. Returns the file path."""
    service_dir = generated_dir / service.id
    service_dir.mkdir(parents=True, exist_ok=True)
    caddyfile_path = service_dir / "Caddyfile"

    content = render_caddyfile(service)

    # Write atomically: temp file then rename
    tmp_path = caddyfile_path.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(caddyfile_path)

    return caddyfile_path
