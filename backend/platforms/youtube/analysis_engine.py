"""YouTube analysis engine: validation, channel URL -> scored Row, via the
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

from urllib.parse import unquote, urlparse

from backend.shared.models.row import Row
from backend.shared.text import name_score, normalized_host, parse_normalized_url
from backend.platforms.youtube.discovery_engine import (RE_DEFAULT_PIC,
                                                         QuotaExceeded,
                                                         YouTubeAPI)


def normalize_url(url: str) -> str:
    p = parse_normalized_url(url)
    if p is None:
        return ""
    host = normalized_host(p)
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
    if seg[0].startswith("UC") and len(seg[0]) >= 20:
        return "id", seg[0]
    return "handle", seg[0]


class Scraper:
    """Same surface as the browser scanners; no browser behind it."""

    normalize_url = staticmethod(normalize_url)

    def __init__(self, args, cookies=None, session_id: str = "", proxy=None):
        # API-key authed, no browser, session_id/proxy exist only so
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
        """False means the KEY ITSELF is conclusively rejected, the
        YouTube-API equivalent of a browser landing on a login wall.

        Daily quota exhaustion is NOT that: it is a normal, expected state
        (see discovery_engine.py's module docstring, 10,000 units/day,
        resets on its own) that says nothing about whether the key is
        valid. This used to return False for it too, which wrongly
        quarantined a perfectly good key and fired a SessionInvalid alert
        telling someone to replace credentials that were never the
        problem, every single day the quota happened to run out first.

        A network/timeout error during the check is likewise not evidence
        the key is bad, and is left to propagate as a raised exception
        (via classify_failure's `None` result below) rather than being
        swallowed into "rejected", same conclusive-vs-inconclusive
        contract every other platform's check_session follows (see
        stealth/browser.py's docstring); the caller
        (sessions/manager.py::verify_session_item) treats a raised
        exception as inconclusive and leaves the session's status
        untouched instead of recording a blip as "this key is now dead."
        """
        from backend.shared.logging import get_logger as _gl
        from backend.shared.resilience import classify_failure
        _log = _gl("platforms.youtube.analysis")
        try:
            await self.api.get("channels", part="id", forHandle="@youtube")
            _log.info("SESSION: API key valid")
            return True
        except QuotaExceeded as e:
            _log.info(f"SESSION: quota exhausted for today -- key itself still valid ({e})")
            return True
        except Exception as e:
            reason = classify_failure(e)
            if reason in ("expired", "checkpointed"):
                _log.warning(f"SESSION: API key rejected -- {e}")
                return False
            # rate_limited (a 429/"too many requests" distinct from daily
            # quota), or unclassified/transient, not conclusive evidence
            # the key itself is bad
            raise

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
        row.profile_name = (
            snip.get("title")
            or snip.get("channelTitle")
            or (ch.get("brandingSettings") or {}).get("channel", {}).get("title")
            or snip.get("customUrl")
            or ""
        ).strip()
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
        from backend.shared.logging import get_logger as _gl
        _gl("platforms.youtube.analysis").info(
            f"[{i}/{total}] {u} | {row.status} name={row.profile_name[:22]} "
            f"subs={row.followers if row.followers is not None else '-'} "
            f"active={row.active_yes or '-'} risk={row.risk} {row.priority}"
        )

    async def run(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        rows: list[Row] = []
        for i, (u, tgt, feed) in enumerate(jobs, 1):
            row = await self.one(u, tgt, feed)
            rows.append(row)
            self.report(i, len(jobs), u, row)
            if row.status == "CHECKPOINT":
                from backend.shared.logging import get_logger as _gl
                _gl("platforms.youtube.analysis").warning("QUOTA EXHAUSTED -- stopping.")
                break
        return rows
