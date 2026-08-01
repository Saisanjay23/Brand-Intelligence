"""Request/response shapes for the clients resource."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ClientIn(BaseModel):
    client_id: str
    name: str
    keywords: list[str] = []
    cron: Optional[str] = None
