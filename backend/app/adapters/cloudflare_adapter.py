"""Cloudflare API v4 adapter for DNS record management.

Uses httpx2 for direct HTTP calls — no SDK dependency needed.
All functions are synchronous (httpx2 sync client).

Error-signaling convention
--------------------------
HARD failures a caller must not silently continue past RAISE a typed
exception. Every Cloudflare API error path funnels through ``_check_response``
and raises ``CloudflareAPIError`` (a ``RuntimeError`` subclass) — uniformly for
a structured CF error envelope, a non-JSON edge response (HTML 5xx pages, bare
"522"/"524"), and an unexpected non-object body. Consequently ``create_a_record``
/ ``update_a_record`` never return ``None``/an error sentinel on failure; on
success they always return a dict (a present-but-null CF ``result`` is coerced
to ``{}``), so a caller never proceeds on a phantom record.

DATA/decisions use return values by design: ``find_record`` returns ``Optional``
(``None`` means "no such record", not an error), and ``is_not_found_error``
classifies an already-gone record so teardown can stay idempotent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx2

logger = logging.getLogger(__name__)

CF_API_BASE = "https://api.cloudflare.com/client/v4"

# Default per-call timeout for Cloudflare API requests.
CF_DEFAULT_TIMEOUT = 30.0

# Shorter cap for cleanup/reconcile paths that run while a GLOBAL service-lifecycle
# lock is held (reconcile_dns, cleanup_dns_record, the orphan-cleanup retry job).
# A slow or unreachable Cloudflare on those paths would otherwise stall ALL service
# lifecycle work for the full default (and chains of find+delete/create/update can
# double it to ~60s). Cap it short to bound the worst-case global stall.
CF_CLEANUP_TIMEOUT = 10.0

# Records requested per page when listing matching DNS records. Cloudflare's
# list endpoint defaults to per_page=20, so a hostname with more than 20 matching
# records (external tampering / a runaway create loop) would SILENTLY truncate at
# page 1 — breaking find_record's "pick deterministically, stable across calls"
# guarantee (the global-lowest id could live on an unseen page). Request a larger
# page in a SINGLE round-trip (never a multi-page loop — that would defeat the
# bounded-stall cap above by issuing N requests under the lifecycle lock) and warn
# if Cloudflare still reports more matches than were returned.
CF_FIND_PER_PAGE = 100


class CloudflareAPIError(RuntimeError):
    """Error returned by Cloudflare's API."""

    def __init__(self, action: str, message: str, *, errors: list[Any] | None = None) -> None:
        super().__init__(f"Cloudflare {action} failed: {message}")
        self.action = action
        self.message = message
        self.errors = errors or []


def _format_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if message and code is not None:
            return f"{message} (code {code})"
        if message:
            return str(message)
    return str(error)


def _is_record_not_found_message(message: str) -> bool:
    normalized = message.lower()
    gone = "not found" in normalized or "does not exist" in normalized
    return gone and ("record" in normalized or "dns" in normalized)


def is_not_found_error(exc: Exception) -> bool:
    """Return True when a Cloudflare DNS-record error means the record is already gone."""
    if isinstance(exc, CloudflareAPIError) and exc.action != "delete_a_record":
        return False
    errors = getattr(exc, "errors", None)
    if isinstance(errors, list) and errors:
        for error in errors:
            if isinstance(error, dict):
                code = error.get("code")
                message = str(error.get("message", ""))
                if code == 81044 or _is_record_not_found_message(message):
                    return True
            elif _is_record_not_found_message(str(error)):
                return True
        return False
    if isinstance(exc, CloudflareAPIError):
        return _is_record_not_found_message(exc.message)
    return _is_record_not_found_message(str(exc))


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _check_response(resp: httpx2.Response, action: str) -> dict:
    """Raise on Cloudflare API error, return result dict on success.

    Cloudflare's edge can return non-JSON bodies (HTML 5xx error pages, plain
    "522"/"524" timeouts, rate-limit notices). Parsing those directly would
    raise a cryptic ``JSONDecodeError`` that surfaces to the operator as the
    cert/DNS failure reason, so translate it into a clear, status-coded
    ``CloudflareAPIError`` — the single typed exception every hard failure raises.
    """
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        snippet = " ".join((resp.text or "").split())[:200]
        message = f"non-JSON response (HTTP {resp.status_code})" + (
            f": {snippet}" if snippet else ""
        )
        raise CloudflareAPIError(action, message) from exc
    if not isinstance(data, dict):
        raise CloudflareAPIError(
            action, f"unexpected response shape (HTTP {resp.status_code})"
        )
    if not data.get("success", False):
        errors = data.get("errors", [])
        if isinstance(errors, list) and errors:
            msg = "; ".join(_format_error(error) for error in errors)
            raise CloudflareAPIError(action, msg, errors=errors)
        raise CloudflareAPIError(action, resp.text)
    return data

def _request(
    method: str,
    path: str,
    *,
    token: str,
    timeout: float,
    action: str,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict:
    """Issue a single Cloudflare API request and return the checked response body."""
    kwargs: dict[str, Any] = {
        "headers": _headers(token),
        "timeout": timeout,
    }
    if json is not None:
        kwargs["json"] = json
    if params is not None:
        kwargs["params"] = params

    url = f"{CF_API_BASE}{path}"
    match method:
        case "GET":
            resp = httpx2.get(url, **kwargs)
        case "POST":
            resp = httpx2.post(url, **kwargs)
        case "PATCH":
            resp = httpx2.patch(url, **kwargs)
        case "DELETE":
            resp = httpx2.delete(url, **kwargs)
        case _:
            raise ValueError(f"Unsupported Cloudflare request method: {method}")
    return _check_response(resp, action)


def ownership_comment(service_id: str) -> str:
    """Ownership marker stamped into the Cloudflare record ``comment`` field for the
    A records tailBale manages.

    Lets reconcile (a) deterministically identify OUR record among several matches and
    (b) safely delete only duplicate records that PROVABLY carry this exact marker for
    the service — never a record without it. Cloudflare caps ``comment`` at 100 chars;
    service ids are short, so this stays well under the limit.
    """
    return f"tailbale-managed:{service_id}"


def list_a_records(
    token: str,
    zone_id: str,
    hostname: str,
    record_type: str = "A",
    timeout: float = CF_DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Return ALL DNS records matching ``hostname``+``record_type``, sorted by id.

    Each dict is the raw Cloudflare record (including ``id``, ``content`` and
    ``comment`` — Cloudflare returns ``comment`` by default, so it is never stripped).
    Sorting by id gives a deterministic, stable order across calls so the lowest-id
    fallback pick is reproducible. Emits a truncation warning when Cloudflare reports
    more matches than this single page returned (never loops pages — that would defeat
    the bounded-stall cap by issuing N requests under the lifecycle lock).
    """
    params = {"type": record_type, "name": hostname, "per_page": CF_FIND_PER_PAGE}
    data = _request(
        "GET",
        f"/zones/{zone_id}/dns_records",
        token=token,
        params=params,
        timeout=timeout,
        action="find_record",
    )
    results = data.get("result") or []
    if not isinstance(results, list):
        results = []
    # Surface truncation: if Cloudflare reports more matches than this single page
    # returned, the deterministic pick is no longer guaranteed global. Don't loop
    # for more pages (that would stall the lifecycle lock); flag it for cleanup.
    info = data.get("result_info")
    if isinstance(info, dict):
        total = info.get("total_count")
        if isinstance(total, int) and total > len(results):
            logger.warning(
                "Cloudflare reports %d %s records for %s but only %d were returned "
                "(per_page=%d); the selected record may not be globally deterministic. "
                "Investigate and remove duplicate records.",
                total, record_type, hostname, len(results), CF_FIND_PER_PAGE,
            )
    return sorted(results, key=lambda r: str(r.get("id", "")))


def find_record(
    token: str,
    zone_id: str,
    hostname: str,
    record_type: str = "A",
    timeout: float = CF_DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """Find an existing DNS record by hostname and type. Returns None if not found.

    Reuses :func:`list_a_records`; on multiple matches it picks the lowest id
    deterministically (stable across calls) and warns. Signature/behavior preserved
    for callers that need a single record (the live Cloudflare DNS health check and
    the service-detail endpoint). The jobs.py orphan-cleanup path deliberately uses
    :func:`list_a_records` instead, since it must locate a SPECIFIC record id among
    all matches rather than the lowest-id pick.
    """
    results = list_a_records(token, zone_id, hostname, record_type, timeout=timeout)
    if not results:
        return None
    if len(results) > 1:
        logger.warning(
            "Found %d %s records for %s; expected 1. Selecting record %s deterministically (sorted by id).",
            len(results),
            record_type,
            hostname,
            results[0].get("id"),
        )
    return results[0]


def create_a_record(
    token: str,
    zone_id: str,
    hostname: str,
    ip: str,
    timeout: float = CF_DEFAULT_TIMEOUT,
    comment: str | None = None,
) -> dict[str, Any]:
    """Create an A record pointing to the given IP. proxied=false.

    When ``comment`` is set it is included in the Cloudflare request body (e.g. the
    ownership marker from :func:`ownership_comment`). Omitted by default so other
    callers stay byte-for-byte unchanged.
    """
    body = {
        "type": "A",
        "name": hostname,
        "content": ip,
        "ttl": 1,  # 1 = auto
        "proxied": False,
    }
    if comment is not None:
        body["comment"] = comment
    data = _request(
        "POST",
        f"/zones/{zone_id}/dns_records",
        token=token,
        json=body,
        timeout=timeout,
        action="create_a_record",
    )
    # Coerce a present-but-null ``result`` to {}: Cloudflare normally returns the
    # created record, but ``data.get("result", {})`` only falls back when the key
    # is ABSENT — a literal ``"result": null`` would yield None and make the
    # ``result.get("id")`` below raise a cryptic AttributeError instead of a
    # clear Cloudflare/record-id error.
    result = data.get("result") or {}
    logger.info("Created A record %s -> %s (id=%s)", hostname, ip, result.get("id"))
    return result


def update_a_record(
    token: str,
    zone_id: str,
    record_id: str,
    ip: str,
    timeout: float = CF_DEFAULT_TIMEOUT,
    comment: str | None = None,
) -> dict[str, Any]:
    """Update an existing A record's IP value.

    When ``comment`` is set it is included in the Cloudflare request body so a record
    can be (re)stamped with the ownership marker on update. Omitted by default.
    """
    body = {
        "content": ip,
    }
    if comment is not None:
        body["comment"] = comment
    data = _request(
        "PATCH",
        f"/zones/{zone_id}/dns_records/{record_id}",
        token=token,
        json=body,
        timeout=timeout,
        action="update_a_record",
    )
    result = data.get("result") or {}
    logger.info("Updated A record %s to %s", record_id, ip)
    return result


def delete_a_record(
    token: str, zone_id: str, record_id: str, timeout: float = CF_DEFAULT_TIMEOUT
) -> None:
    """Delete a DNS record by ID."""
    _request(
        "DELETE",
        f"/zones/{zone_id}/dns_records/{record_id}",
        token=token,
        timeout=timeout,
        action="delete_a_record",
    )
    logger.info("Deleted DNS record %s", record_id)
