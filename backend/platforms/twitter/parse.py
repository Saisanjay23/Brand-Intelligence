"""Reading X/Twitter's own API payloads.

`legacy` USED TO carry everything the report needs, but X has been migrating
fields out of it one at a time, and a real capture (2026) showed a
UserByScreenName response with NO `legacy` key at all -- every field had
already moved to a sibling object. Every reader below therefore checks
`legacy` first (older captures/edge cases may still have it) and falls back
to the field's new home, so this keeps working whichever shape a given
response happens to be rather than breaking silently the next time a field
migrates. Pure functions of a parsed payload, so they test against a saved
capture without a browser.

WHERE THE DATA IS (current shape; `legacy.*` fallback still checked first)
    UserByScreenName / UserByRestId -> data.user.result
        .rest_id                       numeric id
        .core.screen_name              handle
        .core.name                     display name
        .core.created_at               "Wed Jun 01 12:00:00 +0000 2016"
        .relationship_counts.followers exact integer, not a rendered string
        .relationship_counts.following "following" on Twitter's naming
        .tweet_counts.tweets           post count
        .location.location
        .avatar.image_url
        .profile_bio.description       bio text
        .privacy.protected
        .verification.verified / .is_blue_verified

    SearchTimeline -> instructions[].entries[] with entryId "user-<id>",
        each holding the same user_results.result shape.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Optional

from backend.utils.text import iter_dicts

SEARCH_QUERY = "SearchTimeline"
USER_QUERIES = ("UserByScreenName", "UserByRestId")
TWEETS_QUERY = "UserTweets"

# the default egg avatar; anything else is a real upload
RE_DEFAULT_PIC = re.compile(
    r"default_profile(_normal)?\.png|/sticky/default_profile", re.I
)


@dataclass
class TwitterUser:
    entity_id: str = ""
    handle: str = ""
    name: str = ""
    followers: Optional[int] = None
    following: Optional[int] = None
    posts: Optional[int] = None
    created_iso: str = ""
    location: str = ""
    avatar: str = ""
    verified: bool = False
    protected: bool = False
    description: str = ""

    @property
    def url(self) -> str:
        return f"https://x.com/{self.handle}" if self.handle else ""

    @property
    def has_custom_pic(self) -> bool:
        return bool(self.avatar) and not RE_DEFAULT_PIC.search(self.avatar)


def parse_created(raw: str) -> str:
    """'Wed Jun 01 12:00:00 +0000 2016' -> '2016-06-01'."""
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").date().isoformat()
    except (ValueError, TypeError):
        return ""


def _user_from_result(res: dict) -> Optional[TwitterUser]:
    if not isinstance(res, dict):
        return None
    legacy = res.get("legacy")
    core = res.get("core") or {}
    if not isinstance(legacy, dict) and not core:
        return None
    legacy = legacy if isinstance(legacy, dict) else {}
    rest_id = res.get("rest_id") or res.get("id_str") or ""

    # newer payloads moved name/handle/created under `core`
    handle = legacy.get("screen_name") or core.get("screen_name") or ""
    name = legacy.get("name") or core.get("name") or ""
    created = legacy.get("created_at") or core.get("created_at") or ""
    avatar = legacy.get("profile_image_url_https") or (res.get("avatar") or {}).get(
        "image_url", ""
    )
    if not (handle or rest_id):
        return None

    # X has been migrating fields out of `legacy` one at a time -- confirmed
    # live (a captured real UserByScreenName response, 2026, carried NO
    # `legacy` key at all): screen_name/name/created_at moved to `core`,
    # location to its own `location.location`, and as of this capture ALSO
    # followers/following moved to `relationship_counts`, post count to
    # `tweet_counts.tweets`, bio to `profile_bio.description`, and protected
    # to `privacy.protected`. Every field below checks `legacy` first (older
    # captures still have it) and falls back to the new sibling object, so
    # this keeps working whichever shape a given response happens to be.
    location = legacy.get("location") or (res.get("location") or {}).get("location") or ""
    relationship_counts = res.get("relationship_counts") or {}
    tweet_counts = res.get("tweet_counts") or {}
    profile_bio = res.get("profile_bio") or {}
    privacy = res.get("privacy") or {}
    verification = res.get("verification") or {}

    followers = legacy.get("followers_count")
    if followers is None:
        followers = relationship_counts.get("followers")
    following = legacy.get("friends_count")
    if following is None:
        following = relationship_counts.get("following")
    posts = legacy.get("statuses_count")
    if posts is None:
        posts = tweet_counts.get("tweets")
    description = legacy.get("description") or profile_bio.get("description") or ""

    return TwitterUser(
        entity_id=str(rest_id),
        handle=handle,
        name=name,
        followers=followers,
        following=following,
        posts=posts,
        created_iso=parse_created(created),
        location=location.strip(),
        # request the larger render; _normal is a 48px thumbnail
        avatar=avatar.replace("_normal.", "_400x400."),
        verified=bool(
            res.get("is_blue_verified")
            or legacy.get("verified")
            or verification.get("verified")
        ),
        protected=bool(legacy.get("protected") or privacy.get("protected")),
        description=description.strip(),
    )


def iter_users(blob: Any) -> Iterator[TwitterUser]:
    """Every user object in a payload, deduped by id, in document order."""
    seen: set[str] = set()
    for d in iter_dicts(blob):
        res = d.get("result") if isinstance(d.get("result"), dict) else None
        if res is None and d.get("__typename") == "User":
            res = d
        if res is None or res.get("__typename") not in (None, "User"):
            continue
        user = _user_from_result(res)
        if user and user.handle and user.handle.lower() not in seen:
            seen.add(user.handle.lower())
            yield user


def latest_post(blob: Any, handle: str = "", entity_id: str = "") -> str:
    """Newest post date in a timeline payload, as ISO.

    Scoped to the profile's own posts: a timeline also carries retweets and
    quoted authors, and counting those would make a dormant account look live.
    Pinned tweets are excluded for the opposite reason -- an old pinned post
    would otherwise pass for recent activity.
    """
    want_h, want_id = handle.lower(), str(entity_id or "")
    best = ""
    for d in iter_dicts(blob):
        legacy = d.get("legacy")
        if not isinstance(legacy, dict) or "created_at" not in legacy:
            continue
        if "full_text" not in legacy and "conversation_id_str" not in legacy:
            continue  # a user object, not a tweet
        if legacy.get("retweeted_status_result"):
            continue
        author = str(legacy.get("user_id_str") or "")
        core = ((d.get("core") or {}).get("user_results") or {}).get("result") or {}
        author_handle = (
            (core.get("legacy") or {}).get("screen_name")
            or (core.get("core") or {}).get("screen_name")
            or ""
        )
        if want_id and author and author != want_id:
            continue
        if want_h and author_handle and author_handle.lower() != want_h:
            continue
        iso = parse_created(legacy["created_at"])
        if iso > best:
            best = iso
    return best


@dataclass
class SearchState:
    """The search timeline's cursor, which is how a sweep knows to stop."""

    bottom_cursor: str = ""
    entries: int = 0
    users: list[TwitterUser] = field(default_factory=list)


def search_state(blob: Any) -> SearchState:
    st = SearchState()
    for d in iter_dicts(blob):
        if (
            d.get("entryType") == "TimelineTimelineCursor"
            or d.get("__typename") == "TimelineTimelineCursor"
        ):
            if d.get("cursorType") == "Bottom" and isinstance(d.get("value"), str):
                st.bottom_cursor = d["value"]
        if isinstance(d.get("entries"), list):
            st.entries = max(st.entries, len(d["entries"]))
    st.users = list(iter_users(blob))
    return st


def parse_lines(text: str) -> Iterator[Any]:
    """Most responses are a single JSON object; some stream as JSON lines."""
    text = text.strip()
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
