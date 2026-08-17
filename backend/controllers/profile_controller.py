"""Thin HTTP-facing layer over `services/profile_service.py`, parses
query/path params and the request body, calls the service, returns its
result. No business logic lives here; see the service for the two response
shapes (card vs full) and the approve -> auto-analysis side effect.
"""

from __future__ import annotations

from typing import Optional

from backend.dto.profile_dto import (BulkDeleteRequest, BulkPatchRequest, DeletePlatformDataRequest,
                                      ManualUrlsRequest, ProfilePatch, PublishAllRequest)
from backend.services import profile_service


async def list_profiles(
    client_id: str, status: Optional[str] = None, phase: Optional[str] = None,
    platform: Optional[str] = None, limit: int = 100, offset: int = 0,
    include_held: bool = False, keyword: Optional[str] = None,
    entity_type: Optional[str] = None, priority: Optional[str] = None,
    match_level: Optional[str] = None, keyword_match_type: Optional[str] = None,
    search: Optional[str] = None, published: Optional[bool] = None,
) -> dict:
    return await profile_service.list_profiles(
        client_id, status=status, phase=phase, platform=platform, limit=limit, offset=offset,
        include_held=include_held, keyword=keyword, entity_type=entity_type,
        priority=priority, match_level=match_level, keyword_match_type=keyword_match_type,
        search=search, published=published,
    )


async def coverage(client_id: str, platform: Optional[str] = None) -> dict:
    return await profile_service.coverage(client_id, platform)


async def screenshot(profile_id: str) -> tuple[bytes, str]:
    return await profile_service.screenshot_path(profile_id)


async def cleanup_stale_pending(days: int) -> dict:
    return await profile_service.cleanup_stale_pending(days)


async def archive_stale_rejected(days: int) -> dict:
    return await profile_service.archive_stale_rejected(days)


async def get_profile(profile_id: str) -> dict:
    return await profile_service.get_profile(profile_id)


async def patch_profile(profile_id: str, body: ProfilePatch) -> dict:
    return await profile_service.patch_profile(profile_id, body.model_dump())


async def add_manual_urls(body: ManualUrlsRequest) -> dict:
    return await profile_service.add_manual_urls(
        body.client_id, body.urls, body.individual_urls, body.domain_urls,
    )


async def bulk_patch_profiles(body: BulkPatchRequest) -> dict:
    return await profile_service.bulk_patch_profiles(
        body.profile_ids, body.status, logo_match=body.logo_match, username_match=body.username_match,
    )


async def delete_profiles(body: BulkDeleteRequest) -> dict:
    return await profile_service.delete_profiles(body.profile_ids)


async def delete_platform_data(body: DeletePlatformDataRequest) -> dict:
    return await profile_service.delete_for_client_platform(body.client_id, body.platform)


async def publish_profile(profile_id: str) -> dict:
    return await profile_service.publish_profile(profile_id)


async def publish_all_profiles(body: PublishAllRequest) -> dict:
    return await profile_service.publish_all_profiles(body.client_id, body.platform, body.mode)
