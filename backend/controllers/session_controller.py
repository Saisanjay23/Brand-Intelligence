"""Session pool management -- pasting cookies, saving an API key, an
interactive login, proxy assignment, deletion. Operational surface, not
something the SaaS caller touches per-request; kept here because it's
still this engine's job to keep its own platform credentials alive.
"""

from __future__ import annotations

from typing import Optional

from backend.dto.session_dto import ApiKeyIn, CookiesIn, LoginIn, ProxyIn
from backend.services import session_service as sessions_engine


async def get_session_status(platform_id: str) -> dict:
    live_health = await sessions_engine.cached_health()
    return await sessions_engine.status(platform_id, live_health)


async def add_cookies(platform_id: str, body: CookiesIn) -> dict:
    return await sessions_engine.save_cookies(platform_id, body.blob, body.identifier)


async def add_api_key(platform_id: str, body: ApiKeyIn) -> dict:
    return await sessions_engine.save_api_key(platform_id, body.key)


async def login(platform_id: str, body: LoginIn) -> dict:
    return await sessions_engine.launch_login(platform_id, body.timeout_s, body.identifier)


async def set_proxy(platform_id: str, session_id: str, body: ProxyIn) -> dict:
    return await sessions_engine.set_proxy(platform_id, session_id, body.proxy)


async def delete_session(platform_id: str, session_id: str) -> dict:
    return await sessions_engine.delete(platform_id, session_id)


async def delete_pool(platform_id: str) -> dict:
    return await sessions_engine.delete(platform_id)


async def check_session(platform_id: str) -> dict:
    ok, detail = await sessions_engine.check_one(platform_id)
    return {"ok": ok, "detail": detail}
