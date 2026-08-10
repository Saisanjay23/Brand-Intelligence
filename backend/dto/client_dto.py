"""Request/response shapes for the clients resource."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ClientIn(BaseModel):
    client_id: str  # the org id
    name: str  # the org/client display name
    domain: str = ""  # e.g. "xyz.com" -- identifying metadata, not itself a search term
    # optional URL of the brand's own real logo -- shown side-by-side with a
    # discovered profile's avatar during triage, so "does this look like an
    # impersonation" doesn't require opening a separate tab to find the real
    # logo to compare against. Purely a display aid, not scored/matched
    # automatically -- no image-similarity model exists here.
    logo_url: str = ""
    name_keywords: list[str] = []  # individual people to protect
    domain_keywords: list[str] = []  # brand/domain keyword variants
    asset_name_individual_keywords: list[str] = []  # asset name choices for individuals
    asset_name_domain_keywords: list[str] = []  # asset name choices for domains
    # platform id -> max results to scrape for THIS client's sweeps. A
    # platform missing here (or mapped to 0) is uncapped -- "scrape all".
    platform_limits: dict[str, int] = {}
    # platform id -> {tab: max results}, for platforms with more than one
    # discovery tab (currently only Facebook: people vs pages -- see
    # PLATFORM_TABS in services/discovery_service.py). A tab missing here
    # falls back to platform_limits[platform_id], then uncapped.
    platform_tab_limits: dict[str, dict[str, int]] = {}
    # platform id -> this brand's OWN official handle on that platform, e.g.
    # {"twitter": "adanionline"}. Discovery scores every discovered profile's
    # handle against it (see shared/text.py::handle_score), which is the only
    # automated username signal in the system -- name_score compares display
    # names, so a username squat like "@adani_care_official" was previously
    # invisible to everything except an analyst's own eyes. A platform absent
    # here simply produces no username score for that platform; it never
    # produces a misleading zero-as-evidence.
    official_handles: dict[str, str] = {}
    cron: Optional[str] = None
