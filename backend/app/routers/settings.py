import contextlib
import os

import docker
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
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
    delete_secret,
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


def _is_valid_tailscale_auth_key(value: str) -> bool:
    return value.startswith("tskey-auth-") or value.startswith("tskey-reusable-")


def _is_valid_tailscale_api_key(value: str) -> bool:
    return value.startswith("tskey-api-")


def _require_developer_mode(db: Session) -> None:
    if get_setting(db, "developer_mode") != "true":
        raise HTTPException(status_code=403, detail="Developer Mode must be enabled first")


def _secret_configured(name: str) -> bool:
    return bool(read_secret(name))


def _build_response(db: Session) -> AllSettingsResponse:
    s = get_all_settings(db)
    return AllSettingsResponse(
        general=GeneralSettingsResponse(
            base_domain=s["base_domain"],
            acme_email=s["acme_email"],
            reconcile_interval_seconds=get_positive_int_setting(db, "reconcile_interval_seconds"),
            cert_renewal_window_days=get_positive_int_setting(db, "cert_renewal_window_days"),
            timezone=s["timezone"],
            developer_mode=s["developer_mode"] == "true",
        ),
        cloudflare=CloudflareSettingsResponse(
            zone_id=s["cf_zone_id"],
            token_configured=_secret_configured(CLOUDFLARE_TOKEN),
        ),
        tailscale=TailscaleSettingsResponse(
            auth_key_configured=_secret_configured(TAILSCALE_AUTH_KEY),
            api_key_configured=_secret_configured(TAILSCALE_API_KEY),
            control_url=s["ts_control_url"],
            default_ts_hostname_prefix=s["ts_default_hostname_prefix"],
        ),
        docker=DockerSettingsResponse(
            socket_path=s["docker_socket_path"],
        ),
        paths=PathSettingsResponse(
            generated_root=s["generated_root"],
            cert_root=s["cert_root"],
            tailscale_state_root=s["tailscale_state_root"],
        ),
        setup_complete=s["setup_complete"] == "true",
    )


# --- GET all settings ---


@router.get("", response_model=AllSettingsResponse)
async def get_settings(db: Session = Depends(get_db)):
    return _build_response(db)


# --- PUT section updates ---


@router.put("/general", response_model=AllSettingsResponse)
async def update_general(body: GeneralSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        if body.base_domain is not None:
            set_setting(db, "base_domain", body.base_domain)
        if body.acme_email is not None:
            set_setting(db, "acme_email", body.acme_email)
        if body.reconcile_interval_seconds is not None:
            set_setting(db, "reconcile_interval_seconds", str(body.reconcile_interval_seconds))
        if body.cert_renewal_window_days is not None:
            set_setting(db, "cert_renewal_window_days", str(body.cert_renewal_window_days))
        if body.timezone is not None:
            set_setting(db, "timezone", body.timezone)
        if body.developer_mode is not None:
            set_setting(db, "developer_mode", "true" if body.developer_mode else "false")
        commit_with_lock(db)
    return _build_response(db)


@router.put("/cloudflare", response_model=AllSettingsResponse)
async def update_cloudflare(body: CloudflareSettingsUpdate, db: Session = Depends(get_db)):
    if body.token is not None:
        token = body.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="Cloudflare token must not be empty")
        write_secret(CLOUDFLARE_TOKEN, token)
    with db_write_section(db):
        if body.zone_id is not None:
            set_setting(db, "cf_zone_id", body.zone_id)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/tailscale", response_model=AllSettingsResponse)
async def update_tailscale(body: TailscaleSettingsUpdate, db: Session = Depends(get_db)):
    auth_key = body.auth_key.strip() if body.auth_key is not None else None
    api_key = body.api_key.strip() if body.api_key is not None else None
    if auth_key is not None and not _is_valid_tailscale_auth_key(auth_key):
        raise HTTPException(
            status_code=400,
            detail="Tailscale auth key must start with 'tskey-auth-' or 'tskey-reusable-'",
        )
    if api_key is not None and not _is_valid_tailscale_api_key(api_key):
        raise HTTPException(
            status_code=400,
            detail="Tailscale API key must start with 'tskey-api-'",
        )

    if auth_key is not None:
        write_secret(TAILSCALE_AUTH_KEY, auth_key)
    if api_key is not None:
        write_secret(TAILSCALE_API_KEY, api_key)
    with db_write_section(db):
        if body.control_url is not None:
            set_setting(db, "ts_control_url", body.control_url)
        if body.default_ts_hostname_prefix is not None:
            set_setting(db, "ts_default_hostname_prefix", body.default_ts_hostname_prefix)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/docker", response_model=AllSettingsResponse)
async def update_docker(body: DockerSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        if body.socket_path is not None:
            set_setting(db, "docker_socket_path", body.socket_path)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/paths", response_model=AllSettingsResponse)
async def update_paths(body: PathSettingsUpdate, db: Session = Depends(get_db)):
    with db_write_section(db):
        if body.generated_root is not None:
            set_setting(db, "generated_root", body.generated_root)
        if body.cert_root is not None:
            set_setting(db, "cert_root", body.cert_root)
        if body.tailscale_state_root is not None:
            set_setting(db, "tailscale_state_root", body.tailscale_state_root)
        commit_with_lock(db)
    return _build_response(db)


@router.put("/setup-complete", response_model=AllSettingsResponse)
async def mark_setup_complete(db: Session = Depends(get_db)):
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
async def get_main_container_logs(
    tail: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    _require_developer_mode(db)
    socket_path = get_setting(db, "docker_socket_path")
    client = None
    try:
        client = docker.DockerClient(base_url=socket_path)
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
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()


@router.post("/developer/reset-setup-complete")
async def reset_setup_complete(db: Session = Depends(get_db)):
    _require_developer_mode(db)
    with db_write_section(db):
        set_setting(db, "setup_complete", "false")
        commit_with_lock(db)
    return {"success": True, "message": "setup_complete reset"}


@router.post("/developer/reset-all")
async def reset_all(db: Session = Depends(get_db)):
    _require_developer_mode(db)

    from app.models.event import Event
    from app.models.job import Job
    from app.models.service import Service
    from app.models.setting import Setting
    from app.models.user import User
    from app.routers.services import _SERVICE_LIFECYCLE_MUTEX, _delete_service_record

    with _SERVICE_LIFECYCLE_MUTEX:
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
async def test_docker(db: Session = Depends(get_db)):
    socket_path = get_setting(db, "docker_socket_path")
    client = None
    try:
        client = docker.DockerClient(base_url=socket_path)
        client.ping()
        info = client.info()
        return ConnectionTestResult(
            success=True,
            message=f"Connected to Docker {info.get('ServerVersion', 'unknown')}",
        )
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()


@router.post("/test/cloudflare", response_model=ConnectionTestResult)
async def test_cloudflare(db: Session = Depends(get_db)):
    token = read_secret(CLOUDFLARE_TOKEN)
    if not token:
        return ConnectionTestResult(success=False, message="Cloudflare token not configured")

    zone_id = get_setting(db, "cf_zone_id")
    if not zone_id:
        return ConnectionTestResult(success=False, message="Cloudflare zone ID not configured")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            data = resp.json()
            if data.get("success"):
                zone_name = data["result"]["name"]
                return ConnectionTestResult(
                    success=True, message=f"Connected to zone: {zone_name}"
                )
            errors = data.get("errors", [])
            msg = errors[0]["message"] if errors else "Unknown error"
            return ConnectionTestResult(success=False, message=msg)
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))


@router.post("/test/tailscale", response_model=ConnectionTestResult)
async def test_tailscale():
    token = read_secret(TAILSCALE_AUTH_KEY)
    if not token:
        return ConnectionTestResult(success=False, message="Tailscale auth key not configured")

    # Basic format validation — full validation happens when creating an edge
    if token.startswith("tskey-auth-") or token.startswith("tskey-reusable-"):
        return ConnectionTestResult(
            success=True,
            message="Auth key format looks valid (full test on edge creation)",
        )
    return ConnectionTestResult(
        success=False,
        message="Auth key should start with 'tskey-auth-' or 'tskey-reusable-'",
    )
