"""Telegram discovery: keywords -> candidate users, channels and groups.

Telegram's global search returns a single capped page per keyword -- there is
no cursor and no "load more" -- so a sweep is one request and is genuinely
complete when it returns. Unlike the browser platforms there is nothing to
scroll and nothing to miss.

A sweep is only incomplete if Telegram asked us to wait (FloodWait), which is
reported rather than retried.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field

from backend.platforms.facebook.discovery.parse import Hit
from backend.platforms.telegram.client import (FloodWait, NotAuthorised,
                                               Telegram, TelegramEntity)

# global search caps well below this; asking for more costs nothing
SEARCH_LIMIT = 100


@dataclass
class Sweep:
    keyword: str
    tab: str = "all"
    hits: list[Hit] = field(default_factory=list)
    entities: list[TelegramEntity] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        return f"{len(self.hits)} hits, {self.stopped}"


class Discovery:
    """`ctx` is accepted and unused -- MTProto needs no browser."""

    def __init__(self, args, ctx=None):
        self.a = args
        self.tg: Telegram | None = None

    async def sweep(self, keyword: str, tab: str = "all") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        try:
            found = await self.tg.search(keyword, SEARCH_LIMIT)
            out.pages = 1
            out.entities = found
            out.hits = [
                Hit(
                    entity_id=e.entity_id or e.username,
                    name=e.title,
                    url=e.url,
                    avatar=e.avatar,
                    # Telethon's own PhotoEmpty check -- see client.py -- not a
                    # guess from whether the avatar URL happens to resolve
                    has_custom_pic=e.has_photo,
                    entity_type=e.kind,
                    keyword=keyword,
                    tab=e.kind,
                    rank=i,
                    source="mtproto",
                )
                for i, e in enumerate(found)
                if e.url
            ]
            if self.a.max_results:
                out.hits = out.hits[: self.a.max_results]
            out.stopped, out.complete = "exhausted", True
        except FloodWait as e:
            out.stopped, out.error = "flood-wait", str(e)
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            out.seconds = time.time() - started
        return out

    async def run(self, keywords: list[str], tabs=None) -> list[Sweep]:
        """Sequential on purpose: one session, and search is what gets limited."""
        self.tg = Telegram(self.a)
        sweeps: list[Sweep] = []
        try:
            await self.tg.start()
            if not await self.tg.check_session():
                raise NotAuthorised("telegram session rejected")
            for i, keyword in enumerate(keywords):
                s = await self.sweep(keyword)
                sweeps.append(s)
                print(
                    f"  [telegram] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
                    file=sys.stderr,
                )
                if s.stopped == "flood-wait":
                    print(
                        "  telegram asked us to slow down -- stopping the sweep",
                        file=sys.stderr,
                    )
                    break
                if i < len(keywords) - 1:
                    await asyncio.sleep(2.0)  # unhurried between searches
        finally:
            await self.tg.stop()
        return sweeps


def merge(sweeps: list[Sweep]) -> list[Hit]:
    seen: dict[str, Hit] = {}
    for s in sweeps:
        for h in s.hits:
            seen.setdefault(h.entity_id or h.url, h)
    return list(seen.values())
