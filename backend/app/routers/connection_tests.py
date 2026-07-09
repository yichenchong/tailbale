from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.adapters.cloudflare_adapter import CloudflareAPIError, verify_zone
from app.auth import get_current_user
from app.database import get_db
from app.schemas.settings import ConnectionTestResult
from app.secrets import (
    TAILSCALE_AUTH_KEY,
    TS_AUTHKEY_PREFIX,
    cloudflare_credentials,
    is_valid_ts_auth_key,
    read_secret,
)
from app.services import docker_client, resolve_socket

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


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
def test_cloudflare(db: Session = Depends(get_db)):
    token, zone_id = cloudflare_credentials(db)
    if not token:
        return ConnectionTestResult(success=False, message="Cloudflare token not configured")
    if not zone_id:
        return ConnectionTestResult(success=False, message="Cloudflare zone ID not configured")

    # Sync endpoint: FastAPI runs it in a threadpool, so the blocking adapter call
    # (verify_zone -> httpx2.get) never stalls the event loop while preserving the
    # ~10s cap and the exact ConnectionTestResult messages the settings API asserts.
    try:
        zone_name = verify_zone(token, zone_id, timeout=10)
        return ConnectionTestResult(success=True, message=f"Connected to zone: {zone_name}")
    except CloudflareAPIError as e:
        errors = e.errors or []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        msg = first.get("message") or "Unexpected Cloudflare API response"
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
