"""X/Twitter analysis engine: validation and impersonation signal extraction
-- profile URL -> scored Row.

Session/login-checking and payload parsing (TwitterUser + friends) live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns everything specific to a validated profile visit: URL
normalization for the analysis entry point, and the browser-drive loop
(Scraper).

One request does it, usually: visiting a profile fires UserByScreenName, whose
`legacy` object holds every field the report wants as typed values, an
integer follower count rather than a rendered "154M", and a real join date.
So most fields never need the DOM, and unlike Facebook the Created Date
column is filled.

The one field that does fall back to the DOM is last-post date. It comes
from a SEPARATE query (UserTweets) than the profile one, and that query can
simply not have landed in the single ~1.2s window this waits for it, a
timing miss, not a parsing failure, confirmed live: two accounts stored with
no last-post date reproduced fine on a fresh visit. When that happens,
dom_last_post() reads the same information off the already-rendered
timeline instead of waiting longer or re-requesting.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from backend.shared.models.row import Row
from backend.shared.text import name_score, normalized_host, parse_normalized_url
from backend.platforms.twitter.discovery_engine import (RE_CHECKPOINT,
                                                         RE_GONE, RE_LOGIN,
                                                         TWEETS_QUERIES,
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
    p = parse_normalized_url(url)
    if p is None:
        return ""
    host = normalized_host(p)
    if "twitter.com" in host or "x.com" in host:
        host = "x.com"
    return f"https://{host}{p.path.rstrip('/')}"


def handle_of(url: str) -> str:
    seg = [s for s in urlparse(normalize_url(url)).path.split("/") if s]
    if not seg:
        return ""
    h = seg[0].lstrip("@")
    return "" if h.lower() in BAD_SEGMENTS else h


# DOM last-post fallback
# latest_post() (discovery_engine.py) reads the UserTweets GraphQL response,
# and deliberately excludes reposts and pinned tweets, counting either
# would make a dormant account look active. A DOM fallback has to preserve
# that same filtering, not just grab the newest <time> on the page: verified
# live on a real account, the single newest tweet CELL on screen was a
# repost, one day newer than that account's actual last original post.
# `[data-testid="socialContext"]` is the exact element X renders that
# repost badge in ("Adani Group reposted"), excluding any cell that has
# one reproduces latest_post()'s scoping.
#
# No live example of a currently-pinned tweet was available to confirm its
# exact socialContext text, so "pinned" is matched defensively (case
# -insensitive substring) alongside the confirmed "repost", an unmatched
# pinned tweet would only make this fallback occasionally too generous by
# one tweet, never wrong in the direction that hides real inactivity.
JS_TWEET_TIMES = """
() => {
  const out = [];
  for (const cell of document.querySelectorAll('[data-testid="tweet"]')) {
    const time = cell.querySelector('time[datetime]');
    if (!time) continue;
    const ctx = cell.querySelector('[data-testid="socialContext"]');
    const label = ctx ? (ctx.textContent || '').toLowerCase() : '';
    out.push({dt: time.getAttribute('datetime'), repostOrPinned: /repost|retweet|pinned/.test(label)});
  }
  return out;
}
"""


# How long to let a profile timeline paint before giving up on reading a
# date off it. Generous on purpose: the cost of waiting is a few seconds
# per profile, the cost of reading too early is a false "this account has
# no posts", which feeds the activity classification and the risk score.
_DOM_TIMELINE_WAIT_MS = 3000


async def dom_last_post(page) -> str:
    """Newest ORGANIC post date read off the already-rendered timeline.
    '' when nothing usable is on screen, never a guess."""
    try:
        cells = await page.evaluate(JS_TWEET_TIMES)
    except Exception:
        return ""
    dates = [
        c["dt"][:10] for c in (cells or [])
        if c.get("dt") and not c.get("repostOrPinned")
    ]
    return max(dates) if dates else ""


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
        self.evidence = args.evidence or None  # GridFS key prefix, not a path
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
                is_tweets = any(q in resp.url for q in TWEETS_QUERIES)
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
                # See instagram/analysis_engine.py: a row we could not
                # read is the one most worth having a picture of, and
                # returning before screenshot() threw that away.
                await self.screenshot(page, row)
                return row

            # the timeline query lands just after the profile one
            if not posts:
                # Wait for the timeline to actually paint, rather than a
                # flat 1.2s. Measured live: the profile query answers at
                # ~4.1s but the first tweet cells only exist at ~5.7s, so
                # a fixed sleep read an empty timeline for some profiles
                # and a full one for others -- exactly the "17 of 33 have
                # no date" pattern that looked like a parser break.
                try:
                    await page.wait_for_selector(
                        '[data-testid="tweet"]', timeout=_DOM_TIMELINE_WAIT_MS,
                    )
                except Exception:
                    # genuinely no tweets on screen (a zero-post account,
                    # or a protected/empty timeline) -- fall through, the
                    # DOM read below will return "" and the caller keeps
                    # posts_seen as it found it
                    pass
            self.fill(row, found[0])
            if posts:
                row.last_post_iso = max(posts)
                row.posts_seen = "yes"
                row.mark("last_post", "graphql")
            elif row.posts_seen != "no":
                # the UserTweets query (captured into `posts` above) missed
                # its window, fill() already determined "no posts at all"
                # is not the case (posts_seen != "no"), so read the same
                # information off the timeline that's already rendered on
                # screen instead of waiting longer or re-requesting
                iso = await dom_last_post(page)
                if iso:
                    row.last_post_iso = iso
                    row.posts_seen = "yes"
                    row.mark("last_post", "dom-time")
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
            row.verified = True
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
        # DETERMINISTIC key, no timestamp: re-analysing a profile must
        # overwrite its own previous capture, not add another one. With a
        # timestamp, a daily re-sweep left one PNG per profile per run in
        # the store forever, and the profile document only ever pointed at
        # the newest, every earlier one was unreachable garbage.
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", row.profile_id or "entity")[:60]
        key = f"{self.evidence}/{stem}.png"
        try:
            # See Session.wait_for_visible_content: field extraction here
            # comes from intercepted API responses, which can land well
            # before the page has visually painted anything, a screenshot
            # taken right after would capture the loading state, not the
            # profile.
            await self.session.wait_for_visible_content(page)
            data = await page.screenshot(full_page=False)
            from backend.database.repositories import evidence_repository
            await evidence_repository.save(key, data)
            row.screenshot = key
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
        from backend.shared.logging import get_logger as _gl
        _gl("platforms.twitter.analysis").info(
            f"[{i}/{total}] {u} | {row.status} name={row.profile_name[:22]} "
            f"followers={row.followers if row.followers is not None else '-'} "
            f"active={row.active_yes or '-'} risk={row.risk} {row.priority}"
        )

    async def run(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        rows: list[Row] = []
        for i, (u, tgt, feed) in enumerate(jobs, 1):
            row = await self.one(u, tgt, feed)
            rows.append(row)
            self.report(i, len(jobs), u, row)
            if row.status == "CHECKPOINT" and not getattr(self.a, "keep_going", False):
                from backend.shared.logging import get_logger as _gl
                _gl("platforms.twitter.analysis").warning(
                    "CHECKPOINT -- aborting to protect the session."
                )
                break
            if i < len(jobs):
                await self.pause()
        return rows
