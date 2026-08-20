"""YouTube discovery engine: search, pagination, and channel extraction,
keywords in, candidate channels out, via the official Data API v3.

Also owns the API client (`YouTubeAPI`) and the default-picture check: both
are produced here first and re-used by analysis_engine.py, which imports
them rather than redefining them, so there is exactly one definition of each
across the two files.

YouTube publishes an official API that returns exactly what the report needs,
so this platform uses no browser at all: nothing to fingerprint, nothing to
detect, and no session to burn. It is the fastest and safest of the six.

QUOTA is the real constraint, not rate limiting. Default allowance is 10,000
units/day:
    search.list        100 units   -- expensive, used once per keyword page
    channels.list        1 unit    -- cheap, batched 50 ids at a time
    playlistItems.list   1 unit    -- cheap, how last-upload is read
So discovery costs ~100 units per 50 results, and analysis is ~2 units per
channel. Reading the newest upload through playlistItems instead of a dated
search is a 100x saving, which is why it is done that way. No browser, so no
session, no pacing and no detection surface, a sweep stops on an explicit
end of results, a cap, or quota exhaustion, and says which.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.shared.logging import get_logger
from backend.platforms.facebook.discovery_engine import Hit

log = get_logger("youtube.api")

BASE = "https://www.googleapis.com/youtube/v3"
CHANNEL_URL = "https://www.youtube.com/channel/{cid}"
# YouTube's stock avatars come from this host; a real upload does not
RE_DEFAULT_PIC = re.compile(r"/(default|no_avatar|blank)", re.I)


class QuotaExceeded(RuntimeError):
    """The daily allowance is gone, retrying today will not help."""


class YouTubeAPI:
    def __init__(self, key: str = ""):
        self.key = key or os.environ.get("YOUTUBE_API_KEY", "")
        if not self.key:
            raise RuntimeError("YOUTUBE_API_KEY is not set")

    def _get_sync(self, endpoint: str, params: dict) -> dict:
        q = urllib.parse.urlencode({**params, "key": self.key}, doseq=True)
        url = f"{BASE}/{endpoint}?{q}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            body_lower = body.lower()
            if e.code in (403, 429) and ("quota" in body_lower or "quotaexceeded" in body_lower):
                raise QuotaExceeded("YouTube daily quota exhausted") from e
            if e.code == 403 and any(tok in body_lower for tok in ("key", "api key", "invalid", "badrequest")):
                raise RuntimeError(f"youtube {endpoint} 403: API key invalid - {body[:200]}") from e
            raise RuntimeError(f"youtube {endpoint} {e.code}: {body[:200]}") from e

    async def get(self, endpoint: str, **params) -> dict:
        """urllib in a thread: one dependency fewer, same behaviour."""
        return await asyncio.to_thread(self._get_sync, endpoint, params)

    # ---------- reads ----------

    async def search_channels(
        self, keyword: str, page_token: str = "", per_page: int = 50
    ) -> tuple[list[dict], str]:
        """--> (items, next_page_token). 100 quota units per call.

        Retries up to 3 times with exponential backoff for transient
        failures (network timeouts, 500s). YouTube's Data API occasionally
        returns empty items on the first attempt but succeeds on retry.
        """
        params: dict[str, Any] = {
            "part": "snippet",
            "type": "channel",
            "q": keyword,
            "maxResults": min(per_page, 50),
        }
        if page_token:
            params["pageToken"] = page_token
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                data = await self.get("search", **params)
                items = data.get("items", [])
                token = data.get("nextPageToken", "")
                # YouTube occasionally returns an empty items list on
                # transient hiccups even with a 200 status. Retry once
                # if this is the first page (no page_token) and we got
                # nothing back.
                if not items and not page_token and attempt < 2:
                    log.info(
                        f"youtube search {keyword!r}: empty response on "
                        f"attempt {attempt + 1}, retrying"
                    )
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                return items, token
            except QuotaExceeded:
                raise
            except Exception as e:
                last_exc = e
                log.info(
                    f"youtube search {keyword!r}: {type(e).__name__} on "
                    f"attempt {attempt + 1}, retrying"
                )
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_exc or RuntimeError("youtube search_channels failed after retries")

    async def channels(self, ids: list[str]) -> list[dict]:
        """Full detail for up to 50 channels in one unit."""
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            data = await self.get(
                "channels",
                part="snippet,statistics,contentDetails,brandingSettings",
                id=",".join(ids[i : i + 50]),
                maxResults=50,
            )
            out += data.get("items", [])
        return out

    async def channel_by_handle(self, handle: str) -> Optional[dict]:
        """Resolve @handle -> channel, or None.

        forHandle is exact. Search is only a fallback for legacy /c/ and /user/
        URLs, and its result is accepted ONLY if the channel's own handle or
        custom URL matches what was asked for, search happily returns a
        similarly-named channel, and silently reporting the wrong one is worse
        than reporting nothing.
        """
        want = handle.lstrip("@").strip().lower()
        if not want:
            return None

        for params in (
            {"forHandle": f"@{want}"},
            {"forHandle": want},
            {"forUsername": want},
        ):
            try:
                data = await self.get(
                    "channels",
                    part="snippet,statistics,contentDetails,brandingSettings",
                    **params,
                )
                if items := data.get("items"):
                    return items[0]
            except (QuotaExceeded, RuntimeError):
                continue

        items, _ = await self.search_channels(handle, per_page=5)
        for it in items:
            cid = (it.get("id") or {}).get("channelId", "")
            if not cid:
                continue
            found = await self.channels([cid])
            if not found:
                continue
            snip = found[0].get("snippet") or {}
            branding = (found[0].get("brandingSettings") or {}).get("channel") or {}
            identifiers = {
                str(snip.get("customUrl") or "").lstrip("@").lower(),
                str(snip.get("title") or "").lower(),
                str(snip.get("channelTitle") or "").lower(),
                str(branding.get("title") or "").lower(),
            }
            if want in identifiers:
                return found[0]
        log.info(f"no channel exactly matches handle {handle!r}")
        return None

    async def latest_upload(self, uploads_playlist: str) -> str:
        """ISO date of the newest upload. 1 unit, versus 100 for a dated search.

        `channels()` always synthesizes an "uploads" playlist id for a
        channel (the `UC` -> `UU` prefix swap) even when that channel has
        never actually uploaded anything, the playlist is never
        materialized server-side in that case, and `playlistItems` 404s on
        it. Confirmed live: a channel with `statistics.videoCount == "0"`
        404s here every time. That is a normal, expected shape (a channel
        genuinely has zero videos), not a real error, analysis_engine.py's
        `process()` already has an `elif videoCount == "0"` fallback for
        exactly this case, but it never got a chance to run before this was
        fixed, because the 404 propagated as an exception straight past it
        and `one()`'s broad except handler discarded the whole `Row`,
        including the profile name/subscriber count `fill()` had already
        successfully populated moments earlier.
        """
        if not uploads_playlist:
            return ""
        try:
            data = await self.get(
                "playlistItems", part="snippet", playlistId=uploads_playlist, maxResults=1
            )
        except RuntimeError as e:
            if "404" in str(e):
                return ""
            raise
        items = data.get("items") or []
        if not items:
            return ""
        published = (items[0].get("snippet") or {}).get("publishedAt", "")
        return published[:10]


# Crawling / pagination


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
    """`ctx` is accepted and unused, this platform needs no browser."""

    def __init__(self, args, ctx=None):
        self.a = args
        self.api = YouTubeAPI()

    async def stop(self) -> None:
        """No persistent connection to release (plain HTTP calls per
        request), exists so discovery_service.py can call every
        no-session platform's discoverer.stop() uniformly, the same way it
        already calls session.stop() for the browser-based ones. See
        telegram/discovery_engine.py's Discovery.stop() for the platform
        where this actually matters."""
        return None

    async def sweep(self, keyword: str, tab: str = "channels", on_progress: Any = None) -> Sweep:
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
                # Small delay between paginated API calls to avoid
                # transient rate limiting from YouTube's backend
                if token and out.pages > 1:
                    await asyncio.sleep(0.5)
                page_hits: list[Hit] = []
                for i, it in enumerate(items):
                    cid = (it.get("id") or {}).get("channelId", "")
                    snip = it.get("snippet") or {}
                    if not cid or cid in by_id:
                        continue
                    # `description`, `publishedAt` and `customUrl` were read
                    # off the snippet here and then dropped on the floor --
                    # `Hit` has no field for any of them (see
                    # facebook/discovery_engine.py::Hit, the shared shape
                    # every platform builds), so there was nowhere for them
                    # to go. Not a wiring gap worth closing either: the
                    # channel's creation date reaches a profile through
                    # analysis instead, where youtube/analysis_engine.py
                    # reads the same `publishedAt` into `row.created_iso`.
                    # search.list's snippet always carries thumbnails, the
                    # same response the channel came from, no extra request
                    thumbs = snip.get("thumbnails") or {}
                    avatar = (thumbs.get("high") or thumbs.get("medium")
                             or thumbs.get("default") or {}).get("url", "")
                    hit = Hit(
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
                    by_id[cid] = hit
                    page_hits.append(hit)

                if page_hits and on_progress and callable(on_progress):
                    try:
                        res = on_progress(len(by_id), out.pages, page_hits)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

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
            if self.a.max_results:
                # Same bug class confirmed live on Twitter's identical
                # pattern: the loop-break check above (`len(by_id) >=
                # max_results`) only fires at the top of the NEXT
                # iteration, after a whole search.list page has already
                # been absorbed into by_id, so a configured cap of 5
                # still returned however many channels came back in that
                # page (YouTube's API commonly pages 50 at a time), with
                # nothing here to trim it back down.
                out.hits = out.hits[: self.a.max_results]
            out.seconds = time.time() - started
        return out

    async def run(self, keywords: list[str], tabs=None) -> list[Sweep]:
        """API calls are cheap to parallelise, but quota is shared, keep it modest."""
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

