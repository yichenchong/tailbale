import os
from collections.abc import Callable
from typing import Any, NamedTuple

import docker
import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.edge.docker_client import docker_client, resolve_socket
from app.schemas.settings import (
    AllSettingsResponse,
    CloudflareSettingsResponse,
    CloudflareSettingsUpdate,
    ConnectionTestResult,
    DockerSettingsResponse,
    DockerSettingsUpdate,
    GeneralSettingsResponse,
    GeneralSettingsUpdate,
    PathSettingsResponse,
    PathSettingsUpdate,
    TailscaleSettingsResponse,
    TailscaleSettingsUpdate,
)
from app.secrets import (
    ALL_SECRETS,
    CLOUDFLARE_TOKEN,
    TAILSCALE_API_KEY,
    TAILSCALE_AUTH_KEY,
    TS_APIKEY_PREFIX,
    TS_AUTHKEY_PREFIX,
    delete_secret,
    is_valid_ts_api_key,
    is_valid_ts_auth_key,
    read_secret,
    write_secret,
)
from app.settings_store import get_all_settings, get_positive_int_setting, get_setting, set_setting
from app.setup_state import missing_setup_requirements

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


def _require_developer_mode(db: Session) -> None:
    if get_setting(db, "developer_mode") != "true":
        raise HTTPException(status_code=403, detail="Developer Mode must be enabled first")


def _secret_configured(name: str) -> bool:
    return bool(read_secret(name))


class _Field(NamedTuple):
    """One declarative settings field.

    ``key`` is the settings-store key; ``serialize`` turns the validated schema
    attribute into its stored string form (write path); ``read`` reconstructs
    the response value from the settings dict / db (read path). Holding both
    directions in one descriptor removes the mirror-drift between the update
    handlers and ``_build_response`` that AR10 targets.
    """

    key: str
    serialize: Callable[[Any], str]
    read: Callable[[Session, dict[str, str]], Any]


def _str_field(key: str) -> _Field:
    return _Field(key, lambda v: v, lambda db, s: s[key])


def _int_field(key: str) -> _Field:
    return _Field(key, str, lambda db, s: get_positive_int_setting(db, key))


def _bool_field(key: str) -> _Field:
    return _Field(
        key,
        lambda v: "true" if v else "false",
        lambda db, s: s[key] == "true",
    )


# Per-section declarative field maps: schema attribute -> stored setting field.
# Single source driving both apply_section (write) and _build_response (read).
_GENERAL_FIELDS: dict[str, _Field] = {
    "base_domain": _str_field("base_domain"),
    "acme_email": _str_field("acme_email"),
    "reconcile_interval_seconds": _int_field("reconcile_interval_seconds"),
    "health_check_interval_seconds": _int_field("health_check_interval_seconds"),
    "cert_renewal_window_days": _int_field("cert_renewal_window_days"),
    "event_retention_days": _int_field("event_retention_days"),
    "timezone": _str_field("timezone"),
    "developer_mode": _bool_field("developer_mode"),
}
_CLOUDFLARE_FIELDS: dict[str, _Field] = {
    "zone_id": _str_field("cf_zone_id"),
}
_TAILSCALE_FIELDS: dict[str, _Field] = {
    "control_url": _str_field("ts_control_url"),
    "default_ts_hostname_prefix": _str_field("ts_default_hostname_prefix"),
}
_DOCKER_FIELDS: dict[str, _Field] = {
    "socket_path": _str_field("docker_socket_path"),
}
_PATH_FIELDS: dict[str, _Field] = {
    "generated_root": _str_field("generated_root"),
    "cert_root": _str_field("cert_root"),
    "tailscale_state_root": _str_field("tailscale_state_root"),
}


def apply_section(db: Session, body: BaseModel, fieldmap: dict[str, _Field]) -> None:
    """Persist each mapped field the request actually set.

    Iterates the field map in declaration order and, preserving the historical
    None-skip semantics exactly, calls ``set_setting`` only for attributes whose
    value is not None. Special-cases (base-domain guard, Tailscale key
    validation, post-commit secret writes) stay in the handlers as explicit
    hooks — this helper only owns the mechanical field copy.
    """
    for attr, field in fieldmap.items():
        value = getattr(body, attr)
        if value is not None:
            set_setting(db, field.key, field.serialize(value))


def _build_response(db: Session) -> AllSettingsResponse:
    s = get_all_settings(db)

    def read_section(fieldmap: dict[str, _Field]) -> dict[str, Any]:
        return {attr: field.read(db, s) for attr, field in fieldmap.items()}

    return AllSettingsResponse(
        general=GeneralSettingsResponse(**read_section(_GENERAL_FIELDS)),
        cloudflare=CloudflareSettingsResponse(
            token_configured=_secret_configured(CLOUDFLARE_TOKEN),
            **read_section(_CLOUDFLARE_FIELDS),
        ),
        tailscale=TailscaleSettingsResponse(
            auth_key_configured=_secret_configured(TAILSCALE_AUTH_KEY),
            api_key_configured=_secret_configured(TAILSCALE_API_KEY),
            **read_section(_TAILSCALE_FIELDS),
        ),
        docker=DockerSettingsResponse(**read_section(_DOCKER_FIELDS)),
        paths=PathSettingsResponse(**read_section(_PATH_FIELDS)),
        setup_complete=s["setup_complete"] == "true",
    )

def _reject_base_domain_change_with_services(db: Session, new_domain: str) -> None:
    # Compare case-insensitively: DNS names are case-insensitive, the incoming
    # value is already lowercased by GeneralSettingsUpdate.normalize_base_domain,
    # and a legacy deployment predating that validator can hold a mixed-case
    # stored value. Comparing raw would flag an unchanged domain as a change and
    # 409-lock the whole /general section on upgrade.
    if new_domain.lower() == get_setting(db, "base_domain").lower():
        return

    from app.models.service import Service

    if db.query(Service.id).first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot change base domain while services exist",
        )




# --- GET all settings ---


@router.get("", response_model=AllSettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    return _build_response(db)


# --- PUT section updates ---


@router.put("/general", response_model=AllSettingsResponse)
def update_general(body: GeneralSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        # Special-case hook: reject a base-domain change while services exist.
        # Runs before apply_section writes base_domain (same order as before).
        if body.base_domain is not None:
            _reject_base_domain_change_with_services(db, body.base_domain)
        apply_section(db, body, _GENERAL_FIELDS)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/cloudflare", response_model=AllSettingsResponse)
def update_cloudflare(body: CloudflareSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        apply_section(db, body, _CLOUDFLARE_FIELDS)
        commit_with_lock(db)
    # Special-case hook: persist the secret only after the DB write commits, so
    # a failed DB write leaves no orphaned secret on disk (a retry re-applies
    # both).
    if body.token is not None:
        write_secret(CLOUDFLARE_TOKEN, body.token.strip())
    return _build_response(db)


@router.put("/tailscale", response_model=AllSettingsResponse)
def update_tailscale(body: TailscaleSettingsUpdate, db: Session = Depends(get_db)):
    # Special-case hook: validate the Tailscale key prefixes before any write.
    auth_key = body.auth_key.strip() if body.auth_key is not None else None
    api_key = body.api_key.strip() if body.api_key is not None else None
    if auth_key is not None and not is_valid_ts_auth_key(auth_key):
        raise HTTPException(
            status_code=400,
            detail=f"Tailscale auth key must start with '{TS_AUTHKEY_PREFIX}'",
        )
    if api_key is not None and not is_valid_ts_api_key(api_key):
        raise HTTPException(
            status_code=400,
            detail=f"Tailscale API key must start with '{TS_APIKEY_PREFIX}'",
        )

    with db_write_section(db):
        apply_section(db, body, _TAILSCALE_FIELDS)
        commit_with_lock(db)
    # Special-case hook: persist secrets only after the DB write commits, so a
    # failed DB write leaves no orphaned secret on disk (a retry re-applies
    # both).
    if auth_key is not None:
        write_secret(TAILSCALE_AUTH_KEY, auth_key)
    if api_key is not None:
        write_secret(TAILSCALE_API_KEY, api_key)
    return _build_response(db)


@router.put("/docker", response_model=AllSettingsResponse)
def update_docker(body: DockerSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        apply_section(db, body, _DOCKER_FIELDS)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/paths", response_model=AllSettingsResponse)
def update_paths(body: PathSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        apply_section(db, body, _PATH_FIELDS)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/setup-complete", response_model=AllSettingsResponse)
def mark_setup_complete(db: Session = Depends(get_db)):
    missing = missing_setup_requirements(db)
    if missing:
        raise HTTPException(
            status_code=400,
            detail="Setup incomplete: configure " + ", ".join(missing) + " first",
        )
    with db_write_section(db):
        set_setting(db, "setup_complete", "true")
        commit_with_lock(db)
    return _build_response(db)


def _find_main_container(client: docker.DockerClient):
    containers = client.containers.list(all=True, filters={"label": "tailbale.main=true"})
    if containers:
        return containers[0]

    fallback_names = (
        "tailbale",
        "backend",
        "tailbale-tailbale-1",
        "tailbale-backend-1",
        os.environ.get("HOSTNAME"),
    )
    for name in fallback_names:
        if not name:
            continue
        try:
            return client.containers.get(name)
        except docker.errors.NotFound:
            continue

    raise HTTPException(status_code=404, detail="tailBale container not found")


@router.get("/developer/main-logs")
def get_main_container_logs(
    tail: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    _require_developer_mode(db)
    try:
        with docker_client(resolve_socket(db)) as client:
            container = _find_main_container(client)
            output = container.logs(stdout=True, stderr=True, tail=tail, timestamps=True)
            logs = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
            return {
                "container": getattr(container, "name", None) or getattr(container, "id", "unknown"),
                "logs": logs,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read tailBale logs: {exc}") from exc


@router.post("/developer/reset-setup-complete")
def reset_setup_complete(db: Session = Depends(get_db)):
    _require_developer_mode(db)
    with db_write_section(db):
        set_setting(db, "setup_complete", "false")
        commit_with_lock(db)
    return {"success": True, "message": "setup_complete reset"}


@router.post("/developer/reset-all")
def reset_all(db: Session = Depends(get_db)):
    _require_developer_mode(db)

    from app.locks import lifecycle_lock
    from app.models.event import Event
    from app.models.job import Job
    from app.models.service import Service
    from app.models.setting import Setting
    from app.models.user import User
    from app.services.service_ops import _delete_service_record

    with lifecycle_lock():
        service_ids = [service_id for (service_id,) in db.query(Service.id).all()]
        for service_id in service_ids:
            service = db.get(Service, service_id)
            if service is not None:
                _delete_service_record(db, service, cleanup_dns=True)

        with db_write_section(db):
            for job in db.query(Job).all():
                db.delete(job)
            for event in db.query(Event).all():
                db.delete(event)
            for user in db.query(User).all():
                db.delete(user)
            for setting in db.query(Setting).all():
                db.delete(setting)
            commit_with_lock(db)

        for secret_name in ALL_SECRETS:
            delete_secret(secret_name)

    return {"success": True, "message": "All setup state reset"}


# --- Connection tests ---


@router.post("/test/docker", response_model=ConnectionTestResult)
def test_docker(db: Session = Depends(get_db)):
    try:
        with docker_client(resolve_socket(db)) as client:
            client.ping()
            info = client.info()
            return ConnectionTestResult(
                success=True,
                message=f"Connected to Docker {info.get('ServerVersion', 'unknown')}",
            )
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))


@router.post("/test/cloudflare", response_model=ConnectionTestResult)
async def test_cloudflare(db: Session = Depends(get_db)):
    token = read_secret(CLOUDFLARE_TOKEN)
    if not token:
        return ConnectionTestResult(success=False, message="Cloudflare token not configured")

    zone_id = get_setting(db, "cf_zone_id")
    if not zone_id:
        return ConnectionTestResult(success=False, message="Cloudflare zone ID not configured")

    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.get(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            data = resp.json()
            if not isinstance(data, dict):
                return ConnectionTestResult(
                    success=False, message="Unexpected Cloudflare API response"
                )
            if data.get("success"):
                result = data.get("result") or {}
                zone_name = result.get("name") or "unknown"
                return ConnectionTestResult(
                    success=True, message=f"Connected to zone: {zone_name}"
                )
            errors = data.get("errors") or []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            msg = first.get("message") or "Unknown error"
            return ConnectionTestResult(success=False, message=msg)
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))


@router.post("/test/tailscale", response_model=ConnectionTestResult)
def test_tailscale():
    token = read_secret(TAILSCALE_AUTH_KEY)
    if not token:
        return ConnectionTestResult(success=False, message="Tailscale auth key not configured")

    # Basic format validation — full validation happens when creating an edge
    if is_valid_ts_auth_key(token):
        return ConnectionTestResult(
            success=True,
            message="Auth key format looks valid (full test on edge creation)",
        )
    return ConnectionTestResult(
        success=False,
        message=f"Auth key should start with '{TS_AUTHKEY_PREFIX}'",
    )
