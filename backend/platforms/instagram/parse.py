"""Reading Instagram's own API payloads.

Instagram serves the web client from a private JSON API rather than a GraphQL
document, so interception targets those endpoints directly:

    /api/v1/users/web_profile_info/    the profile: counts, bio, newest posts
    /api/v1/fbsearch/topsearch/        search results: users[].user
    /graphql/query                     newer builds move profile data here

The profile payload is unusually complete -- it carries the newest twelve
posts with unix timestamps, so the last-post date needs no extra request.

NOT AVAILABLE: account creation date. Instagram exposes it only inside "About
this account", which is an authenticated interactive panel, so that column
stays blank rather than guessed -- the same call made for Facebook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from backend.utils.text import iter_dicts

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
