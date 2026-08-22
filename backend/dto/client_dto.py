"""Request/response shapes for the clients resource."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ReorderIn(BaseModel):
    """The full desired client order, front to back, see
    client_service.reorder / client_repository.reorder. Drives both the
    Scheduler tab's own listing and the round-robin engine's rotation
    sequence, both of which sort by the same persisted `order` field."""
    client_ids: list[str]


class ClientIn(BaseModel):
    client_id: str  # the org id
    name: str  # the org/client display name
    domain: str = ""  # e.g. "xyz.com", identifying metadata, not itself a search term
    # optional URL of the brand's own real logo, shown side-by-side with a
    # discovered profile's avatar during triage, so "does this look like an
    # impersonation" doesn't require opening a separate tab to find the real
    # logo to compare against. Purely a display aid, not scored/matched
    # automatically, no image-similarity model exists here.

    # PARENT keywords: the real names being protected. These are the match
    # targets and the buckets results are filed under -- see
    # shared/keywords.py. Kept as plain flat lists, unchanged in shape, so
    # every existing reader of them keeps working; `keyword_groups` below is
    # what adds the searchable child permutations.
    #
    # Derived server-side from `keyword_groups` when that is supplied, so
    # the two can never disagree (client_repository.upsert). A caller may
    # still send only these (no groups) -- that is exactly what a client
    # saved before this feature looks like, and it sweeps identically.
    name_keywords: list[str] = []  # individual people to protect
    domain_keywords: list[str] = []  # brand/domain keyword variants
    # {"individual": [{"parent": str, "children": [str]}], "domain": [...]}
    #
    # `parent` is the real name -- never searched, used to score and file
    # results. `children` are the analyst's own generated permutations --
    # searched on every platform, never scored against. A parent with no
    # children searches itself, which is the pre-groups behaviour.
    # Normalised and validated server-side by
    # shared/keywords.py::normalize_groups, which drops malformed entries
    # rather than rejecting the whole request.
    keyword_groups: dict[str, list[dict]] = {}
    asset_name_individual_keywords: list[str] = []  # asset name choices for individuals
    asset_name_domain_keywords: list[str] = []  # asset name choices for domains
    # platform id -> max results to scrape for THIS client's sweeps, scoped
    # to keywords found on name_keywords/asset_name_individual_keywords
    # (a person/executive search) vs everything else (a domain/brand
    # search), see services/discovery_service.py::_is_individual_keyword.
    # Independent caps: an analyst who wants "don't miss any impersonating
    # exec but cap the noisy brand-name search" can express that directly,
    # instead of one number governing both search types at once. A platform
    # missing here (or mapped to 0) is uncapped, "scrape all", for that
    # keyword type.
    platform_limits_individual: dict[str, int] = {}
    platform_limits_domain: dict[str, int] = {}
    # platform id -> {tab -> {"individual"/"domain": max results}}, for
    # platforms with more than one discovery tab (currently only Facebook:
    # people/pages/groups, see PLATFORM_TABS in services/discovery_service.py).
    # Independent per (tab, keyword type), e.g. Facebook People x Individual
    # can be capped tighter than Pages x Domain, so a noisy tab/keyword-type
    # combination can be limited without affecting the others. A (tab, type)
    # pair missing here falls back to the flat individual/domain cap above;
    # the more restrictive of the two applies when both are set. Uncapped
    # ("scrape everything found") when neither is.
    #
    # Also accepts the legacy flat shape {tab: max results} from before this
    # was split by keyword type, see client_repository.get()'s migration
    # in which case that one number applies to both individual and domain.
    platform_tab_limits: dict[str, dict[str, object]] = {}
    cron: Optional[str] = None
