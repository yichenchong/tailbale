"""Application version, read from the VERSION file at the repo/image root."""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE_LOCATIONS = [
    Path(__file__).resolve().parent.parent.parent / "VERSION",  # dev: repo root
    Path("/app") / "VERSION",  # production: Docker image root
]


def get_version() -> str:
    """Return the current app version string, or 'dev' if not found.

    A location that exists but reads back empty/whitespace (a 0-byte VERSION from
    a truncated build or a botched volume mount — the same failure mode
    ``config.py`` guards for the JWT secret) is treated as "not found" and the
    next location is tried, ultimately falling through to 'dev'. Reading directly
    and catching ``OSError`` (rather than an ``is_file()`` pre-check) also closes
    the TOCTOU window where the file vanishes between the check and the read.
    """
    for path in _VERSION_FILE_LOCATIONS:
        try:
            version = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version
    return "dev"


__version__ = get_version()
