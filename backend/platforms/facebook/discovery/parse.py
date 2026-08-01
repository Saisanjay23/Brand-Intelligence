"""Reading search results out of Facebook's own search payloads.

Everything here is a pure function of a parsed payload, so it can be tested
against a saved capture without driving a browser.

WHY NOT SCRAPE THE LINKS OFF THE PAGE
    A search page is full of profile links that are not results: the chat
    sidebar, the notification flyout, "people you may know". Harvesting
    a[href*=facebook.com] on the People tab returned 52 ids that were not
    results at all. Results live in one place -- the edges of the search
    connection -- and that is the only place this reads.

WHERE THE RESULTS ARE
    data.serpResponse.results.edges[]
        .rendering_strategy.view_model            __typename SearchProfileViewModel
            .profile                              __typename User | Page
                id, name, profile_url, url
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from backend.utils.text import iter_dicts
from backend.platforms.facebook.analysis.constants import RE_DEFAULT_PIC
from backend.platforms.facebook.urls import hd_picture_url

VIEW_MODELS = {"SearchProfileViewModel"}
ENTITY_TYPES = {"User": "profile", "Page": "page"}
QUERY_NAME = "SearchCometResultsPaginatedResultsQuery"


@dataclass
class Hit:
    """One search result."""

    entity_id: str
    name: str
    url: str
    avatar: str = ""
    # whether `avatar` is the entity's own uploaded photo, not the platform's
    # placeholder/default avatar -- decided per-platform at the point avatar is
    # set (see each discovery module), never guessed from "an avatar URL exists"
    has_custom_pic: bool = False
    entity_type: str = "profile"  # profile | page
    keyword: str = ""
    tab: str = ""
    rank: int = 0
    source: str = "graphql"  # graphql | id-backfill

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "url": self.url,
            "avatar": self.avatar,
            "has_custom_pic": self.has_custom_pic,
            "entity_type": self.entity_type,
            "keyword": self.keyword,
            "tab": self.tab,
            "rank": self.rank,
            "source": self.source,
        }


@dataclass
class PageState:
    """What one pagination response says about how far the results go.

    `ids_shown` is what Facebook rendered. `ids_processed` is the wider set the
    backend considered -- on the Pages tab it held 32 ids while only 30 were
    ever rendered. Those extras are real entities the search matched but chose
    not to display, so discovery keeps them, tagged separately.
    """

    has_next: bool = False
    page_number: Optional[int] = None
    ids_shown: list[str] = field(default_factory=list)
    ids_processed: list[str] = field(default_factory=list)
    total_results: Optional[int] = None
    end_of_serp: bool = False


def profile_url_for(entity_id: str) -> str:
    return f"https://www.facebook.com/profile.php?id={entity_id}"


def iter_results(blob: Any) -> Iterator[Hit]:
    """Every profile/page result in one pagination payload, in page order."""
    for edge_holder in iter_dicts(blob):
        edges = edge_holder.get("edges")
        if not isinstance(edges, list):
            continue
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue
            vm = (edge.get("rendering_strategy") or {}).get("view_model")
            if not isinstance(vm, dict) or vm.get("__typename") not in VIEW_MODELS:
                continue
            prof = vm.get("profile")
            if not isinstance(prof, dict):
                continue
            eid = prof.get("id")
            if not (isinstance(eid, str) and eid.isdigit()):
                continue
            url = prof.get("profile_url") or prof.get("url") or profile_url_for(eid)
            # already in this same search response -- no extra request needed.
            # The search snippet asks for a 40-60px thumbnail; bump it to the
            # same URL's own signed max (cstp) so cards show the real upload,
            # not a postage stamp, without discovery costing an extra request.
            pic = prof.get("profile_picture")
            raw_uri = pic.get("uri", "") if isinstance(pic, dict) else ""
            has_custom = bool(raw_uri) and not bool(RE_DEFAULT_PIC.search(raw_uri))
            avatar = hd_picture_url(raw_uri) if has_custom else ""
            yield Hit(
                entity_id=eid,
                name=(prof.get("name") or "").strip(),
                url=url.split("?__")[0],
                avatar=avatar,
                has_custom_pic=has_custom,
                entity_type=ENTITY_TYPES.get(prof.get("__typename"), "profile"),
                rank=i,
            )


def page_state(blob: Any) -> Optional[PageState]:
    """The pagination cursor for the search connection, if this payload has one.

    Facebook puts a page_info on several connections per response (the
    notification dropdown has one too), so this only accepts a cursor that
    decodes to a search cursor -- one carrying result_ids_shown.
    """
    for d in iter_dicts(blob):
        pi = d.get("page_info")
        if not isinstance(pi, dict) or "has_next_page" not in pi:
            continue
        cursor = pi.get("end_cursor")
        if not isinstance(cursor, str):
            continue
        try:
            c = json.loads(cursor)
        except (json.JSONDecodeError, ValueError):
            continue
        if "result_ids_shown" not in c:
            continue
        totals = c.get("unit_id_logging_fields") or {}
        return PageState(
            has_next=bool(pi["has_next_page"]),
            page_number=c.get("page_number"),
            ids_shown=[str(i) for i in (c.get("result_ids_shown") or [])],
            ids_processed=_processed_ids(c),
            total_results=totals.get("num_total_results"),
            end_of_serp=bool(c.get("is_end_of_serp")),
        )
    return None


def _processed_ids(cursor: dict) -> list[str]:
    """Ids the backend matched, including ones it never rendered.

    They sit in the per-tab flow cursor, which is itself a JSON string.
    """
    out: list[str] = []
    for v in (cursor.get("flow_cursors_serialized") or {}).values():
        if not isinstance(v, str) or "processed_unicorn_ids" not in v:
            continue
        try:
            inner = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            continue
        out += [str(i) for i in (inner.get("processed_unicorn_ids") or [])]
    out += [str(i) for i in (cursor.get("processed_unicorn_ids") or [])]
    return out


def is_search_response(post_body: str) -> bool:
    return QUERY_NAME in (post_body or "")


def parse_lines(text: str) -> Iterator[Any]:
    """Search responses stream as newline-delimited JSON chunks."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


RE_EMBEDDED = re.compile(r"^\s*\{")


def parse_embedded(texts) -> Iterator[Any]:
    """The first page of results is server-rendered, not fetched over XHR."""
    for t in texts or []:
        if not t or "SearchProfileViewModel" not in t:
            continue
        if not RE_EMBEDDED.match(t):
            continue
        try:
            yield json.loads(t)
        except (json.JSONDecodeError, ValueError):
            continue
