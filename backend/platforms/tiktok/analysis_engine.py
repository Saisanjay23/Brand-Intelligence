"""TikTok analysis engine: validation and impersonation signal extraction
-- profile URL -> scored Row.

Session/login-checking and payload parsing (TikTokUser + friends) live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns everything specific to a validated profile visit: URL
normalization, the DOM-header fallback, and the browser-drive loop
(Scraper).

Same extraction order as discovery's search sweep, and for the same reason
(see discovery_engine.py's module docstring): TikTok's own hydration script
first (same page load, no XHR race), then the rendered header text, in that
order. NEEDS LIVE VERIFICATION, see that same docstring.

LAST-POST DATE, in order of preference:
  1. The newest video's id, decoded straight from the id itself (see
     discovery_engine.py::_video_id_to_iso), free, no extra navigation,
     but the profile hydration payload's own `itemList` is confirmed
     (live, 2026-08-11) to always arrive empty, so this tier realistically
     never fires today. Kept because it costs nothing to try and would
     start working for free if TikTok ever changes that.
  2. `newest_post_via_search`, one extra page visit, searching the
     account's own username on the Videos tab and reading the real
     `createTime` TikTok's search response carries. This exists because
     the obvious alternative, visiting the profile's OWN video grid --
     is live-confirmed to trip TikTok's slide-verify CAPTCHA challenge
     (repeatedly, across a clean session with no prior automated visits);
     this backend will never automate solving that, on principle, not
     just because it's hard. Search never triggered the challenge in
     testing. Not exhaustive (search relevance, not a full post history),
     so a miss here is not evidence of inactivity, see that function's
     own docstring.

NOT COLLECTED: account creation date. Not exposed anywhere on a profile
page's own rendered surface or (per public write-ups) its hydration
payload, so that column stays blank rather than guessed.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from backend.shared.models.row import Row
from backend.shared.text import name_score, normalized_host, parse_count, parse_normalized_url
from backend.platforms.tiktok.discovery_engine import (RE_CHECKPOINT, RE_GONE,
                                                        RE_LOGIN, TikTokSession,
                                                        TikTokUser, newest_post_iso,
                                                        newest_post_via_search,
                                                        profile_from, read_hydration)

BAD_SEGMENTS = {
    "video", "live", "tag", "music", "discover", "explore", "upload",
    "messages", "following", "notification", "search", "foryou", "login",
}


def normalize_url(url: str) -> str:
    p = parse_normalized_url(url)
    if p is None:
        return ""
    host = normalized_host(p)
    if "tiktok" in host:
        host = "www.tiktok.com"
    path = p.path.rstrip("/")
    return f"https://{host}{path}" if path else f"https://{host}"


def username_of(url: str) -> str:
    seg = [s for s in urlparse(normalize_url(url)).path.split("/") if s]
    if not seg:
        return ""
    u = seg[0].lstrip("@")
    return "" if u.lower() in BAD_SEGMENTS else u


class Scraper:
    """One logged-in TikTok session, driven over a list of profiles."""

    normalize_url = staticmethod(normalize_url)

    def __init__(
        self, args, cookies: list[dict], session_id: str = "", proxy: Optional[dict] = None,
    ):
        self.a = args
        self.evidence = args.evidence or None  # GridFS key prefix, not a path
        self.session = TikTokSession(
            args, cookies, load_images=bool(self.evidence), session_id=session_id, proxy=proxy,
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
    #
    # TikTok's profile header renders, in a fixed order, the account's
    # nickname, then three counts labelled by the word that follows each
    # one ("Following" / "Followers" / "Likes"), reading by label text
    # rather than position or a CSS class survives a layout change the way
    # Instagram's own fixed-order header parse does.
    JS_HEADER = """
    (username) => {
      const lines = (document.body.innerText || "").split("\\n")
        .map(s => s.trim()).filter(Boolean);
      let following = "", followers = "", likes = "";
      for (let i = 1; i < lines.length; i++) {
        if (/^following$/i.test(lines[i])) following = lines[i - 1];
        else if (/^followers$/i.test(lines[i])) followers = lines[i - 1];
        else if (/^likes$/i.test(lines[i])) likes = lines[i - 1];
      }
      const h = document.querySelector('h1, h2');
      const nickname = h ? (h.textContent || '').trim() : '';
      const img = document.querySelector(
        `img[alt*="${username}"], header img, img[class*="avatar" i]`);
      const verified = !!document.querySelector('[data-e2e="user-verified"], svg[aria-label="Verified"]');
      const bodyText = document.body.innerText || "";
      return {
        following, followers, likes, nickname,
        avatar: img ? (img.getAttribute('src') || '') : '',
        verified,
        isPrivate: /this account is private/i.test(bodyText),
        notFound: /couldn.t find this account|this account can.t be found/i.test(bodyText),
      };
    }
    """

    async def read_dom(self, page, username: str) -> dict:
        try:
            return await page.evaluate(self.JS_HEADER, username) or {}
        except Exception:
            return {}

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = username_of(url)

        if not row.profile_id:
            row.status = "ERROR"
            row.note("no @username in the URL -- private/video links cannot be resolved")
            return row

        page = await self.ctx.new_page()
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000)
            except Exception:
                row.status = "ERROR"
                row.note("navigation failed")
                return row

            # give the hydration script (and, on a suspicious-traffic
            # response, whatever TikTok replaces it with) a moment to settle
            await page.wait_for_timeout(min(2500, int(self.a.settle * 1000)))

            body = ""
            try:
                body = await page.inner_text("body")
            except Exception:
                pass

            hydration = await read_hydration(page)
            user = profile_from(hydration, row.profile_id) if hydration else None

            if user:
                self.fill(row, user)
                if not row.last_post_iso:
                    iso = newest_post_iso(hydration, row.profile_id)
                    if iso:
                        row.last_post_iso = iso
                        row.posts_seen = "yes"
                        row.mark("last_post", "hydration")
            else:
                if RE_CHECKPOINT.search(body) or "/captcha" in page.url:
                    row.status = "CHECKPOINT"
                    row.note("session checkpointed")
                    return row
                if "/login" in page.url or RE_LOGIN.search(body):
                    row.status = "LOGIN_REQUIRED"
                    row.note("cookies rejected/expired")
                    return row
                if RE_GONE.search(body):
                    row.status = "GONE"
                    row.note("removed or unavailable -- may already be down")
                    return row

                dom = await self.read_dom(page, row.profile_id)
                if dom.get("notFound"):
                    row.status = "GONE"
                    row.note("removed or unavailable -- may already be down")
                    return row
                if dom.get("followers") or dom.get("nickname"):
                    self.fill_from_dom(row, dom)
                else:
                    row.status = "PARTIAL"
                    row.note("profile payload not seen")
                    return row

            if not row.last_post_iso:
                # the free, no-extra-navigation attempt above came up empty
                # (it always does today, see module docstring), fall
                # back to the CAPTCHA-free search-based lookup, one extra
                # page visit. Best-effort: any failure here must not fail
                # the whole profile visit.
                try:
                    iso = await newest_post_via_search(self.ctx, row.profile_id)
                except Exception:
                    iso = ""
                if iso:
                    row.last_post_iso = iso
                    row.posts_seen = "yes"
                    row.mark("last_post", "search")

            await self.screenshot(page, row)
            row.status = "OK" if row.profile_name or row.profile_id else "PARTIAL"
            return row
        finally:
            try:
                await page.close()
            except Exception:
                pass

    @staticmethod
    def fill(row: Row, u: TikTokUser) -> None:
        row.profile_id = u.username
        # the display name is what an impersonator copies; fall back to handle
        row.profile_name = u.nickname or u.username
        row.mark("name", "hydration")
        row.name_score = name_score(row.profile_name, row.target)

        if u.follower_count is not None:
            row.followers = u.follower_count
            row.followers_exact = "yes"
            row.mark("followers", "hydration")
        if u.following_count is not None:
            row.friends = u.following_count
            row.mark("friends", "hydration")
        if u.avatar:
            row.profile_pic_url = u.avatar
            row.has_custom_pic = u.has_custom_pic
            row.mark("logo", "hydration")
        if u.video_count is not None:
            row.posts_seen = "yes" if u.video_count > 0 else "no"
            row.mark("posts", "hydration")
        if u.bio:
            row.note(f"bio: {u.bio[:120]}")
        if u.verified:
            row.verified = True
            row.note("verified account")
        if u.private:
            row.note("private account -- some fields may be limited")
        row.note("creation date not exposed by TikTok")

    @staticmethod
    def fill_from_dom(row: Row, dom: dict) -> None:
        """Same fields as fill(), read from the rendered header instead.
        Last-post date is NOT available from this path, it needs a video
        id to decode (see newest_post_iso), and the header alone carries no
        video ids, so it stays blank rather than guessed when analysis
        has fallen all the way through to the DOM."""
        nickname = (dom.get("nickname") or "").strip()
        if nickname:
            row.profile_name = nickname
            row.mark("name", "dom-header")
            row.name_score = name_score(nickname, row.target)

        followers, exact = parse_count(dom.get("followers", ""))
        if followers is not None:
            row.followers = followers
            row.followers_exact = "yes" if exact else "no"
            row.mark("followers", "dom-header")

        following, _ = parse_count(dom.get("following", ""))
        if following is not None:
            row.friends = following
            row.mark("friends", "dom-header")

        avatar = dom.get("avatar") or ""
        if avatar:
            row.profile_pic_url = avatar
            row.has_custom_pic = True
            row.mark("logo", "dom-header")

        if dom.get("verified"):
            row.verified = True
            row.note("verified account")
        if dom.get("isPrivate"):
            row.note("private account -- some fields may be limited")
        row.note("creation date not exposed by TikTok")

    async def screenshot(self, page, row: Row) -> None:
        if not self.evidence:
            return
        # DETERMINISTIC key, no timestamp, re-analysing a profile must
        # overwrite its own previous capture, not add another one.
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", row.profile_id or "entity")[:60]
        key = f"{self.evidence}/{stem}.png"
        try:
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
            row.profile_id = username_of(row.url)
            row.status = "ERROR"
            row.note(f"{type(e).__name__}: {e}")
            return row

    @staticmethod
    def report(i: int, total: int, u: str, row: Row) -> None:
        from backend.shared.logging import get_logger as _gl
        _gl("platforms.tiktok.analysis").info(
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
                _gl("platforms.tiktok.analysis").warning(
                    "CHECKPOINT -- aborting to protect the session."
                )
                break
            if i < len(jobs):
                await self.pause()
        return rows
