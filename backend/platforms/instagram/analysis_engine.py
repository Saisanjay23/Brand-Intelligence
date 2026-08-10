"""Instagram analysis engine: validation and impersonation signal extraction
-- profile URL -> scored Row.

Session/login-checking and payload parsing (InstagramUser + friends) live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns everything specific to a validated profile visit: URL
normalization, the DOM-header fallback, and the browser-drive loop (Scraper).

Visiting a profile *was designed* to fire `users/web_profile_info` and read its
counts, avatar and newest posts from the payload directly. Verified against two
real, very-active accounts, that assumption is wrong: the current web client
never issues that call passively for a logged-in view of someone else's
profile. So this now asks for it directly instead of waiting: `fetch_via_api()`
below calls the exact same private mobile endpoint discovery_engine.py's
search sweep already uses successfully (`PROFILE_INFO_API`, a sibling of
`MOBILE_SEARCH_API`), the same way, with the same headers -- a plain
authenticated HTTP request via `ctx.request`, not a page navigation. That
sidesteps the passive-interception dead end entirely, and since it's a raw
JSON response rather than a rendered page, it does not depend on whether
images are allowed to load in the browser -- fixing the logo/avatar field
being blank by default (see below). Passive network interception is kept as
a second-chance source in case the direct call is ever rate-limited or the
endpoint returns nothing for a particular account, and DOM reading remains
the last resort.

What's reliable when both the API call and interception come up empty: the
header numbers render straight into the page ("685M followers", "8,534
posts") in a fixed, stable order -- username, full name, posts, followers,
following. That DOM read is the final fallback.

The avatar carries a conventional `alt="<username>'s profile picture"` in the
DOM fallback path, but Instagram unmounts that <img> entirely when its fetch
is blocked -- so before this change, the logo/avatar field only populated via
DOM when images were allowed to load, i.e. with --evidence set (the same
posture Facebook already uses). The direct API call above does not have this
problem (it's JSON, not a rendered image), so the logo/avatar field now
populates from a normal analysis run too, not only an --evidence one. The DOM
fallback still requires --evidence, and its avatar match stays intentionally
scoped by an exact alt="<username>'s profile picture", not a loose selector:
a page-wide search for *any* avatar-like image or string was tried and
rejected, because Instagram embeds the session's OWN viewer avatar on every
page ("PolarisViewer"), and an unscoped match silently attributed the
analyst's own photo to whichever profile was being scored.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from backend.shared.models.row import Row
from backend.shared.text import (MONTHS, fmt_created, name_score,
                                   normalized_host, parse_count,
                                   parse_normalized_url)
from backend.platforms.instagram.discovery_engine import (DEFAULT_PIC_HINTS,
                                                           MOBILE_UA,
                                                           PROFILE_ENDPOINTS,
                                                           PROFILE_INFO_API,
                                                           RE_CHECKPOINT,
                                                           RE_GONE, RE_LOGIN,
                                                           InstagramSession,
                                                           InstagramUser,
                                                           parse_lines,
                                                           profile_from)

BAD_SEGMENTS = {"p", "reel", "reels", "explore", "stories", "accounts", "direct", "tv"}


def normalize_url(url: str) -> str:
    p = parse_normalized_url(url)
    if p is None:
        return ""
    host = normalized_host(p)
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

    # ─────────────────────────── direct API call ───────────────────────── #

    async def fetch_via_api(self, username: str) -> Optional[InstagramUser]:
        """Ask Instagram's own profile-info endpoint directly, the same
        request discovery_engine.py's search sweep already makes
        successfully (PROFILE_INFO_API, a sibling of MOBILE_SEARCH_API) --
        rather than waiting for the browser's own JS to fire it passively,
        which it no longer does for a logged-in view of someone else's
        profile (see module docstring). A plain authenticated HTTP call, so
        unlike the DOM fallback it does not depend on whether images are
        allowed to load in the browser. Returns None on anything short of a
        clean parse -- callers fall through to interception/DOM."""
        try:
            res = await self.ctx.request.get(
                PROFILE_INFO_API.format(u=quote(username)),
                headers={
                    "User-Agent": MOBILE_UA,
                    "x-ig-app-id": "936619743392459",
                    "accept": "application/json",
                },
                timeout=self.a.timeout * 1000,
            )
            if res.status != 200:
                return None
            text = await res.text()
        except Exception:
            return None
        for blob in parse_lines(text):
            if user := profile_from(blob, username):
                return user
        return None

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

    # The grid's own post/reel links are NOT reliably newest-first --
    # confirmed live (adanifoundationschools, 2026-08-10): the first three
    # tiles were all dated 2025-09-01 while a genuinely newer post sat in
    # 4th position -- Instagram's "pin to grid" feature, which holds up to
    # 3 posts at the top regardless of date. No pin marker is visible in a
    # third party's view of the DOM at all (checked the full ancestor chain
    # of the pinned tiles up 4 levels -- no icon, no aria-label, nothing to
    # key off of the way Twitter's TimelinePinEntry or a "Pinned" badge
    # would give), so this can't be fixed by detecting "is this one
    # pinned." Fixed the same way underneath as Twitter/Facebook were,
    # though: read several candidates and take the real max instead of
    # trusting grid position.
    JS_GRID_ALT_DATES = """
    () => {
      const out = [];
      for (const a of document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]')) {
        const img = a.querySelector('img[alt]');
        if (img) out.push(img.getAttribute('alt') || '');
      }
      return out.slice(0, 12);
    }
    """

    # Instagram's own pin cap is 3 -- visiting this many candidate links
    # guarantees at least one genuinely-newest, non-pinned post is checked
    # regardless of how many (0 to 3) are actually pinned right now.
    JS_GRID_POST_LINKS = """
    () => Array.from(document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]'))
      .slice(0, 3).map(a => a.getAttribute('href'))
    """

    JS_POST_TIME = """
    () => {
      const t = document.querySelector('time[datetime]');
      return t ? t.getAttribute('datetime') : null;
    }
    """

    # "Photo by X on September 01, 2025." / "...on August 09, 2026. May be
    # an image..." / "Photo shared by X on August 08, 2026 tagging @Y."
    # -- confirmed live across several accounts, always this "on <Month>
    # <Day>, <Year>" shape for a PHOTO post's own accessibility alt text.
    # Reels carry only their caption as alt text, no date -- yields nothing
    # here, which is why tier 2 below still exists.
    _RE_ALT_DATE = re.compile(r"\bon\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\b")

    @classmethod
    def _parse_alt_date(cls, text: str) -> str:
        m = cls._RE_ALT_DATE.search(text or "")
        if not m:
            return ""
        mon_name, day, year = m.groups()
        month = next(
            (i for i, name in enumerate(MONTHS, start=1)
             if name.lower() == mon_name.lower()),
            None,
        )
        if not month:
            return ""
        try:
            dt = datetime(int(year), month, int(day), tzinfo=timezone.utc)
        except ValueError:
            return ""
        now = datetime.now(timezone.utc)
        # Instagram launched in 2010; a stamp before that or in the future
        # is not a real post date
        return dt.date().isoformat() if 2010 <= dt.year and dt <= now else ""

    async def read_last_post_date(self, page, private: bool, has_posts: bool) -> str:
        """The real last-post date, robust to grid pinning (see
        JS_GRID_ALT_DATES' comment above for the live-confirmed gap this
        closes).

        Tier 1, free -- no extra navigation: every currently-rendered grid
        tile's own photo already carries its publish date in its
        accessibility alt text. Reading every tile (not just the first)
        and taking the real max is what survives pinning, at zero added
        cost over the page visit already made to reach this profile.

        Tier 2, up to 3 extra page visits -- only when tier 1 found no
        parseable date at all (an all-Reels account, most often). Visits
        the first 3 grid links -- Instagram's own pin cap -- and reads each
        one's real `<time datetime>` element directly, taking the max.
        Confirmed live: that page renders a
        `<time datetime="2026-07-23T16:00:21.000Z">` element with an exact
        UTC timestamp.

        Returns "" on anything short of a clean read: a private/postless
        account, no candidates, or failed navigations -- never a guess.
        """
        if private or not has_posts:
            return ""

        try:
            alts = await page.evaluate(self.JS_GRID_ALT_DATES) or []
        except Exception:
            alts = []
        dates = [d for d in (self._parse_alt_date(a) for a in alts) if d]
        if dates:
            return max(dates)

        try:
            hrefs = await page.evaluate(self.JS_GRID_POST_LINKS) or []
        except Exception:
            hrefs = []
        found: list[str] = []
        for href in hrefs:
            if not href:
                continue
            try:
                await page.goto(
                    f"https://www.instagram.com{href}",
                    wait_until="domcontentloaded",
                    timeout=self.a.timeout * 1000,
                )
                await page.wait_for_timeout(1500)
                iso = await page.evaluate(self.JS_POST_TIME)
                # the element's own datetime attribute is already a UTC ISO
                # string ("...T...Z") -- the date is just its first 10
                # characters, no parsing needed
                if iso and len(iso) >= 10:
                    found.append(iso[:10])
            except Exception:
                continue
        return max(found) if found else ""

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = username_of(url)

        # Try the direct API call first (see fetch_via_api's docstring) --
        # independent of the page visit below, so it costs nothing extra
        # even when it comes up empty and we fall through to interception/DOM.
        api_user = await self.fetch_via_api(row.profile_id) if row.profile_id else None

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

            # No need to wait for passive interception if the direct API
            # call already got us a usable result.
            if api_user is None:
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
            candidates = ([api_user] if api_user else []) + found
            if candidates:
                # prefer the richest payload seen: the direct API call is
                # usually best (see fetch_via_api), but a later interception
                # catch can still be more complete for a given account.
                best = max(
                    candidates,
                    key=lambda u: (u.followers is not None, bool(u.avatar), bool(u.last_post_iso)),
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
            row.verified = True
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
            row.verified = True
            row.note("verified account")
        if dom.get("isPrivate"):
            row.note("private account -- posts not visible")
        row.note("creation date not exposed by Instagram")

    async def screenshot(self, page, row: Row) -> None:
        if not self.evidence:
            return
        # DETERMINISTIC filename, no timestamp: re-analysing a profile must
        # overwrite its own previous capture, not add another one. With a
        # timestamp, a daily re-sweep left one PNG per profile per run on
        # disk forever, and the profile document only ever pointed at the
        # newest -- every earlier file was unreachable garbage.
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", row.profile_id or "entity")[:60]
        shot = self.evidence / f"{stem}.png"
        try:
            await page.screenshot(path=str(shot), full_page=False)
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
        from backend.shared.logging import get_logger as _gl
        _gl("platforms.instagram.analysis").info(
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
                _gl("platforms.instagram.analysis").warning(
                    "CHECKPOINT -- aborting to protect the session."
                )
                break
            if i < len(jobs):
                await self.pause()
        return rows
