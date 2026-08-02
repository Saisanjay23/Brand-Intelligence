"""Request/response shapes for the clients resource."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ClientIn(BaseModel):
    client_id: str  # the org id
    name: str  # the org/client display name
    domain: str = ""  # e.g. "xyz.com" -- identifying metadata, not itself a search term
    name_keywords: list[str] = []  # individual people to protect
    domain_keywords: list[str] = []  # brand/domain keyword variants
    # platform id -> max results to scrape for THIS client's sweeps. A
    # platform missing here (or mapped to 0) is uncapped -- "scrape all".
    platform_limits: dict[str, int] = {}
    cron: Optional[str] = None
