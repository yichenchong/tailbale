"""Transport-agnostic domain exceptions for the service layer.

The orchestration layer (the :mod:`app.services.create` / :mod:`app.services.update`
/ :mod:`app.services.delete` lifecycle ops, :mod:`app.services.edge_ops`,
:mod:`app.services.cert_ops`) raises these instead of FastAPI ``HTTPException`` so
it stays reusable off-HTTP (reconciler / background paths can call the same
functions without importing FastAPI's request/response machinery). One set of
``@app.exception_handler`` mappers in :mod:`app.main` translates each to the exact
status code + detail string the routers used to raise inline — the observable HTTP
behavior is unchanged.

Each exception carries the canonical ``status_code`` + ``detail`` it maps to, so
the central handler is a single ``JSONResponse(status_code=exc.status_code,
{"detail": exc.detail})`` with no per-type branching. ``detail`` never leaks
internals (Docker socket paths, ``str(exc)`` of an upstream failure); those are
logged server-side by the caller before raising.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base for service-layer failures that map to a specific HTTP status.

    Subclasses set a default ``status_code`` + ``detail``; both may be overridden
    per-instance for the few call sites that build a contextual message (e.g. the
    hostname-suffix error names the configured domain).
    """

    status_code: int = 400
    detail: str = "Service operation failed"

    def __init__(self, detail: str | None = None, *, status_code: int | None = None):
        if detail is not None:
            self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.detail)


class ServiceNotFound(ServiceError):
    """The requested service row does not exist -> 404 'Service not found'."""

    status_code = 404
    detail = "Service not found"


class ServiceDisabled(ServiceError):
    """An edge action targeted a disabled service -> 409 'Service is disabled'."""

    status_code = 409
    detail = "Service is disabled"


class HostnameInUse(ServiceError):
    """Another service already owns the requested hostname -> 409.

    The detail names the offending hostname, matching the router's original
    ``f"Hostname '{hostname}' is already in use"`` string.
    """

    status_code = 409
    detail = "Hostname is already in use"

    def __init__(self, hostname: str):
        super().__init__(f"Hostname '{hostname}' is already in use")


class HostnameSuffixInvalid(ServiceError):
    """The hostname is not a subdomain of the configured base domain -> 422.

    Mirrors the original ``f"Hostname '{hostname}' must end with '.{domain}'"``.
    """

    status_code = 422
    detail = "Hostname must end with the configured base domain"

    def __init__(self, hostname: str, configured_domain: str | None):
        super().__init__(
            f"Hostname '{hostname}' must end with '.{configured_domain}'"
        )


class TailscaleAuthKeyMissing(ServiceError):
    """An edge (re)create needs a Tailscale auth key that is not set -> 400."""

    status_code = 400
    detail = "Tailscale auth key not configured"


class HostnameChangeError(ServiceError):
    """A hostname change could not complete its destructive DNS/cert teardown.

    Carries the contextual detail + status the update path used inline (502 when
    Cloudflare rejects the old-record delete, 422 when credentials are missing
    but a live DNS record still exists).
    """

    def __init__(self, detail: str, *, status_code: int):
        super().__init__(detail, status_code=status_code)


class DockerUnavailable(ServiceError):
    """The Docker daemon could not be reached -> 503 'Docker is unavailable'.

    Raised by any layer whose operation needs the Docker daemon when the
    connection fails (``docker.errors.DockerException`` /
    ``requests.ConnectionError``). Replaces the hand-rolled ``HTTPException(503,
    "Docker is unavailable")`` the routers used inline (AR-R3-2). The underlying
    ``str(exc)`` — which can embed the socket path — is logged server-side by the
    caller; only the static ``detail`` reaches the client.
    """

    status_code = 503
    detail = "Docker is unavailable"


class UpstreamApiError(ServiceError):
    """An external/upstream API call failed -> 502 (Bad Gateway).

    Covers the Cloudflare API (orphan-cleanup retry) and the Docker log proxy
    (developer main-logs) failures the routers used to map inline with
    ``HTTPException(502, f"...: {exc}")`` (AR-R3-2). The concrete failure
    (``str(exc)`` — may embed a token, socket path, or remote error body) is
    logged server-side by the caller; only a static, non-leaking ``detail``
    reaches the client. Callers pass the contextual static message
    (e.g. ``"Cloudflare API error"``) but never interpolate ``str(exc)``.
    """

    status_code = 502
    detail = "Upstream service request failed"


class AdditionalNetworkInvalid(ServiceError):
    """An additional edge network attachment is invalid -> 422.

    Raised when a configured additional network cannot be validated against the
    derived service state that the request-body schema cannot see — currently
    when it names the service's own primary per-service network (``network_name``
    is derived after schema validation), which the reconciler would silently skip
    while the API/UI would still show it as configured.
    """

    status_code = 422
    detail = "Invalid additional edge network"
