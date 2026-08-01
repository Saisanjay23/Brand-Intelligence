"""Instagram discovery engine: search, crawling, pagination, and profile
extraction -- keywords in, candidate accounts out.

Also owns the browser session (login/checkpoint detection) and the payload
parsing (InstagramUser + friends): both are produced here first and re-used
by analysis_engine.py, which imports them rather than redefining them, so
there is exactly one definition of each across the two files.

Strategy: directly hit the Instagram Mobile API via Playwright's API context
using a spoofed Android User-Agent. This bypasses the web UI and returns a
robust JSON payload with up to 100+ users per request, with pagination to
fetch all available profiles for the keyword while respecting rate limits.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import quote

from backend.utils.text import iter_dicts
from backend.stealth.browser import Session
from backend.platforms.facebook.discovery_engine import Hit

# ─────────────────────────── session / login state ─────────────────────────

ME = "https://www.instagram.com/accounts/edit/"

RE_LOGIN = re.compile(
    r"(Log in to Instagram|Sign up to see|Log In\b.*Sign Up|"
    r"Phone number, username, or email)",
    re.I,
)
RE_CHECKPOINT = re.compile(
    r"(challenge_required|Suspicious Login|"
    r"We Detected An Unusual Login|confirm it.s you|"
    r"Help Us Confirm)",
    re.I,
)
RE_GONE = re.compile(
    r"(Sorry, this page isn.t available|" r"user not found|page not found)", re.I
)


class InstagramSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(ME, RE_LOGIN, RE_CHECKPOINT)


# ───────────────────── payload parsing (profile extraction) ────────────────
#
# Reading Instagram's own API payloads.
#
# Instagram serves the web client from a private JSON API rather than a
# GraphQL document, so interception targets those endpoints directly:
#
#     /api/v1/users/web_profile_info/    the profile: counts, bio, newest posts
#     /api/v1/fbsearch/topsearch/        search results: users[].user
#     /graphql/query                     newer builds move profile data here
#
# The profile payload is unusually complete -- it carries the newest twelve
# posts with unix timestamps, so the last-post date needs no extra request.
#
# NOT AVAILABLE: account creation date. Instagram exposes it only inside
# "About this account", which is an authenticated interactive panel, so that
# column stays blank rather than guessed -- the same call made for Facebook.

PROFILE_ENDPOINTS = ("users/web_profile_info", "api/v1/users/", "graphql/query")
SEARCH_ENDPOINTS = (
    "fbsearch/topsearch",
    "fbsearch/web/top_serp",
    "web/search/topsearch",
)

# Instagram's anonymous avatar
DEFAULT_PIC_HINTS = (
    "44884218_345707102882519_2446069589734326272_n",
    "anonymousUser",
    "default_profile",
)


@dataclass
class InstagramUser:
    entity_id: str = ""
    username: str = ""
    full_name: str = ""
    followers: Optional[int] = None
    following: Optional[int] = None
    posts: Optional[int] = None
    avatar: str = ""
    biography: str = ""
    verified: bool = False
    private: bool = False
    last_post_iso: str = ""

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.username}/" if self.username else ""

    @property
    def has_custom_pic(self) -> bool:
        return bool(self.avatar) and not any(
            h in self.avatar for h in DEFAULT_PIC_HINTS
        )


def _count(node: Any, *keys: str) -> Optional[int]:
    """Counts appear either as {"count": N} or as a bare integer."""
    for k in keys:
        v = node.get(k) if isinstance(node, dict) else None
        if isinstance(v, dict) and isinstance(v.get("count"), int):
            return v["count"]
        if isinstance(v, int):
            return v
    return None


def _latest_post(node: dict) -> str:
    """Newest timestamp among the profile's own recent media."""
    best = 0
    for d in iter_dicts(node):
        ts = d.get("taken_at_timestamp") or d.get("taken_at")
        if isinstance(ts, int) and 1_000_000_000 < ts < 4_000_000_000:
            best = max(best, ts)
    if not best:
        return ""
    return datetime.fromtimestamp(best, timezone.utc).date().isoformat()


def user_from_node(node: dict) -> Optional[InstagramUser]:
    if not isinstance(node, dict):
        return None
    username = node.get("username")
    if not isinstance(username, str) or not username:
        return None
    pk = node.get("id") or node.get("pk") or node.get("pk_id") or ""
    return InstagramUser(
        entity_id=str(pk),
        username=username,
        full_name=(node.get("full_name") or "").strip(),
        followers=_count(node, "edge_followed_by", "follower_count"),
        following=_count(node, "edge_follow", "following_count"),
        posts=_count(node, "edge_owner_to_timeline_media", "media_count"),
        avatar=(node.get("profile_pic_url_hd") or node.get("profile_pic_url") or ""),
        biography=(node.get("biography") or "").strip(),
        verified=bool(node.get("is_verified")),
        private=bool(node.get("is_private")),
        last_post_iso=_latest_post(node),
    )


def profile_from(blob: Any, username: str = "") -> Optional[InstagramUser]:
    """The profile this page is about, not whoever else the payload mentions."""
    want = username.lower().strip("/")
    best: Optional[InstagramUser] = None
    for d in iter_dicts(blob):
        # a profile node is the one carrying counts, not a bare mention
        if "username" not in d:
            continue
        if not any(
            k in d
            for k in (
                "edge_followed_by",
                "follower_count",
                "edge_owner_to_timeline_media",
                "media_count",
                "biography",
            )
        ):
            continue
        user = user_from_node(d)
        if not user:
            continue
        if want and user.username.lower() != want:
            continue
        if best is None or (user.followers is not None and best.followers is None):
            best = user
    return best


def iter_search_users(blob: Any) -> Iterator[InstagramUser]:
    """Users from a search payload, in result order."""
    seen: set[str] = set()
    for d in iter_dicts(blob):
        node = d.get("user") if isinstance(d.get("user"), dict) else None
        if node is None:
            continue
        user = user_from_node(node)
        if user and user.username.lower() not in seen:
            seen.add(user.username.lower())
            yield user

def iter_mobile_search_users(blob: Any) -> Iterator[InstagramUser]:
    """Users from a mobile search payload (`api/v1/users/search/`)."""
    seen: set[str] = set()
    users = blob.get("users", []) if isinstance(blob, dict) else []
    for node in users:
        if not isinstance(node, dict):
            continue
        user = user_from_node(node)
        if user and user.username.lower() not in seen:
            seen.add(user.username.lower())
            yield user


def parse_lines(text: str) -> Iterator[Any]:
    text = (text or "").strip()
    if not text:
        return
    try:
        yield json.loads(text)
        return
    except (json.JSONDecodeError, ValueError):
        pass
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


# ───────────────────────────── crawling / pagination ───────────────────────

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
