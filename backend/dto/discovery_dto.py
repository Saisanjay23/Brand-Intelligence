"""Request shape for POST /discovery."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DiscoveryIn(BaseModel):
    client_id: str  # must already exist -- see POST /clients
    keywords: list[str]
    tabs: list[str] = ["people", "pages"]
    max_results: int = 0
    max_seconds: Optional[float] = None
    concurrency: Optional[int] = None
    callback_url: str = ""
