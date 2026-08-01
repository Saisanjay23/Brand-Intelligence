"""YouTube analysis engine: validation -- channel URL -> scored Row, via the
official API.

The API client (`YouTubeAPI`) and the default-picture check live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns URL normalization for the analysis entry point and the
drive loop (Scraper).

Two cheap calls per channel (detail + newest upload) and every field arrives
typed: subscriber counts as integers, a real creation date, and an upload date
that makes the activity check meaningful. No browser, so `start`/`stop` are
no-ops kept only to satisfy the same interface as the browser platforms.
"""

from __future__ import annotations

import sys
from urllib.parse import unquote, urlparse

from backend.shared.models.row import Row
from backend.shared.text import fmt_created, name_score
from backend.platforms.youtube.discovery_engine import (CHANNEL_URL,
                                                         RE_DEFAULT_PIC,
                                                         QuotaExceeded,
                                                         YouTubeAPI)


def normalize_url(url: str) -> str:
    url = (url or "").strip().strip("\"'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if "youtu" in host:
        host = "www.youtube.com"
    return f"https://{host}{p.path.rstrip('/')}"


def channel_ref(url: str) -> tuple[str, str]:
    """-> (kind, value) where kind is 'id' | 'handle' | 'name'."""
    p = urlparse(normalize_url(url))
    seg = [unquote(s) for s in p.path.split("/") if s]
    if not seg:
        return "", ""
    if seg[0] == "channel" and len(seg) > 1:
        return "id", seg[1]
    if seg[0].startswith("@"):
        return "handle", seg[0]
    if seg[0] in ("c", "user") and len(seg) > 1:
        return "handle", seg[1]
    return "handle", seg[0]


class Scraper:
    """Same surface as the browser scanners; no browser behind it."""

    normalize_url = staticmethod(normalize_url)

    def __init__(self, args, cookies=None, session_id: str = "", proxy=None):
        # API-key authed, no browser -- session_id/proxy exist only so
        # jobs.py can call every platform's Scraper with the same signature.
        self.a = args
        self.api = YouTubeAPI()

    async def start(self):
        return None

    async def stop(self):
        return None

    async def pause(self, mult: float = 1.0):
        return None  # quota-bound, not rate-bound: no pacing needed

    async def check_session(self) -> bool:
        """A key with no quota left is as unusable as an expired cookie."""
        try:
            await self.api.get("channels", part="id", forHandle="youtube")
            print("SESSION: API key valid", file=sys.stderr)
            return True
        except QuotaExceeded as e:
            print(f"SESSION: {e}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"SESSION: API key rejected -- {e}", file=sys.stderr)
            return False

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        kind, ref = channel_ref(url)
        row.entity_type = "channel"

        if not ref:
            row.status = "ERROR"
            row.note("could not read a channel reference from the URL")
            return row

        ch = None
        if kind == "id":
            found = await self.api.channels([ref])
            ch = found[0] if found else None
        if ch is None:
            ch = await self.api.channel_by_handle(ref)

        if ch is None:
            row.status = "GONE"
            row.note("no such channel -- may already be taken down")
            return row

        self.fill(row, ch)
        uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get(
            "uploads", ""
        )
        if iso := await self.api.latest_upload(uploads):
            row.last_post_iso = iso
            row.posts_seen = "yes"
            row.mark("last_post", "api")
        elif (ch.get("statistics") or {}).get("videoCount") == "0":
            row.posts_seen = "no"
            row.mark("last_post", "api-no-videos")

        row.status = "OK" if row.profile_name else "PARTIAL"
        return row

    @staticmethod
    def fill(row: Row, ch: dict) -> None:
        snip = ch.get("snippet") or {}
        stats = ch.get("statistics") or {}

        row.profile_id = ch.get("id", "")
        row.url = CHANNEL_URL.format(cid=row.profile_id) if row.profile_id else row.url
        row.profile_name = (snip.get("title") or "").strip()
        row.mark("name", "api")
        row.name_score = name_score(row.profile_name, row.target)

        if (subs := stats.get("subscriberCount")) is not None:
            row.followers = int(subs)
            # YouTube rounds public subscriber counts to 3 significant figures
            row.followers_exact = "no" if stats.get("hiddenSubscriberCount") else "yes"
            row.mark("followers", "api")
        if stats.get("hiddenSubscriberCount"):
            row.note("subscriber count hidden by the channel")

        if published := snip.get("publishedAt"):
            row.created_iso = published[:10]
            row.mark("created", "api")
        if country := snip.get("country"):
            row.location = country
            row.mark("location", "api")

        thumbs = snip.get("thumbnails") or {}
        best = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
        if uri := best.get("url"):
            row.profile_pic_url = uri
            row.has_custom_pic = not bool(RE_DEFAULT_PIC.search(uri))
            row.mark("logo", "api")

        if (videos := stats.get("videoCount")) is not None:
            row.note(f"{int(videos):,} videos")

    # ─────────────────────────── orchestration ────────────────────────── #

    async def one(self, u: str, tgt: str, feed: str) -> Row:
        try:
            return await self.process(u, tgt, feed)
        except QuotaExceeded as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.status = "CHECKPOINT"  # stops the run, same as a challenge
            row.note(str(e))
            return row
        except Exception as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.status = "ERROR"
            row.note(f"{type(e).__name__}: {e}")
            return row

    @staticmethod
    def report(i: int, total: int, u: str, row: Row) -> None:
        print(f"[{i}/{total}] {u}", file=sys.stderr)
        print(
            f"    {row.status:<14} name={row.profile_name[:22]:<22} "
            f"created={fmt_created(row.created_iso) or '-':<10} "
            f"subs={row.followers if row.followers is not None else '-':<10} "
            f"active={row.active_yes or '-':<3} "
            f"risk={row.risk} {row.priority}",
            file=sys.stderr,
        )

    async def run(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        rows: list[Row] = []
        for i, (u, tgt, feed) in enumerate(jobs, 1):
            row = await self.one(u, tgt, feed)
            rows.append(row)
            self.report(i, len(jobs), u, row)
            if row.status == "CHECKPOINT":
                print("\nQUOTA EXHAUSTED -- stopping.", file=sys.stderr)
                break
        return rows
