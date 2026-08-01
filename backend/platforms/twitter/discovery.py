"""X/Twitter discovery: keywords -> candidate accounts.

Uses the People tab of search (`f=user`) and reads the SearchTimeline payload
directly. Every result arrives fully hydrated -- handle, name, followers, join
date -- so unlike Facebook there is no second visit needed to score a profile
found here.

Pagination is cursor-driven and, like Facebook's people search, effectively
endless for a common name. A sweep therefore stops on an explicit end (no new
bottom cursor / no new users) or on a budget, and says which.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

from backend.platforms.facebook.discovery.parse import Hit
from backend.platforms.twitter.parse import (SEARCH_QUERY, TwitterUser,
                                             parse_lines, search_state)

SEARCH_URL = "https://x.com/search?q={q}&src=typed_query&f=user"


@dataclass
class Sweep:
    keyword: str
    tab: str = "people"
    hits: list[Hit] = field(default_factory=list)
    users: list[TwitterUser] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        return f"{len(self.hits)} hits, {self.pages} pages, {self.stopped}"


class Discovery:
    """Runs keyword sweeps on an already-started browser session."""

    def __init__(self, args, ctx):
        self.a = args
        self.ctx = ctx

    async def sweep(self, keyword: str, tab: str = "people") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        page = await self.ctx.new_page()

        by_id: dict[str, TwitterUser] = {}
        cursor = ""
        arrived = asyncio.Event()

        async def on_response(resp):
            nonlocal cursor
            try:
                if SEARCH_QUERY not in resp.url:
                    return
                text = await resp.text()
            except Exception:
                return
            for blob in parse_lines(text):
                st = search_state(blob)
                for u in st.users:
                    by_id.setdefault(u.entity_id or u.handle, u)
                if st.bottom_cursor:
                    cursor = st.bottom_cursor
            out.pages += 1
            arrived.set()

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            await page.goto(
                SEARCH_URL.format(q=quote(keyword)),
                wait_until="domcontentloaded",
                timeout=self.a.timeout * 1000,
            )
            try:
                await page.wait_for_function(
                    "() => document.body.innerText.length > 400",
                    timeout=self.a.settle * 1000,
                )
            except Exception:
                pass

            stalls, last_cursor = 0, ""
            while True:
                if self.a.max_results and len(by_id) >= self.a.max_results:
                    out.stopped = "cap:results"
                    break
                if self.a.max_seconds and time.time() - started >= self.a.max_seconds:
                    out.stopped = "cap:seconds"
                    break

                before = len(by_id)
                arrived.clear()
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    await asyncio.wait_for(arrived.wait(), timeout=self.a.page_wait)
                except asyncio.TimeoutError:
                    pass

                if len(by_id) > before:
                    stalls = 0
                    if out.pages % self.a.progress_every == 0:
                        print(
                            f"    [x/{tab}] {keyword!r}: {len(by_id)} so far, "
                            f"page {out.pages}, {time.time()-started:.0f}s",
                            file=sys.stderr,
                        )
                else:
                    stalls += 1
                    # the same bottom cursor twice with no new users is the end
                    if cursor and cursor == last_cursor and stalls >= 2:
                        out.stopped, out.complete = "exhausted", True
                        break
                    if stalls >= self.a.patience:
                        out.stopped = "stalled"
                        break
                    await page.wait_for_timeout(600)
                last_cursor = cursor

            out.users = list(by_id.values())
            out.hits = [
                Hit(
                    entity_id=u.entity_id,
                    name=u.name,
                    url=u.url,
                    avatar=u.avatar,   # already in the SearchTimeline payload
                    has_custom_pic=u.has_custom_pic,
                    entity_type="profile",
                    keyword=keyword,
                    tab=tab,
                    rank=i,
                    source="graphql",
                )
                for i, u in enumerate(out.users)
                if u.url
            ]
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            try:
                await page.close()
            except Exception:
                pass
            out.seconds = time.time() - started
        return out

    async def run(
        self, keywords: list[str], tabs: Optional[list[str]] = None
    ) -> list[Sweep]:
        """X has one people-search surface, so `tabs` is accepted and ignored."""
        sem = asyncio.Semaphore(max(1, self.a.concurrency))

        async def one(i: int, keyword: str) -> tuple[int, Sweep]:
            async with sem:
                await asyncio.sleep(i % max(1, self.a.concurrency) * 1.0)
                s = await self.sweep(keyword)
                print(
                    f"  [x/people] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
                    file=sys.stderr,
                )
                return i, s

        pairs = await asyncio.gather(*(one(i, k) for i, k in enumerate(keywords)))
        return [s for _, s in sorted(pairs, key=lambda p: p[0])]


def merge(sweeps: list[Sweep]) -> list[Hit]:
    seen: dict[str, Hit] = {}
    for s in sweeps:
        for h in s.hits:
            seen.setdefault(h.entity_id or h.url, h)
    return list(seen.values())
