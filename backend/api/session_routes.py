from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import session_controller
from backend.dto.session_dto import (ApiKeyIn, CookiesIn, LoginIn, ProxyIn,
                                      TelegramLoginCode, TelegramLoginPassword,
                                      TelegramLoginStart)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{platform_id}")
async def get_session_status(platform_id: str) -> dict:
    return await session_controller.get_session_status(platform_id)


@router.post("/{platform_id}/cookies")
async def add_cookies(platform_id: str, body: CookiesIn) -> dict:
    return await session_controller.add_cookies(platform_id, body)


@router.post("/{platform_id}/api-key")
async def add_api_key(platform_id: str, body: ApiKeyIn) -> dict:
    return await session_controller.add_api_key(platform_id, body)


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


# Telegram's MTProto login is multi-step (code, then optionally a 2FA
# password) so it can't reuse the single-shot /{platform_id}/login route
# above -- see services/telegram_login_service.py.
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
