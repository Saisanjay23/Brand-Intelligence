"""YouTube discovery: keywords -> candidate channels, via the official API.

No browser, so no session, no pacing and no detection surface. The limit is
quota, not rate: each page of 50 results costs 100 units of a 10,000/day
allowance, so a sweep stops on an explicit end of results, a cap, or quota
exhaustion -- and says which.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field

from backend.platforms.facebook.discovery.parse import Hit
from backend.platforms.youtube.analysis import RE_DEFAULT_PIC
from backend.platforms.youtube.api import QuotaExceeded, YouTubeAPI

CHANNEL_URL = "https://www.youtube.com/channel/{cid}"


@dataclass
class Sweep:
    keyword: str
    tab: str = "channels"
    hits: list[Hit] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        return f"{len(self.hits)} hits, {self.pages} pages, {self.stopped}"


class Discovery:
    """`ctx` is accepted and unused -- this platform needs no browser."""

    def __init__(self, args, ctx=None):
        self.a = args
        self.api = YouTubeAPI()

    async def sweep(self, keyword: str, tab: str = "channels") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        by_id: dict[str, Hit] = {}
        token = ""
        try:
            while True:
                if self.a.max_results and len(by_id) >= self.a.max_results:
                    out.stopped = "cap:results"
                    break
                if self.a.max_seconds and time.time() - started >= self.a.max_seconds:
                    out.stopped = "cap:seconds"
                    break

                items, token = await self.api.search_channels(keyword, token)
                out.pages += 1
                for i, it in enumerate(items):
                    cid = (it.get("id") or {}).get("channelId", "")
                    snip = it.get("snippet") or {}
                    if not cid or cid in by_id:
                        continue
                    # search.list's snippet always carries thumbnails -- the
                    # same response the channel came from, no extra request
                    thumbs = snip.get("thumbnails") or {}
                    avatar = (thumbs.get("high") or thumbs.get("medium")
                             or thumbs.get("default") or {}).get("url", "")
                    by_id[cid] = Hit(
                        entity_id=cid,
                        name=(
                            snip.get("channelTitle") or snip.get("title") or ""
                        ).strip(),
                        url=CHANNEL_URL.format(cid=cid),
                        avatar=avatar,
                        has_custom_pic=bool(avatar) and not RE_DEFAULT_PIC.search(avatar),
                        entity_type="channel",
                        keyword=keyword,
                        tab=tab,
                        rank=len(by_id) + i,
                        source="api",
                    )
                if not token:
                    # the API stopped offering pages: genuinely the end
                    out.stopped, out.complete = "exhausted", True
                    break
        except QuotaExceeded as e:
            out.stopped, out.error = "quota", str(e)
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            out.hits = list(by_id.values())
            out.seconds = time.time() - started
        return out

    async def run(self, keywords: list[str], tabs=None) -> list[Sweep]:
        """API calls are cheap to parallelise, but quota is shared -- keep it modest."""
        sem = asyncio.Semaphore(max(1, min(self.a.concurrency, 4)))

        async def one(i: int, keyword: str) -> tuple[int, Sweep]:
            async with sem:
                s = await self.sweep(keyword)
                print(
                    f"  [youtube] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
                    file=sys.stderr,
                )
                return i, s

        pairs = await asyncio.gather(*(one(i, k) for i, k in enumerate(keywords)))
        return [s for _, s in sorted(pairs, key=lambda p: p[0])]


def merge(sweeps: list[Sweep]) -> list[Hit]:
    seen: dict[str, Hit] = {}
    for s in sweeps:
        for h in s.hits:
            seen.setdefault(h.entity_id, h)
    return list(seen.values())
