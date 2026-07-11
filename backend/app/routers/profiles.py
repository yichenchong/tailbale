"""Profiles API endpoints."""

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.profiles import APP_PROFILES, detect_profile
from app.schemas.services import ProfileDetectionResponse, ProfilesResponse

router = APIRouter(
    prefix="/api/profiles",
    tags=["profiles"],
    dependencies=[Depends(get_current_user)],
)

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
