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
    include_held: bool = False,
) -> dict:
    return await profile_controller.list_profiles(
        client_id, status=status, phase=phase, platform=platform, limit=limit, offset=offset,
        include_held=include_held,
    )


@router.get("/profiles/media-proxy")
async def proxy_image(url: str):
    import asyncio
    import urllib.request
    from fastapi.responses import Response
    from fastapi import HTTPException

    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid url")
    
    def fetch():
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.instagram.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read(), resp.headers.get("Content-Type", "image/jpeg")
        except Exception:
            return None, None

    data, content_type = await asyncio.to_thread(fetch)
    if not data:
        raise HTTPException(status_code=404, detail="Image could not be fetched")
    return Response(content=data, media_type=content_type)


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str) -> dict:
    return await profile_controller.get_profile(profile_id)


@router.patch("/profiles/{profile_id}")
async def patch_profile(profile_id: str, body: ProfilePatch) -> dict:
    return await profile_controller.patch_profile(profile_id, body)


@router.post("/profiles/{profile_id}/publish")
async def publish_profile(profile_id: str) -> dict:
    return await profile_controller.publish_profile(profile_id)
