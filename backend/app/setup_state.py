"""Shared setup-readiness checks for bootstrap and settings endpoints."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.setting import Setting
from app.models.user import User
from app.secrets import (
    CLOUDFLARE_TOKEN,
    TAILSCALE_API_KEY,
    TAILSCALE_AUTH_KEY,
    is_valid_ts_api_key,
    is_valid_ts_auth_key,
    read_secret,
)


def compute_setup_progress(db: Session) -> dict[str, bool]:
    """Return the setup wizard step completion flags."""
    user_exists = db.query(User.id).first() is not None
    base_domain = db.get(Setting, "base_domain")
    cf_zone = db.get(Setting, "cf_zone_id")
    acme_email = db.get(Setting, "acme_email")
    docker_socket = db.get(Setting, "docker_socket_path")

    cf_token_set = bool(read_secret(CLOUDFLARE_TOKEN))
    ts_auth_key = read_secret(TAILSCALE_AUTH_KEY)
    ts_api_key = read_secret(TAILSCALE_API_KEY)
    return {
        "user_exists": user_exists,
        "base_domain_set": bool(base_domain and base_domain.value),
        "cloudflare_configured": bool(cf_zone and cf_zone.value) and cf_token_set,
        "cloudflare_token_set": cf_token_set,
        "acme_email_set": bool(acme_email and acme_email.value),
        "tailscale_configured": is_valid_ts_auth_key(ts_auth_key) and is_valid_ts_api_key(ts_api_key),
        "docker_configured": docker_socket is not None,
    }


def missing_setup_requirements(db: Session) -> list[str]:
    """Return incomplete setup steps using the same flags exposed to the wizard."""
    progress = compute_setup_progress(db)
    missing: list[str] = []
    if not progress["user_exists"]:
        missing.append("user")
    if not progress["base_domain_set"]:
        missing.append("base domain")
    if not progress["cloudflare_configured"]:
        missing.append("Cloudflare zone and token")
    if not progress["acme_email_set"]:
        missing.append("ACME email")
    if not progress["tailscale_configured"]:
        missing.append("Tailscale auth key and API key")
    if not progress["docker_configured"]:
        missing.append("Docker socket")
    return missing
