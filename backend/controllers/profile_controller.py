"""Thin HTTP-facing layer over `services/profile_service.py` -- parses
query/path params and the request body, calls the service, returns its
result. No business logic lives here; see the service for the two response
shapes (card vs full) and the approve -> auto-analysis side effect.
"""

from __future__ import annotations

from typing import Optional

from backend.dto.profile_dto import ProfilePatch
from backend.services import profile_service


async def list_profiles(
    client_id: str, status: Optional[str] = None, phase: Optional[str] = None,
    platform: Optional[str] = None, limit: int = 100, offset: int = 0,
    include_held: bool = False, keyword: Optional[str] = None,
) -> dict:
    return await profile_service.list_profiles(
        client_id, status=status, phase=phase, platform=platform, limit=limit, offset=offset,
        include_held=include_held, keyword=keyword,
    )


async def get_profile(profile_id: str) -> dict:
    return await profile_service.get_profile(profile_id)


async def patch_profile(profile_id: str, body: ProfilePatch) -> dict:
    return await profile_service.patch_profile(profile_id, body.model_dump())


async def publish_profile(profile_id: str) -> dict:
    return await profile_service.publish_profile(profile_id)
