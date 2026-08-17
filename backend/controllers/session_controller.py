"""Session pool management, pasting cookies, saving an API key, an
interactive login, proxy assignment, deletion. Operational surface, not
something the SaaS caller touches per-request; kept here because it's
still this engine's job to keep its own platform credentials alive.
"""

from __future__ import annotations

from backend.dto.session_dto import (ApiKeyIn, CookiesIn, CredentialsIn, LoginIn, ProxyIn,
                                      SessionUpdateIn, TelegramLoginCode,
                                      TelegramLoginPassword, TelegramLoginStart)
from backend.sessions import manager as sessions_engine
from backend.services import telegram_login_service


async def get_session_status(platform_id: str) -> dict:
    live_health = await sessions_engine.cached_health()
    return await sessions_engine.status(platform_id, live_health)


async def get_all_session_status() -> dict:
    """Every platform's pool in ONE call.

    The frontend keeps this view live by polling, and fanning that out to
    one request per platform meant six round trips (plus six reads of the
    same health cache) every tick. One call also removes a real class of
    inconsistency: the platforms are read against a single snapshot of the
    health cache, so they can't disagree with each other about a sweep that
    landed midway through the fan-out.

    A platform that fails to report doesn't take the others down with it
    it's simply absent, the same tolerance the per-platform fan-out had.
    """
    import asyncio

    from backend.platforms import registry
    from backend.shared.logging import get_logger

    live_health = await sessions_engine.cached_health()
    platform_ids = list(registry.PLATFORMS)
    results = await asyncio.gather(
        *(sessions_engine.status(pid, live_health) for pid in platform_ids),
        return_exceptions=True,
    )
    items = []
    for platform_id, result in zip(platform_ids, results):
        if isinstance(result, BaseException):
            get_logger("controllers.session").warning(
                f"{platform_id}: session status unavailable -- {type(result).__name__}: {result}"
            )
            continue
        items.append(result)
    return {"items": items}


async def add_cookies(platform_id: str, body: CookiesIn) -> dict:
    return await sessions_engine.save_cookies(platform_id, body.blob, body.identifier)


async def add_credentials(platform_id: str, body: CredentialsIn) -> dict:
    return await sessions_engine.save_credentials(
        platform_id, body.identifier, body.username, body.password, body.two_factor_secret, body.proxy
    )


async def add_api_key(platform_id: str, body: ApiKeyIn) -> dict:
    return await sessions_engine.save_api_key(platform_id, body.key, body.identifier)


async def update_session(platform_id: str, session_id: str, body: SessionUpdateIn) -> dict:
    return await sessions_engine.update_session_credentials(platform_id, session_id, body.blob, body.api_key, body.identifier)


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


async def check_session_item(platform_id: str, session_id: str) -> dict:
    """Verify one specific account and hand back the refreshed pool with
    the answer, one round trip, so the caller doesn't have to re-fetch to
    see the row it just changed."""
    result = await sessions_engine.check_item(platform_id, session_id)
    return {**result, "session": await sessions_engine.status(platform_id, await sessions_engine.cached_health())}


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
