"""App profile definitions and API endpoint."""

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.schemas.services import ProfileDetectionResponse, ProfilesResponse

router = APIRouter(
    prefix="/api/profiles",
    tags=["profiles"],
    dependencies=[Depends(get_current_user)],
)

# Profile definitions for common self-hosted apps
APP_PROFILES: dict[str, dict] = {
    "generic": {
        "name": "Generic",
        "recommended_port": 80,
        "healthcheck_path": None,
        "preserve_host_header": True,
        "post_setup_reminder": None,
        "image_patterns": [],
    },
    "nextcloud": {
        "name": "Nextcloud",
        "recommended_port": 80,
        "healthcheck_path": "/status.php",
        "preserve_host_header": True,
        "post_setup_reminder": (
            "Add your hostname to Nextcloud's trusted_domains in config.php and set "
            "overwrite.cli.url to https://<your-hostname>."
        ),
        "image_patterns": ["nextcloud"],
    },
    "jellyfin": {
        "name": "Jellyfin",
        "recommended_port": 8096,
        "healthcheck_path": "/health",
        "preserve_host_header": True,
        "post_setup_reminder": (
            "In Jellyfin Dashboard > Networking, set the public HTTPS port to 443 "
            "and the base URL if using a subpath."
        ),
        "image_patterns": ["jellyfin"],
    },
    "immich": {
        "name": "Immich",
        "recommended_port": 3001,
        "healthcheck_path": "/api/server-info/ping",
        "preserve_host_header": True,
        "post_setup_reminder": (
            "Set IMMICH_SERVER_URL in your .env to https://<your-hostname> "
            "for correct URL generation."
        ),
        "image_patterns": ["immich"],
    },
    "calibre-web": {
        "name": "Calibre-Web",
        "recommended_port": 8083,
        "healthcheck_path": None,
        "preserve_host_header": True,
        "post_setup_reminder": (
            "In Calibre-Web admin settings, set the server external port to 443 "
            "and enable reverse proxy authentication if desired."
        ),
        "image_patterns": ["calibre-web", "calibreweb"],
    },
    "home-assistant": {
        "name": "Home Assistant",
        "recommended_port": 8123,
        "healthcheck_path": "/api/",
        "preserve_host_header": True,
        "post_setup_reminder": (
            "Add a trusted_proxies entry in your Home Assistant configuration.yaml "
            "for the edge container's Tailscale IP range (100.64.0.0/10)."
        ),
        "image_patterns": ["homeassistant", "home-assistant"],
    },
    "vaultwarden": {
        "name": "Vaultwarden",
        "recommended_port": 80,
        "healthcheck_path": "/alive",
        "preserve_host_header": True,
        "post_setup_reminder": (
            "Set the DOMAIN environment variable on the Vaultwarden container "
            "to https://<your-hostname> for correct URL generation."
        ),
        "image_patterns": ["vaultwarden"],
    },
}


def _repository_path(image_name: str) -> str:
    """Return the repository path without registry, tag, or digest."""
    image = image_name.strip().lower().split("@", 1)[0]
    if "/" in image:
        first, rest = image.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            image = rest
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        image = image[:last_colon]
    return image


def _repository_component_matches(component: str, pattern: str) -> bool:
    return (
        component == pattern
        or component.startswith(f"{pattern}-")
        or component.endswith(f"-{pattern}")
    )


def detect_profile(image_name: str) -> str | None:
    """Auto-detect an app profile from a Docker image name.

    Returns the profile key or None if no match.
    """
    repository = _repository_path(image_name)
    components = repository.split("/")
    for key, profile in APP_PROFILES.items():
        if key == "generic":
            continue
        for pattern in profile["image_patterns"]:
            if any(_repository_component_matches(component, pattern) for component in components):
                return key
    return None


@router.get("", response_model=ProfilesResponse)
def list_profiles() -> ProfilesResponse:
    """List all available app profiles."""
    return ProfilesResponse(profiles=APP_PROFILES)


@router.get("/detect", response_model=ProfileDetectionResponse)
def detect_profile_endpoint(image: str) -> ProfileDetectionResponse:
    """Auto-detect a profile from a Docker image name."""
    profile_key = detect_profile(image)
    return ProfileDetectionResponse(
        detected_profile=profile_key,
        profile=APP_PROFILES.get(profile_key) if profile_key else None,
    )
