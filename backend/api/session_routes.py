from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import session_controller
from backend.dto.session_dto import (ApiKeyIn, CookiesIn, CredentialsIn, LoginIn, ProxyIn,
                                      SessionUpdateIn, TelegramLoginCode,
                                      TelegramLoginPassword, TelegramLoginStart)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# GET /sessions (no platform segment), every pool in one call, so the
# frontend can keep the whole view live on one polled request instead of one
# per platform. Bare "" rather than "/" keeps it off a trailing-slash redirect.
@router.get("")
async def get_all_session_status() -> dict:
    return await session_controller.get_all_session_status()


@router.get("/{platform_id}")
async def get_session_status(platform_id: str) -> dict:
    return await session_controller.get_session_status(platform_id)


@router.post("/{platform_id}/cookies")
async def add_cookies(platform_id: str, body: CookiesIn) -> dict:
    return await session_controller.add_cookies(platform_id, body)


@router.post("/{platform_id}/credentials")
async def add_credentials(platform_id: str, body: CredentialsIn) -> dict:
    return await session_controller.add_credentials(platform_id, body)


@router.post("/{platform_id}/api-key")
async def add_api_key(platform_id: str, body: ApiKeyIn) -> dict:
    return await session_controller.add_api_key(platform_id, body)


@router.put("/{platform_id}/{session_id}")
async def update_session(platform_id: str, session_id: str, body: SessionUpdateIn) -> dict:
    return await session_controller.update_session(platform_id, session_id, body)


@router.post("/{platform_id}/login")
async def login(platform_id: str, body: LoginIn) -> dict:
    return await session_controller.login(platform_id, body)


@router.put("/{platform_id}/{session_id}/proxy")
async def set_proxy(platform_id: str, session_id: str, body: ProxyIn) -> dict:
    return await session_controller.set_proxy(platform_id, session_id, body)


@router.delete("/{platform_id}/{session_id}")
async def delete_session(platform_id: str, session_id: str) -> dict:
    return await session_controller.delete_session(platform_id, session_id)


@router.delete("/{platform_id}")
async def delete_pool(platform_id: str) -> dict:
    return await session_controller.delete_pool(platform_id)


@router.post("/{platform_id}/check")
async def check_session(platform_id: str) -> dict:
    return await session_controller.check_session(platform_id)


# One named account, on demand, what someone who has just re-pasted
# cookies needs, as opposed to the sweep above which picks whichever
# session is most overdue for a check.
@router.post("/{platform_id}/{session_id}/check")
async def check_session_item(platform_id: str, session_id: str) -> dict:
    return await session_controller.check_session_item(platform_id, session_id)


# Telegram's MTProto login is multi-step (code, then optionally a 2FA
# password) so it can't reuse the single-shot /{platform_id}/login route
# above, see services/telegram_login_service.py.
@router.post("/telegram/login/start")
async def telegram_login_start(body: TelegramLoginStart) -> dict:
    return await session_controller.telegram_login_start(body)


@router.post("/telegram/login/code")
async def telegram_login_code(body: TelegramLoginCode) -> dict:
    return await session_controller.telegram_login_code(body)


@router.post("/telegram/login/password")
async def telegram_login_password(body: TelegramLoginPassword) -> dict:
    return await session_controller.telegram_login_password(body)


@router.post("/telegram/login/cancel")
async def telegram_login_cancel() -> dict:
    return await session_controller.telegram_login_cancel()
