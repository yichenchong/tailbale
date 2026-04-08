"""Application version, read from the VERSION file at the repo/image root."""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE_LOCATIONS = [
    Path(__file__).resolve().parent.parent.parent / "VERSION",  # dev: repo root
    Path("/app") / "VERSION",  # production: Docker image root
]


def get_version() -> str:
    """Return the current app version string, or 'dev' if not found."""
    for path in _VERSION_FILE_LOCATIONS:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return "dev"


__version__ = get_version()
