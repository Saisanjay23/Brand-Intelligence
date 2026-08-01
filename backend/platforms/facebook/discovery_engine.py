"""Facebook discovery engine: search, crawling, pagination, profile/URL
extraction -- keywords in, candidate profile URLs out.

Also owns the browser session (login/checkpoint detection) and URL/identity
normalization: both are produced here first and re-used by
analysis_engine.py, which imports them rather than redefining them, so
there is exactly one definition of each across the two files.

COMPLETENESS
    Three independent stopping signals, and the run records which one fired:
      * has_next_page = false          -- Facebook says there is no more
      * is_end_of_serp = true          -- the results feed is exhausted
      * no new ids after `--patience` scrolls
    Anything else (a cap, an error) is reported as an incomplete sweep rather
    than being silently treated as the end.

    People and Pages are NOT held to the same completeness bar, on purpose:
    Pages is a genuinely small, finite set for any real keyword, so it always
    runs to one of the three signals above -- no result-count cap at all.
    People is not: a common name keeps serving loosely-matching profiles well
    past anything an analyst would act on, so it stops at PEOPLE_MAX_RESULTS
    (250) regardless of whether Facebook would still hand over more.

    The cursor also carries `result_ids_shown`: every id Facebook has rendered
    so far. After the sweep those are reconciled against what was extracted,
    and any id we never saw as an edge is backfilled from its id alone. That
    is the guard against a layout change quietly dropping results.

SPEED
    Pagination is cursor-driven, so pages must be fetched in order -- but the
    scanner waits on the pagination response itself rather than sleeping, and
    images are never downloaded. Different keywords are independent, so they
    run in parallel tabs.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional
from urllib.parse import parse_qs, quote, urlparse

from backend.utils.text import iter_dicts
from backend.stealth.browser import Session

# ─────────────────────────── session / login state ─────────────────────────

ME = "https://www.facebook.com/me"

RE_LOGIN = re.compile(r"(You must log in|Log in to Facebook|Log In or Sign Up)", re.I)
RE_CHECKPOINT = re.compile(
    r"(checkpoint|suspicious activity|Confirm Your Identity|"
    r"account has been locked|We've temporarily)",
    re.I,
)
RE_GONE = re.compile(
    r"(isn't available|Page Not Found|content is currently unavailable)", re.I
)

# real profile photos live on scontent*.fbcdn.net; rsrc.php / static.xx / t1.30497-1 is
# Facebook's own chrome, which is where the silhouette placeholder comes from.
# Shared with analysis_engine.py: discovery uses it to decide has_custom_pic
# on a fresh Hit, analysis uses it again once it re-visits the profile.
RE_DEFAULT_PIC = re.compile(
    r"(static.*silhouette|default.*avatar|/rsrc\.php/|static\.xx\.fbcdn\.net|t1\.30497-1|30497-1)",
    re.I,
)


class FacebookSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(ME, RE_LOGIN, RE_CHECKPOINT)


# ───────────────────────────── URL / identity ──────────────────────────────


def normalize_url(url: str) -> str:
    url = url.strip().strip("\"'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if "facebook" in host or host in {"fb.com", "fb.me"}:
        host = "www.facebook.com"
    q = f"?{p.query}" if p.query else ""
    return f"https://{host}{p.path}{q}"


def profile_id(url: str) -> str:
    """Returns the numeric id or the vanity slug -- always as a string.

    Normalises first: without a scheme, urlparse puts the host in the path and
    the first segment comes back as "facebook.com".
    """
    url = normalize_url(url)
    if m := re.search(r"profile\.php\?id=(\d+)", url):
        return m.group(1)
    if m := re.search(r"/people/[^/]+/(\d+)", url):
        return m.group(1)
    seg = [s for s in urlparse(url).path.split("/") if s]
    if not seg:
        return ""
    first = seg[0].split("?")[0]
    bad = {"pages", "groups", "profile.php", "people", "watch", "reel", "share"}
    return "" if first.lower() in bad else first


def tab_url(base: str, sk: str) -> str:
    p = urlparse(base)
    if "profile.php" in p.path:
        uid = parse_qs(p.query).get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={uid}&sk={sk}"
    return f"https://www.facebook.com{p.path.rstrip('/')}/{sk}"


# fbcdn signs the whole crop range up to `cstp`'s bound, not the specific
# `ctp` size actually requested -- every profile-picture URL Facebook hands
# out (search snippet or profile header alike) asks for a tiny ctp (40-60px)
# while cstp carries the real uploaded photo's native size. Raising ctp to
# match cstp, with the same signature tokens untouched, is what actually
# yields the full-resolution upload rather than the thumbnail -- verified
# live: a 50x50 crop (2.8KB) vs. the same URL bumped to 638x638 (33KB), both
# 200 OK, no new request needed.
_CTP = re.compile(r"ctp=s\d+x\d+")
_CSTP = re.compile(r"cstp=mx(\d+)x(\d+)")
_STP_SIZE = re.compile(r"([sp])\d+x\d+")


def hd_picture_url(url: str) -> str:
    if not url:
        return url
    cstp = _CSTP.search(url)
    if not cstp:
        return url
    w, h = cstp.group(1), cstp.group(2)
    if _CTP.search(url):
        url = _CTP.sub(f"ctp=s{w}x{h}", url)
    url = _STP_SIZE.sub(lambda m: f"{m.group(1)}{w}x{h}", url)
    return url


# ───────────────────── search payload parsing (profile extraction) ─────────
#
# Reading search results out of Facebook's own search payloads.
#
# WHY NOT SCRAPE THE LINKS OFF THE PAGE
#     A search page is full of profile links that are not results: the chat
#     sidebar, the notification flyout, "people you may know". Harvesting
#     a[href*=facebook.com] on the People tab returned 52 ids that were not
#     results at all. Results live in one place -- the edges of the search
#     connection -- and that is the only place this reads.
#
# WHERE THE RESULTS ARE
#     data.serpResponse.results.edges[]
#         .rendering_strategy.view_model            __typename SearchProfileViewModel
#             .profile                              __typename User | Page
#                 id, name, profile_url, url

VIEW_MODELS = {"SearchProfileViewModel"}
ENTITY_TYPES = {"User": "profile", "Page": "page"}
QUERY_NAME = "SearchCometResultsPaginatedResultsQuery"


@dataclass
class Hit:
    """One search result. The universal discovery-result shape every other
    platform's discovery_engine.py also builds -- see each platform's
    `from backend.platforms.facebook.discovery_engine import Hit`."""

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


# ───────────────────────────── crawling / pagination ───────────────────────
#
# Searches the People and Pages tabs for each keyword and collects every
# result Facebook will serve, then hands the URLs to the analysis phase.

TABS = {
    "people": "https://www.facebook.com/search/people/?q={q}",
    "pages": "https://www.facebook.com/search/pages/?q={q}",
}

# People search is effectively unbounded -- Facebook keeps serving pages of
# loosely-matching profiles long past anything useful for a common keyword,
# so it gets a hard stop. Pages has no such cap: real Facebook Pages for a
# given keyword are a small, genuinely finite set, so a Pages sweep is meant
# to run to its own real completion (exhausted/end-of-serp/stalled) rather
# than being cut off by a count -- the time budget (DiscoveryOptions.
# max_seconds) stays as an outer safety net for both tabs, but should
# essentially never be what stops a Pages sweep in practice.
PEOPLE_MAX_RESULTS = 250

JS_EMBEDDED = (
    "() => Array.from(document.querySelectorAll("
    "'script[type=\"application/json\"]')).map(s => s.textContent)"
    ".filter(t => t && t.length > 40)"
)


async def _notify(on_progress, found_count: int, page_num: int, new_hits: list) -> None:
    """Best-effort progress callback -- a broken callback must never abort
    the scrape itself, so any exception here is swallowed by the caller."""
    if asyncio.iscoroutinefunction(on_progress):
        await on_progress(found_count, page_num, new_hits)
    else:
        on_progress(found_count, page_num, new_hits)


@dataclass
class Sweep:
    """One keyword on one tab, and how the search ended."""

    keyword: str
    tab: str
    hits: list[Hit] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""  # exhausted | end-of-serp | stalled | cap | error
    complete: bool = False
    reported_total: Optional[int] = None
    backfilled: int = 0
    unshown: int = 0
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        note = f"{len(self.hits)} hits, {self.pages} pages, {self.stopped}"
        if self.backfilled:
            note += f", {self.backfilled} backfilled"
        if self.unshown:
            note += f", {self.unshown} matched-but-unshown"
        if self.reported_total is not None and self.reported_total != len(self.hits):
            note += f", facebook counted {self.reported_total}"
        return note


class Discovery:
    """Runs keyword sweeps on an already-started browser session."""

    def __init__(self, args, ctx):
        self.a = args
        self.ctx = ctx

    async def sweep(self, keyword: str, tab: str, on_progress=None) -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        page = await self.ctx.new_page()

        by_id: dict[str, Hit] = {}
        rendered_ids: set[str] = set()
        processed_ids: set[str] = set()
        state: Optional[PageState] = None
        arrived = asyncio.Event()
        # the tab is authoritative about what was searched; __typename is not --
        # Pages render through the profile stack and report themselves as User
        kind = "page" if tab == "pages" else "profile"

        def absorb(blob) -> None:
            nonlocal state
            for hit in iter_results(blob):
                # capped here, not only in the while-loop's own check below --
                # one response can carry a whole page's worth of edges at
                # once, so checking only between scrolls let by_id overshoot
                # 250 by up to a page's width before the loop noticed, and
                # those extras were already incrementally saved to Mongo by
                # the time it did
                if tab == "people" and len(by_id) >= PEOPLE_MAX_RESULTS:
                    break
                if hit.entity_id not in by_id:
                    hit.keyword, hit.tab, hit.entity_type = keyword, tab, kind
                    by_id[hit.entity_id] = hit
            if st := page_state(blob):
                state = st
                rendered_ids.update(st.ids_shown)
                processed_ids.update(st.ids_processed)

        async def on_response(resp):
            try:
                if "/api/graphql" not in resp.url:
                    return
                if not is_search_response(resp.request.post_data or ""):
                    return
                text = await resp.text()
            except Exception:
                return
            for blob in parse_lines(text):
                absorb(blob)
            out.pages += 1
            arrived.set()

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            url = TABS[tab].format(q=quote(keyword))
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000
            )
            # the first page of results is in the document, not over XHR
            try:
                await page.wait_for_function(
                    "() => document.body.innerText.length > 400",
                    timeout=self.a.settle * 1000,
                )
            except Exception:
                pass
            for blob in parse_embedded(await page.evaluate(JS_EMBEDDED)):
                absorb(blob)

            # Insertion order in by_id is discovery order, so slicing from
            # `notified` on each call yields exactly the hits new since the
            # last notification -- callers (jobs.py) use this to persist and
            # show results while a single long sweep is still running,
            # instead of everything landing at once when it finally ends.
            notified = 0
            if on_progress and by_id:
                notified = len(by_id)
                try:
                    await _notify(on_progress, len(by_id), 0, list(by_id.values()))
                except Exception:
                    pass

            stalls = 0
            while True:
                if tab == "people" and len(by_id) >= PEOPLE_MAX_RESULTS:
                    out.stopped = "cap:results"
                    break
                if self.a.max_results and len(by_id) >= self.a.max_results:
                    out.stopped = "cap:results"
                    break
                if self.a.max_pages and out.pages >= self.a.max_pages:
                    out.stopped = "cap:pages"
                    break
                if self.a.max_seconds and time.time() - started >= self.a.max_seconds:
                    out.stopped = "cap:seconds"
                    break
                if state and not state.has_next:
                    out.stopped = "end-of-serp" if state.end_of_serp else "exhausted"
                    out.complete = True
                    break

                before = len(by_id)
                arrived.clear()
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception as e:
                    out.stopped = "error"
                    out.error = f"scroll failed: {e}"
                    break
                # wait for the next page of results rather than a fixed sleep
                try:
                    await asyncio.wait_for(arrived.wait(), timeout=self.a.page_wait)
                except asyncio.TimeoutError:
                    pass

                if len(by_id) > before:
                    stalls = 0
                    if on_progress:
                        new_hits = list(by_id.values())[notified:]
                        notified = len(by_id)
                        try:
                            await _notify(on_progress, len(by_id), out.pages, new_hits)
                        except Exception:
                            pass
                    # people search runs for a long time; show it is progressing
                    if out.pages % self.a.progress_every == 0:
                        print(
                            f"    [{tab:<6}] {keyword!r}: {len(by_id)} so far, "
                            f"page {out.pages}, {time.time()-started:.0f}s",
                            file=sys.stderr,
                        )
                else:
                    stalls += 1
                    if stalls >= self.a.patience:
                        out.stopped = "stalled"
                        break
                    await page.wait_for_timeout(600)

            # anything Facebook rendered but we never parsed as an edge: a
            # layout change would show up here rather than as silent data loss
            missing = rendered_ids - by_id.keys()
            for eid in sorted(missing):
                by_id[eid] = Hit(
                    entity_id=eid,
                    # blank, not a "fb:{id}" placeholder string -- the UI's
                    # own display_name -> username -> entity_id -> "Unnamed
                    # Profile" fallback already renders this sensibly, and
                    # nothing downstream keys off the old "fb:" prefix anymore
                    name="",
                    url=profile_url_for(eid),
                    entity_type=kind,
                    keyword=keyword,
                    tab=tab,
                    source="id-backfill",
                )
            out.backfilled = len(missing)

            # matched by the backend but never rendered -- kept because a
            # profile Facebook declined to show is still a candidate
            unshown = processed_ids - by_id.keys()
            for eid in sorted(unshown):
                by_id[eid] = Hit(
                    entity_id=eid,
                    # blank, not a "fb:{id}" placeholder string -- the UI's
                    # own display_name -> username -> entity_id -> "Unnamed
                    # Profile" fallback already renders this sensibly, and
                    # nothing downstream keys off the old "fb:" prefix anymore
                    name="",
                    url=profile_url_for(eid),
                    entity_type=kind,
                    keyword=keyword,
                    tab=tab,
                    source="processed-not-shown",
                )
            out.unshown = len(unshown)
            # Every id in by_id came from a real Facebook edge, a rendered-id
            # gap, or an id the backend matched but declined to render -- all
            # three are genuine candidates (see the docstrings above), and the
            # UI already falls back to entity_id/"Unnamed Profile" when a name
            # is blank. An earlier version of this dropped any hit with a
            # blank/numeric name, which silently discarded every backfilled
            # and processed-not-shown candidate -- defeating the whole point
            # of tracking them -- plus any genuine People-tab result Facebook
            # simply returned no name for (privacy-restricted profiles do
            # this often, which is exactly the "people not appearing" bug).
            out.hits = sorted(
                by_id.values(), key=lambda h: (h.source != "graphql", h.rank)
            )
            if tab == "people" and len(out.hits) > PEOPLE_MAX_RESULTS:
                # the loop's own cap check (above) stops fetching at 250, but
                # reconciliation can still add a page's worth of ids Facebook
                # already told us about (rendered/processed) before the cap
                # fired -- trim back to the real limit here too, keeping the
                # graphql-confirmed hits (sorted first) over backfilled ones
                out.hits = out.hits[:PEOPLE_MAX_RESULTS]
            out.reported_total = state.total_results if state else None
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            try:
                await page.close()
            except Exception:
                pass
            out.seconds = time.time() - started
        return out

    async def run(self, keywords: list[str], tabs: list[str]) -> list[Sweep]:
        """Every keyword on every tab. Keywords are independent, so they overlap."""
        jobs = [(k, t) for k in keywords for t in tabs]
        sem = asyncio.Semaphore(max(1, self.a.concurrency))

        async def one(i: int, keyword: str, tab: str):
            async with sem:
                await asyncio.sleep(i % max(1, self.a.concurrency) * 1.0)
                s = await self.sweep(keyword, tab)
                print(
                    f"  [{tab:<6}] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
                    file=sys.stderr,
                )
                return i, s

        pairs = await asyncio.gather(*(one(i, k, t) for i, (k, t) in enumerate(jobs)))
        return [s for _, s in sorted(pairs, key=lambda p: p[0])]


def merge(sweeps: list[Sweep]) -> list[Hit]:
    """One row per profile, keeping the first keyword/tab that found it."""
    seen: dict[str, Hit] = {}
    for s in sweeps:
        for h in s.hits:
            seen.setdefault(h.entity_id, h)
    return list(seen.values())
