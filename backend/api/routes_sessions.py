"""Session pool management -- pasting cookies, saving an API key, an
interactive login, proxy assignment, deletion. Operational surface, not
something the SaaS caller touches per-request; kept here because it's
still this engine's job to keep its own platform credentials alive.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.engine import sessions as sessions_engine

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{platform_id}")
async def get_session_status(platform_id: str) -> dict:
    live_health = await sessions_engine.cached_health()
    return await sessions_engine.status(platform_id, live_health)


class CookiesIn(BaseModel):
    blob: str
    identifier: str = ""


@router.post("/{platform_id}/cookies")
async def add_cookies(platform_id: str, body: CookiesIn) -> dict:
    return await sessions_engine.save_cookies(platform_id, body.blob, body.identifier)


class ApiKeyIn(BaseModel):
    key: str


@router.post("/{platform_id}/api-key")
async def add_api_key(platform_id: str, body: ApiKeyIn) -> dict:
    return await sessions_engine.save_api_key(platform_id, body.key)


class LoginIn(BaseModel):
    timeout_s: int = 300
    identifier: str = ""


@router.post("/{platform_id}/login")
async def login(platform_id: str, body: LoginIn) -> dict:
    return await sessions_engine.launch_login(platform_id, body.timeout_s, body.identifier)


class ProxyIn(BaseModel):
    proxy: Optional[dict] = None


@router.put("/{platform_id}/{session_id}/proxy")
async def set_proxy(platform_id: str, session_id: str, body: ProxyIn) -> dict:
    return await sessions_engine.set_proxy(platform_id, session_id, body.proxy)


@router.delete("/{platform_id}/{session_id}")
async def delete_session(platform_id: str, session_id: str) -> dict:
    return await sessions_engine.delete(platform_id, session_id)


@router.delete("/{platform_id}")
async def delete_pool(platform_id: str) -> dict:
    return await sessions_engine.delete(platform_id)


@router.post("/{platform_id}/check")
async def check_session(platform_id: str) -> dict:
    ok, detail = await sessions_engine.check_one(platform_id)
    return {"ok": ok, "detail": detail}
