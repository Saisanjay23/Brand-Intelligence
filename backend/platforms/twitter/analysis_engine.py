"""X/Twitter analysis engine: validation and impersonation signal extraction
-- profile URL -> scored Row.

Session/login-checking and payload parsing (TwitterUser + friends) live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns everything specific to a validated profile visit: URL
normalization for the analysis entry point, and the browser-drive loop
(Scraper).

One request does it: visiting a profile fires UserByScreenName, whose `legacy`
object holds every field the report wants as typed values -- an integer
follower count rather than a rendered "154M", and a real join date. So this
never scrapes the DOM, and unlike Facebook the Created Date column is filled.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from backend.models.row import Row
from backend.utils.text import fmt_created, name_score
from backend.platforms.twitter.discovery_engine import (RE_CHECKPOINT,
                                                         RE_GONE, RE_LOGIN,
                                                         TWEETS_QUERY,
                                                         USER_QUERIES,
                                                         TwitterSession,
                                                         TwitterUser,
                                                         iter_users,
                                                         latest_post,
                                                         parse_lines)

BAD_SEGMENTS = {
    "home",
    "search",
    "explore",
    "notifications",
    "messages",
    "i",
    "settings",
    "compose",
    "intent",
}


def normalize_url(url: str) -> str:
    url = (url or "").strip().strip("\"'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if "twitter.com" in host or "x.com" in host:
        host = "x.com"
    return f"https://{host}{p.path.rstrip('/')}"


def handle_of(url: str) -> str:
    seg = [s for s in urlparse(normalize_url(url)).path.split("/") if s]
    if not seg:
        return ""
    h = seg[0].lstrip("@")
    return "" if h.lower() in BAD_SEGMENTS else h


class Scraper:
    """One logged-in X session, driven over a list of profiles."""

    normalize_url = staticmethod(normalize_url)

    def __init__(
        self,
        args,
        cookies: list[dict],
        session_id: str = "",
        proxy: dict | None = None,
    ):
        self.a = args
        self.evidence = Path(args.evidence) if args.evidence else None
        if self.evidence:
            self.evidence.mkdir(parents=True, exist_ok=True)
        self.session = TwitterSession(
            args,
            cookies,
            load_images=bool(self.evidence),
            session_id=session_id,
            proxy=proxy,
        )

    @property
    def ctx(self):
        return self.session.ctx

    async def start(self):
        await self.session.start()

    async def stop(self):
        await self.session.stop()

    async def pause(self, mult: float = 1.0):
        await self.session.pause(mult)

    async def check_session(self) -> bool:
        return await self.session.check_session()

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = handle_of(url)

        page = await self.ctx.new_page()
        found: list[TwitterUser] = []
        posts: list[str] = []
        got = asyncio.Event()
        wanted = row.profile_id.lower()

        async def on_response(resp):
            try:
                is_user = any(q in resp.url for q in USER_QUERIES)
                is_tweets = TWEETS_QUERY in resp.url
                if not (is_user or is_tweets):
                    return
                text = await resp.text()
            except Exception:
                return
            for blob in parse_lines(text):
                if is_user:
                    for u in iter_users(blob):
                        # a profile page also loads other users (who to follow);
                        # only the one whose handle matches is this profile
                        if not wanted or u.handle.lower() == wanted:
                            found.append(u)
                            got.set()
                if is_tweets:
                    ident = found[0] if found else None
                    if iso := latest_post(
                        blob, wanted, ident.entity_id if ident else ""
                    ):
                        posts.append(iso)

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            try:
                await page.goto(
                    f"{url}?lang=en",
                    wait_until="domcontentloaded",
                    timeout=self.a.timeout * 1000,
                )
            except Exception:
                row.status = "ERROR"
                row.note("navigation failed")
                return row

            # the profile query fires within a second of the document loading
            try:
                await asyncio.wait_for(got.wait(), timeout=self.a.settle)
            except asyncio.TimeoutError:
                pass

            body = ""
            try:
                body = await page.inner_text("body")
            except Exception:
                pass

            if not found:
                if RE_CHECKPOINT.search(body):
                    row.status = "CHECKPOINT"
                    row.note("session checkpointed")
                elif (
                    "/login" in page.url
                    or "/i/flow/login" in page.url
                    or RE_LOGIN.search(body)
                ):
                    row.status = "LOGIN_REQUIRED"
                    row.note("cookies rejected/expired")
                elif RE_GONE.search(body):
                    row.status = "GONE"
                    row.note("suspended or does not exist -- may already be down")
                else:
                    row.status = "PARTIAL"
                    row.note("profile payload not seen")
                return row

            # the timeline query lands just after the profile one
            if not posts:
                try:
                    await page.wait_for_timeout(1200)
                except Exception:
                    pass
            self.fill(row, found[0])
            if posts:
                row.last_post_iso = max(posts)
                row.posts_seen = "yes"
                row.mark("last_post", "graphql")
            await self.screenshot(page, row)
            row.status = "OK" if row.profile_name else "PARTIAL"
            return row
        finally:
            try:
                await page.close()
            except Exception:
                pass

    @staticmethod
    def fill(row: Row, u: TwitterUser) -> None:
        """Every field from one payload, each tagged with where it came from."""
        row.profile_id = u.entity_id or u.handle
        row.profile_name = u.name
        row.mark("name", "graphql")
        row.name_score = name_score(u.name, row.target)

        if u.followers is not None:
            row.followers = u.followers
            row.followers_exact = "yes"  # a typed integer, never rounded
            row.mark("followers", "graphql")
        if u.following is not None:
            row.friends = u.following
            row.mark("friends", "graphql")
        if u.created_iso:
            row.created_iso = u.created_iso
            row.mark("created", "graphql")
        if u.location:
            row.location = u.location
            row.mark("location", "graphql")
        if u.avatar:
            row.profile_pic_url = u.avatar
            row.has_custom_pic = u.has_custom_pic
            row.mark("logo", "graphql")
        if u.verified:
            row.note("verified account")
        if u.protected:
            row.note("protected account -- posts not visible")

        # X exposes no last-post date on the profile payload; posts_count is the
        # honest activity signal, and "no posts at all" is a real answer
        if u.posts is not None:
            row.mark("posts", "graphql")
            if u.posts == 0:
                row.posts_seen = "no"
            else:
                row.note(f"{u.posts:,} posts")

    async def screenshot(self, page, row: Row) -> None:
        if not self.evidence:
            return
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", row.profile_id or "entity")[:60]
        shot = self.evidence / f"{stem}_{int(time.time())}.png"
        try:
            await page.screenshot(path=str(shot))
            row.screenshot = str(shot)
        except Exception:
            pass

    # ─────────────────────────── orchestration ────────────────────────── #

    async def one(self, u: str, tgt: str, feed: str) -> Row:
        try:
            return await self.process(u, tgt, feed)
        except Exception as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.profile_id = handle_of(row.url)
            row.status = "ERROR"
            row.note(f"{type(e).__name__}: {e}")
            return row

    @staticmethod
    def report(i: int, total: int, u: str, row: Row) -> None:
        print(f"[{i}/{total}] {u}", file=sys.stderr)
        print(
            f"    {row.status:<14} name={row.profile_name[:22]:<22} "
            f"created={fmt_created(row.created_iso) or '-':<10} "
            f"followers={row.followers if row.followers is not None else '-':<9} "
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
            if row.status == "CHECKPOINT" and not getattr(self.a, "keep_going", False):
                print(
                    "\nCHECKPOINT -- aborting to protect the session.", file=sys.stderr
                )
                break
            if i < len(jobs):
                await self.pause()
        return rows
