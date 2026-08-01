"""YouTube Data API v3 client.

YouTube publishes an official API that returns exactly what the report needs,
so this platform uses no browser at all: nothing to fingerprint, nothing to
detect, and no session to burn. It is the fastest and safest of the four.

QUOTA is the real constraint, not rate limiting. Default allowance is 10,000
units/day:
    search.list        100 units   -- expensive, used once per keyword page
    channels.list        1 unit    -- cheap, batched 50 ids at a time
    playlistItems.list   1 unit    -- cheap, how last-upload is read
So discovery costs ~100 units per 50 results, and analysis is ~2 units per
channel. Reading the newest upload through playlistItems instead of a dated
search is a 100x saving, which is why it is done that way.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from backend.utils.logging import get_logger

log = get_logger("youtube.api")

BASE = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(RuntimeError):
    """The daily allowance is gone -- retrying today will not help."""


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
            if e.code == 403 and "quota" in body.lower():
                raise QuotaExceeded("YouTube daily quota exhausted") from e
            raise RuntimeError(f"youtube {endpoint} {e.code}: {body[:200]}") from e

    async def get(self, endpoint: str, **params) -> dict:
        """urllib in a thread: one dependency fewer, same behaviour."""
        return await asyncio.to_thread(self._get_sync, endpoint, params)

    # ---------- reads ----------

    async def search_channels(
        self, keyword: str, page_token: str = "", per_page: int = 50
    ) -> tuple[list[dict], str]:
        """-> (items, next_page_token). 100 quota units per call."""
        params: dict[str, Any] = {
            "part": "snippet",
            "type": "channel",
            "q": keyword,
            "maxResults": min(per_page, 50),
        }
        if page_token:
            params["pageToken"] = page_token
        data = await self.get("search", **params)
        return data.get("items", []), data.get("nextPageToken", "")

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
        custom URL matches what was asked for -- search happily returns a
        similarly-named channel, and silently reporting the wrong one is worse
        than reporting nothing.
        """
        want = handle.lstrip("@").strip().lower()
        if not want:
            return None

        for key in ("forHandle", "forUsername"):
            try:
                data = await self.get(
                    "channels",
                    part="snippet,statistics,contentDetails,brandingSettings",
                    **{key: handle.lstrip("@")},
                )
                if items := data.get("items"):
                    return items[0]
            except RuntimeError:
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
            identifiers = {
                str(snip.get("customUrl") or "").lstrip("@").lower(),
                str(snip.get("title") or "").lower(),
            }
            if want in identifiers:
                return found[0]
        log.info(f"no channel exactly matches handle {handle!r}")
        return None

    async def latest_upload(self, uploads_playlist: str) -> str:
        """ISO date of the newest upload. 1 unit, versus 100 for a dated search."""
        if not uploads_playlist:
            return ""
        data = await self.get(
            "playlistItems", part="snippet", playlistId=uploads_playlist, maxResults=1
        )
        items = data.get("items") or []
        if not items:
            return ""
        published = (items[0].get("snippet") or {}).get("publishedAt", "")
        return published[:10]
