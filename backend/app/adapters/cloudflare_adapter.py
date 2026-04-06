"""Cloudflare API v4 adapter for DNS record management.

Uses httpx for direct HTTP calls — no SDK dependency needed.
All functions are synchronous (httpx sync client).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _check_response(resp: httpx.Response, action: str) -> dict:
    """Raise on Cloudflare API error, return result dict on success."""
    data = resp.json()
    if not data.get("success", False):
        errors = data.get("errors", [])
        msg = "; ".join(e.get("message", str(e)) for e in errors) if errors else resp.text
        raise RuntimeError(f"Cloudflare {action} failed: {msg}")
    return data


def list_zones(token: str) -> list[dict[str, Any]]:
    """List all zones accessible with the given token."""
    resp = httpx.get(f"{CF_API_BASE}/zones", headers=_headers(token), timeout=30)
    data = _check_response(resp, "list_zones")
    return data.get("result", [])


def get_zone(token: str, zone_id: str) -> dict[str, Any]:
    """Get a specific zone by ID. Raises on failure."""
    resp = httpx.get(f"{CF_API_BASE}/zones/{zone_id}", headers=_headers(token), timeout=30)
    data = _check_response(resp, "get_zone")
    return data.get("result", {})


def find_record(
    token: str, zone_id: str, hostname: str, record_type: str = "A"
) -> dict[str, Any] | None:
    """Find an existing DNS record by hostname and type. Returns None if not found."""
    params = {"type": record_type, "name": hostname}
    resp = httpx.get(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records",
        headers=_headers(token),
        params=params,
        timeout=30,
    )
    data = _check_response(resp, "find_record")
    results = data.get("result", [])
    return results[0] if results else None


def create_a_record(
    token: str, zone_id: str, hostname: str, ip: str
) -> dict[str, Any]:
    """Create an A record pointing to the given IP. proxied=false."""
    body = {
        "type": "A",
        "name": hostname,
        "content": ip,
        "ttl": 1,  # 1 = auto
        "proxied": False,
    }
    resp = httpx.post(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records",
        headers=_headers(token),
        json=body,
        timeout=30,
    )
    data = _check_response(resp, "create_a_record")
    result = data.get("result", {})
    logger.info("Created A record %s -> %s (id=%s)", hostname, ip, result.get("id"))
    return result


def update_a_record(
    token: str, zone_id: str, record_id: str, ip: str
) -> dict[str, Any]:
    """Update an existing A record's IP value."""
    body = {
        "content": ip,
    }
    resp = httpx.patch(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
        headers=_headers(token),
        json=body,
        timeout=30,
    )
    data = _check_response(resp, "update_a_record")
    result = data.get("result", {})
    logger.info("Updated A record %s to %s", record_id, ip)
    return result


def delete_a_record(token: str, zone_id: str, record_id: str) -> None:
    """Delete a DNS record by ID."""
    resp = httpx.delete(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
        headers=_headers(token),
        timeout=30,
    )
    _check_response(resp, "delete_a_record")
    logger.info("Deleted DNS record %s", record_id)
