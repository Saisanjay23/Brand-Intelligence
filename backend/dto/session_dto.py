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
