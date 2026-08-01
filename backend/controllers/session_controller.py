"""Session pool management -- pasting cookies, saving an API key, an
interactive login, proxy assignment, deletion. Operational surface, not
something the SaaS caller touches per-request; kept here because it's
still this engine's job to keep its own platform credentials alive.
"""

from __future__ import annotations

from backend.dto.session_dto import (ApiKeyIn, CookiesIn, LoginIn, ProxyIn,
                                      TelegramLoginCode, TelegramLoginPassword,
                                      TelegramLoginStart)
from backend.sessions import manager as sessions_engine
from backend.services import telegram_login_service


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


# ---------- Telegram's multi-step MTProto login ----------

async def telegram_login_start(body: TelegramLoginStart) -> dict:
    return await telegram_login_service.send_code(body.api_id, body.api_hash, body.phone)


async def telegram_login_code(body: TelegramLoginCode) -> dict:
    return await telegram_login_service.submit_code(body.code)


async def telegram_login_password(body: TelegramLoginPassword) -> dict:
    return await telegram_login_service.submit_password(body.password)


async def telegram_login_cancel() -> dict:
    await telegram_login_service.cancel()
    return {"status": "cancelled"}
