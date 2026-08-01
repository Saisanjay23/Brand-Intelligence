"""Request shapes for the sessions resource."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CookiesIn(BaseModel):
    blob: str
    identifier: str = ""


class ApiKeyIn(BaseModel):
    key: str


class LoginIn(BaseModel):
    timeout_s: int = 300
    identifier: str = ""


class ProxyIn(BaseModel):
    proxy: Optional[dict] = None


# Telegram's MTProto login is inherently multi-step (code, then optionally a
# 2FA password) and cannot reuse LoginIn's single-shot headful-browser shape
# -- see services/telegram_login_service.py.
class TelegramLoginStart(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class TelegramLoginCode(BaseModel):
    code: str


class TelegramLoginPassword(BaseModel):
    password: str
