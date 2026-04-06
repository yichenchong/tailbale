import docker
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
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
    CLOUDFLARE_TOKEN,
    TAILSCALE_AUTH_KEY,
    read_secret,
    secret_exists,
    write_secret,
)
from app.settings_store import get_all_settings, get_setting, set_setting

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


def _build_response(db: Session) -> AllSettingsResponse:
    s = get_all_settings(db)
    return AllSettingsResponse(
        general=GeneralSettingsResponse(
            base_domain=s["base_domain"],
            acme_email=s["acme_email"],
            reconcile_interval_seconds=int(s["reconcile_interval_seconds"]),
            cert_renewal_window_days=int(s["cert_renewal_window_days"]),
        ),
        cloudflare=CloudflareSettingsResponse(
            zone_id=s["cf_zone_id"],
            token_configured=secret_exists(CLOUDFLARE_TOKEN),
        ),
        tailscale=TailscaleSettingsResponse(
            auth_key_configured=secret_exists(TAILSCALE_AUTH_KEY),
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
    if body.base_domain is not None:
        set_setting(db, "base_domain", body.base_domain)
    if body.acme_email is not None:
        set_setting(db, "acme_email", body.acme_email)
    if body.reconcile_interval_seconds is not None:
        set_setting(db, "reconcile_interval_seconds", str(body.reconcile_interval_seconds))
    if body.cert_renewal_window_days is not None:
        set_setting(db, "cert_renewal_window_days", str(body.cert_renewal_window_days))
    db.commit()
    return _build_response(db)


@router.put("/cloudflare", response_model=AllSettingsResponse)
async def update_cloudflare(body: CloudflareSettingsUpdate, db: Session = Depends(get_db)):
    if body.zone_id is not None:
        set_setting(db, "cf_zone_id", body.zone_id)
    if body.token is not None:
        write_secret(CLOUDFLARE_TOKEN, body.token)
    db.commit()
    return _build_response(db)


@router.put("/tailscale", response_model=AllSettingsResponse)
async def update_tailscale(body: TailscaleSettingsUpdate, db: Session = Depends(get_db)):
    if body.auth_key is not None:
        write_secret(TAILSCALE_AUTH_KEY, body.auth_key)
    if body.control_url is not None:
        set_setting(db, "ts_control_url", body.control_url)
    if body.default_ts_hostname_prefix is not None:
        set_setting(db, "ts_default_hostname_prefix", body.default_ts_hostname_prefix)
    db.commit()
    return _build_response(db)


@router.put("/docker", response_model=AllSettingsResponse)
async def update_docker(body: DockerSettingsUpdate, db: Session = Depends(get_db)):
    if body.socket_path is not None:
        set_setting(db, "docker_socket_path", body.socket_path)
    db.commit()
    return _build_response(db)


@router.put("/paths", response_model=AllSettingsResponse)
async def update_paths(body: PathSettingsUpdate, db: Session = Depends(get_db)):
    if body.generated_root is not None:
        set_setting(db, "generated_root", body.generated_root)
    if body.cert_root is not None:
        set_setting(db, "cert_root", body.cert_root)
    if body.tailscale_state_root is not None:
        set_setting(db, "tailscale_state_root", body.tailscale_state_root)
    db.commit()
    return _build_response(db)


@router.put("/setup-complete", response_model=AllSettingsResponse)
async def mark_setup_complete(db: Session = Depends(get_db)):
    set_setting(db, "setup_complete", "true")
    db.commit()
    return _build_response(db)


# --- Connection tests ---


@router.post("/test/docker", response_model=ConnectionTestResult)
async def test_docker(db: Session = Depends(get_db)):
    socket_path = get_setting(db, "docker_socket_path")
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
