"""Instagram analysis engine: validation and impersonation signal extraction
-- profile URL -> scored Row.

Session/login-checking and payload parsing (InstagramUser + friends) live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns everything specific to a validated profile visit: URL
normalization, the DOM-header fallback, and the browser-drive loop (Scraper).

Visiting a profile *was designed* to fire `users/web_profile_info` and read its
counts, avatar and newest posts from the payload directly. Verified against two
real, very-active accounts, that assumption is wrong: the current web client
never issues that call for a logged-in view of someone else's profile. The
only two GraphQL queries that fire are post-timeline and feed queries, neither
of which carries the profile header (followers/following/name). Network
interception is kept below because it may still fire for other account or
view types, but it has not been observed to.

What actually IS reliable, confirmed on both accounts: the header numbers
render straight into the page ("685M followers", "8,534 posts") in a fixed,
stable order -- username, full name, posts, followers, following. That DOM
read is the primary source now; network interception is the fallback that
almost never answers.

The avatar carries a conventional `alt="<username>'s profile picture"`, but
Instagram unmounts that <img> entirely when its fetch is blocked -- so the
logo/avatar fields only populate when images are allowed to load, i.e. with
--evidence set (the same posture Facebook already uses). Without --evidence
they stay blank rather than guessed: a page-wide search for *any* avatar-like
image or string was tried and rejected, because Instagram embeds the
session's OWN viewer avatar on every page ("PolarisViewer"), and an unscoped
match silently attributed the analyst's own photo to whichever profile was
being scored.

LAST-POST DATE: the header gives a post COUNT, not a date, and re-verified
live (2026-07-27, against a real active account) that network interception
still does not fire the target's own profile/timeline payload -- the two
GraphQL calls that DO fire are the viewer's own SSO/credentials request and
the viewer's own home-feed recommendations, neither naming the profile being
scored. What DOES work: the profile's own most recent post/reel page renders
a real `<time datetime="...">` element (confirmed live: an exact UTC
timestamp, not a relative guess). `read_last_post_date()` below is one extra
page visit -- to the first post link already sitting in the grid DOM -- to
read that element directly. Skipped for private accounts and accounts with
no posts, where there is nothing to visit.

NOT COLLECTED at all: creation date. It lives behind the interactive "About
this account" panel, so that column stays blank rather than guessed.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from backend.models.row import Row
from backend.utils.text import fmt_created, name_score, parse_count
from backend.platforms.instagram.discovery_engine import (DEFAULT_PIC_HINTS,
                                                           PROFILE_ENDPOINTS,
                                                           RE_CHECKPOINT,
                                                           RE_GONE, RE_LOGIN,
                                                           InstagramSession,
                                                           InstagramUser,
                                                           parse_lines,
                                                           profile_from)

BAD_SEGMENTS = {"p", "reel", "reels", "explore", "stories", "accounts", "direct", "tv"}


def normalize_url(url: str) -> str:
    url = (url or "").strip().strip("\"'")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if "instagram" in host:
        host = "www.instagram.com"
    path = p.path.rstrip("/")
    return f"https://{host}{path}/" if path else f"https://{host}/"


def username_of(url: str) -> str:
    seg = [s for s in urlparse(normalize_url(url)).path.split("/") if s]
    if not seg:
        return ""
    u = seg[0].lstrip("@")
    return "" if u.lower() in BAD_SEGMENTS else u


class Scraper:
    """One logged-in Instagram session, driven over a list of profiles."""

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
        self.session = InstagramSession(
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

    # ─────────────────────────── DOM fallback ─────────────────────────── #

    # Confirmed on two unrelated real accounts: the header always renders as
    # username, full name, "N posts", "N followers", "N following", bio -- in
    # that fixed order -- so the name is simply "whatever precedes the posts
    # line" rather than a guess at a CSS class that Instagram will rename.
    #
    # The avatar is intentionally scoped by an exact alt="<username>'s profile
    # picture" match, not a loose selector. A loose page-wide search for any
    # image or JSON string that looks like an avatar was tried and rejected:
    # Instagram embeds the VIEWER's own avatar (whoever the session cookies
    # belong to, under a "PolarisViewer" block) on every single page, and an
    # unscoped match returns that one, silently attributing the analyst's own
    # photo to whatever profile is being scored. Exact alt-matching only
    # returns the header photo belonging to the account actually being viewed.
    JS_HEADER = """
    (username) => {
      const lines = (document.body.innerText || "").split("\\n")
        .map(s => s.trim()).filter(Boolean);
      let name = "", posts = "", followers = "", following = "";
      for (let i = 0; i < lines.length; i++) {
        if (/^[\\d][\\d,.]*[KMB]?\\s*posts$/i.test(lines[i])) {
          posts = lines[i];
          followers = lines[i + 1] || "";
          following = lines[i + 2] || "";
          if (i > 0) name = lines[i - 1];
          break;
        }
      }
      const img = document.querySelector(
        `header img[alt="${username}'s profile picture"]`);
      const verified = !!document.querySelector('header svg[aria-label="Verified"]');
      const bodyText = document.body.innerText || "";
      return {
        name, posts, followers, following,
        avatar: img ? (img.src || "") : "",
        verified,
        isPrivate: /this account is private/i.test(bodyText),
      };
    }
    """

    async def read_dom(self, page, username: str) -> dict:
        try:
            return await page.evaluate(self.JS_HEADER, username) or {}
        except Exception:
            return {}

    # The grid's own post/reel links are in feed order (newest first), so
    # the first one found is the most recent post -- no API needed to know
    # that ordering, it's just how the profile page renders.
    JS_FIRST_POST_LINK = """
    () => {
      const a = document.querySelector('a[href*="/p/"], a[href*="/reel/"]');
      return a ? a.getAttribute('href') : null;
    }
    """

    JS_POST_TIME = """
    () => {
      const t = document.querySelector('time[datetime]');
      return t ? t.getAttribute('datetime') : null;
    }
    """

    async def read_last_post_date(self, page, private: bool, has_posts: bool) -> str:
        """One extra page visit -- to the profile's own most recent post/reel
        -- for a real last-post DATE. Confirmed live: that page renders a
        `<time datetime="2026-07-23T16:00:21.000Z">` element with an exact
        UTC timestamp, unlike the profile header (a post COUNT only) and
        unlike network interception (does not fire the target's own profile
        payload -- see module docstring). Returns "" on anything short of a
        clean read: a private/postless account, a missing link, a failed
        navigation, or a page with no <time> element -- never a guess.
        """
        if private or not has_posts:
            return ""
        try:
            href = await page.evaluate(self.JS_FIRST_POST_LINK)
            if not href:
                return ""
            await page.goto(
                f"https://www.instagram.com{href}",
                wait_until="domcontentloaded",
                timeout=self.a.timeout * 1000,
            )
            await page.wait_for_timeout(1500)
            iso = await page.evaluate(self.JS_POST_TIME)
            # the element's own datetime attribute is already a UTC ISO
            # string ("...T...Z") -- the date is just its first 10 characters,
            # no parsing needed
            return iso[:10] if iso and len(iso) >= 10 else ""
        except Exception:
            return ""

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = username_of(url)

        page = await self.ctx.new_page()
        found: list[InstagramUser] = []
        got = asyncio.Event()

        async def on_response(resp):
            try:
                if not any(e in resp.url for e in PROFILE_ENDPOINTS):
                    return
                text = await resp.text()
            except Exception:
                return
            for blob in parse_lines(text):
                if user := profile_from(blob, row.profile_id):
                    found.append(user)
                    got.set()

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000
                )
            except Exception:
                row.status = "ERROR"
                row.note("navigation failed")
                return row

            try:
                await asyncio.wait_for(got.wait(), timeout=self.a.settle)
            except asyncio.TimeoutError:
                pass

            body = ""
            try:
                body = await page.inner_text("body")
            except Exception:
                pass

            private = False
            if found:
                # prefer the richest payload seen: later ones can be partial
                best = max(
                    found,
                    key=lambda u: (u.followers is not None, bool(u.last_post_iso)),
                )
                self.fill(row, best)
                private = best.private
            else:
                if RE_CHECKPOINT.search(body) or "challenge" in page.url:
                    row.status = "CHECKPOINT"
                    row.note("session checkpointed")
                    return row
                if "/accounts/login" in page.url or RE_LOGIN.search(body):
                    row.status = "LOGIN_REQUIRED"
                    row.note("cookies rejected/expired")
                    return row
                if RE_GONE.search(body):
                    row.status = "GONE"
                    row.note("removed or unavailable -- may already be down")
                    return row

                # the payload interception has not been observed to fire in
                # practice (see module docstring) -- read the rendered header
                dom = await self.read_dom(page, row.profile_id)
                if dom.get("posts") or dom.get("followers") or dom.get("name"):
                    self.fill_from_dom(row, dom)
                    private = bool(dom.get("isPrivate"))
                else:
                    row.status = "PARTIAL"
                    row.note("profile payload not seen")
                    return row

            if not row.last_post_iso:
                last_post = await self.read_last_post_date(
                    page, private, row.posts_seen != "no"
                )
                if last_post:
                    row.last_post_iso = last_post
                    row.mark("last_post", "post-page")

            await self.screenshot(page, row)
            row.status = "OK" if row.profile_name or row.profile_id else "PARTIAL"
            return row
        finally:
            try:
                await page.close()
            except Exception:
                pass

    @staticmethod
    def fill(row: Row, u: InstagramUser) -> None:
        row.profile_id = u.entity_id or u.username
        # the display name is what an impersonator copies; fall back to handle
        row.profile_name = u.full_name or u.username
        row.mark("name", "api")
        row.name_score = name_score(row.profile_name, row.target)

        if u.followers is not None:
            row.followers = u.followers
            row.followers_exact = "yes"
            row.mark("followers", "api")
        if u.following is not None:
            row.friends = u.following
            row.mark("friends", "api")
        if u.avatar:
            row.profile_pic_url = u.avatar
            row.has_custom_pic = u.has_custom_pic
            row.mark("logo", "api")
        if u.last_post_iso:
            row.last_post_iso = u.last_post_iso
            row.posts_seen = "yes"
            row.mark("last_post", "api")
        elif u.posts == 0:
            row.posts_seen = "no"
            row.mark("last_post", "api-no-posts")
        if u.verified:
            row.note("verified account")
        if u.private:
            row.note("private account -- posts not visible")
        row.note("creation date not exposed by Instagram")

    @staticmethod
    def fill_from_dom(row: Row, dom: dict) -> None:
        """Same fields as fill(), read from the rendered header instead.

        The header itself gives a post COUNT, not a date, so `posts_seen`
        is set here for read_last_post_date() to act on (see process()) --
        the actual date comes from that separate post-page visit, not from
        this header read.
        """
        def count(field: str, word: str) -> tuple[Optional[int], bool]:
            m = re.match(rf"^([\d][\d,.]*[KMB]?)\s*{word}\b", dom.get(field, ""), re.I)
            return parse_count(m.group(1)) if m else (None, False)

        name = (dom.get("name") or "").strip()
        if name:
            row.profile_name = name
            row.mark("name", "dom-header")
            row.name_score = name_score(name, row.target)

        val, exact = count("followers", "followers")
        if val is not None:
            row.followers = val
            row.followers_exact = "yes" if exact else "no"
            row.mark("followers", "dom-header")

        val, _ = count("following", "following")
        if val is not None:
            row.friends = val
            row.mark("friends", "dom-header")

        val, _ = count("posts", "posts")
        if val is not None:
            row.posts_seen = "yes" if val > 0 else "no"
            row.mark("posts", "dom-header")

        avatar = dom.get("avatar") or ""
        if avatar:
            row.profile_pic_url = avatar
            row.has_custom_pic = not any(h in avatar for h in DEFAULT_PIC_HINTS)
            row.mark("logo", "dom-header")

        if dom.get("verified"):
            row.note("verified account")
        if dom.get("isPrivate"):
            row.note("private account -- posts not visible")
        row.note("creation date not exposed by Instagram")

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
            row.profile_id = username_of(row.url)
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
