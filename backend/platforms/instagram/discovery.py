"""Instagram discovery: keywords -> candidate accounts.

Strategy:
  Directly hit the Instagram Mobile API via Playwright's API context using
  a spoofed Android User-Agent. This bypasses the web UI and returns a robust
  JSON payload with up to 100+ users per request. We also implement pagination
  to fetch all available profiles for the keyword while respecting rate limits.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from backend.platforms.facebook.discovery.parse import Hit
from backend.platforms.instagram.parse import (InstagramUser,
                                               iter_mobile_search_users, parse_lines)


MOBILE_SEARCH_API = "https://i.instagram.com/api/v1/users/search/?q={q}&count={count}"
MOBILE_UA = "Instagram 219.0.0.12.117 Android (29/10; 480dpi; 1080x2151; OnePlus; GM1913; OnePlus7Pro; qcom; en_US; 314660328)"


@dataclass
class Sweep:
    keyword: str
    tab: str = "people"
    hits: list[Hit] = field(default_factory=list)
    users: list[InstagramUser] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        return f"{len(self.hits)} hits, {self.pages} responses, {self.stopped}"


class Discovery:
    def __init__(self, args, ctx):
        self.a = args
        self.ctx = ctx

    async def sweep(self, keyword: str, tab: str = "people") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        by_name: dict[str, InstagramUser] = {}
        
        page_token = None
        rank_token = None
        
        try:
            # We paginate up to 10 times to prevent infinite loops and respect limits
            for p in range(10):
                url = MOBILE_SEARCH_API.format(q=quote(keyword), count=100)
                if page_token:
                    url += f"&page_token={page_token}"
                if rank_token:
                    url += f"&rank_token={rank_token}"
                    
                res = await self.ctx.request.get(
                    url,
                    headers={
                        "User-Agent": MOBILE_UA,
                        "x-ig-app-id": "936619743392459",
                        "accept": "application/json"
                    },
                    timeout=self.a.timeout * 1000
                )
                
                if res.status != 200:
                    out.stopped = f"http-{res.status}"
                    break
                    
                text = await res.text()
                data = json.loads(text)
                
                new_users = 0
                for user in iter_mobile_search_users(data):
                    if user.username.lower() not in by_name:
                        by_name[user.username.lower()] = user
                        new_users += 1
                
                if new_users > 0:
                    out.pages += 1
                
                # Check for pagination
                has_more = data.get("has_more")
                rank_token = data.get("rank_token")
                page_token = data.get("page_token") or data.get("next_max_id")
                
                if not has_more and not page_token:
                    out.stopped = "exhausted"
                    out.complete = True
                    break
                    
                # Respect limits between pages
                await asyncio.sleep(2.5)

            if not out.stopped:
                out.stopped = "limit-reached"
                out.complete = True

            out.users = list(by_name.values())
            out.hits = [
                Hit(
                    entity_id=u.entity_id or u.username,
                    name=u.full_name or u.username,
                    url=u.url,
                    avatar=u.avatar,
                    has_custom_pic=u.has_custom_pic,
                    entity_type="profile",
                    keyword=keyword,
                    tab=tab,
                    rank=i,
                    source="api",
                )
                for i, u in enumerate(out.users)
                if u.url
            ]
            if self.a.max_results:
                out.hits = out.hits[: self.a.max_results]
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            out.seconds = time.time() - started
            
        return out

    async def run(self, keywords: list[str], tabs=None) -> list[Sweep]:
        sem = asyncio.Semaphore(max(1, self.a.concurrency))

        async def one(i: int, keyword: str) -> tuple[int, Sweep]:
            async with sem:
                # Initial delay to space out concurrent requests
                await asyncio.sleep(i % max(1, self.a.concurrency) * 2.0)
                s = await self.sweep(keyword)
                print(
                    f"  [instagram] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
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
