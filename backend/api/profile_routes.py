from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from backend.controllers import profile_controller
from backend.dto.profile_dto import ProfilePatch

router = APIRouter(tags=["profiles"])


@router.get("/profiles")
async def list_profiles(
    client_id: str, status: Optional[str] = None, phase: Optional[str] = None,
    platform: Optional[str] = None, limit: int = 100, offset: int = 0,
) -> dict:
    return await profile_controller.list_profiles(
        client_id, status=status, phase=phase, platform=platform, limit=limit, offset=offset,
    )


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str) -> dict:
    return await profile_controller.get_profile(profile_id)


@router.patch("/profiles/{profile_id}")
async def patch_profile(profile_id: str, body: ProfilePatch) -> dict:
    return await profile_controller.patch_profile(profile_id, body)
