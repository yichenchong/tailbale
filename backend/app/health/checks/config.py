"""Caddy config-presence subcheck (AR18)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.service import Service


def _check_caddy_config(service: Service, generated_dir: str | Path) -> bool:
    return (Path(generated_dir) / service.id / "Caddyfile").exists()
