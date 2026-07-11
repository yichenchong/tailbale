from collections.abc import Callable
from typing import Any, NamedTuple

# Preserve the historical ``app.routers.settings.docker`` monkeypatch target
# used by diagnostics tests/callers after AR-N22 moves those endpoints out.
import docker as docker
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import settings_store
from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.models.service import Service
from app.schemas.settings import (
    AllSettingsResponse,
    CloudflareSettingsResponse,
    CloudflareSettingsUpdate,
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
    TAILSCALE_API_KEY,
    TAILSCALE_AUTH_KEY,
    TS_APIKEY_PREFIX,
    TS_AUTHKEY_PREFIX,
    is_valid_ts_api_key,
    is_valid_ts_auth_key,
    read_secret,
    write_secret,
)
from app.setup_state import missing_setup_requirements

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


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
    return _Field(key, str, lambda db, s: settings_store.get_positive_int_setting(db, key))


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
            settings_store.set_setting(db, field.key, field.serialize(value))


def _build_response(db: Session) -> AllSettingsResponse:
    s = settings_store.get_all_settings(db)

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
    if new_domain.lower() == settings_store.get_setting(db, "base_domain").lower():
        return

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
        settings_store.set_setting(db, "setup_complete", "true")
        commit_with_lock(db)
    return _build_response(db)
