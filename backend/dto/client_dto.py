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
    # optional per-keyword "Digital Risk Keyword" display-name override --
    # keyed by the literal keyword string (from name_keywords/domain_keywords
    # above), value is what to show as the published incident's assetName
    # for a profile matched under that keyword. A keyword absent here (the
    # default) keeps today's behavior unchanged -- see
    # services/incident_publisher.py::_category_and_asset_name.
    name_keyword_drk: dict[str, str] = {}
    domain_keyword_drk: dict[str, str] = {}
    # platform id -> max results to scrape for THIS client's sweeps. A
    # platform missing here (or mapped to 0) is uncapped -- "scrape all".
    platform_limits: dict[str, int] = {}
    # platform id -> {tab: max results}, for platforms with more than one
    # discovery tab (currently only Facebook: people vs pages -- see
    # PLATFORM_TABS in services/discovery_service.py). A tab missing here
    # falls back to platform_limits[platform_id], then uncapped.
    platform_tab_limits: dict[str, dict[str, int]] = {}
    cron: Optional[str] = None
