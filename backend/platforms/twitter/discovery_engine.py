"""Twitter discovery engine: search, crawling, pagination, and profile
extraction, keywords in, candidate accounts out.

Also owns the browser session (login/checkpoint detection) and the payload
parsing (TwitterUser + friends): both are produced here first and re-used by
analysis_engine.py, which imports them rather than redefining them, so there
is exactly one definition of each across the two files.

Uses the People tab of search (`f=user`) and reads the SearchTimeline payload
directly. Every result arrives fully hydrated, handle, name, followers, join
date, so unlike Facebook there is no second visit needed to score a profile
found here.

Pagination is cursor-driven and, like Facebook's people search, effectively
endless for a common name. A sweep therefore stops on an explicit end (no new
bottom cursor / no new users) or on a budget, and says which.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Optional
from urllib.parse import quote

from backend.shared.extraction import ExtractionResult, run_strategies
from backend.shared.text import iter_dicts
from backend.stealth.browser import Session
from backend.platforms.facebook.discovery_engine import Hit

# Session / login state

HOME = "https://x.com/home"

RE_LOGIN = re.compile(
    r"(Sign in to X|Log in to Twitter|Sign up for X|" r"Don't miss what's happening)",
    re.I,
)
RE_CHECKPOINT = re.compile(
    r"(Verify your identity|unusual login activity|"
    r"Your account has been locked|Confirm your)",
    re.I,
)
RE_GONE = re.compile(
    r"(This account doesn.t exist|Account suspended|" r"page doesn.t exist)", re.I
)


class TwitterSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        # deny_paths is load-bearing, not decoration: confirmed live
        # (2026-08-13, a fresh no-cookie context) that a logged-out
        # `/home` lands on `https://x.com/` with a body reading "Happening
        # now... Continue with phone / Google / Apple... Email or
        # username", which matches NEITHER RE_LOGIN (none of its
        # alternatives appear in that text) NOR the "/login" URL check
        # (the browser never leaves "/"). Without this, a genuinely dead
        # Twitter session read as healthy indefinitely, the exact same
        # false-negative this file's own Session.check_session docstring
        # documents Instagram having had, just never fixed here too.
        return await super().check_session(HOME, RE_LOGIN, RE_CHECKPOINT, deny_paths=("/",))


# Payload parsing (profile extraction)
# Reading Twitter's own API payloads.
#
# `legacy` USED TO carry everything the report needs, but X has been migrating
# fields out of it one at a time, and a real capture (2026) showed a
# UserByScreenName response with NO `legacy` key at all, every field had
# already moved to a sibling object. Every reader below therefore checks
# `legacy` first (older captures/edge cases may still have it) and falls back
# to the field's new home, so this keeps working whichever shape a given
# response happens to be rather than breaking silently the next time a field
# migrates. Pure functions of a parsed payload, so they test against a saved
# capture without a browser.
#
# WHERE THE DATA IS (current shape; `legacy.*` fallback still checked first)
#     UserByScreenName / UserByRestId -> data.user.result
#         .rest_id                       numeric id
#         .core.screen_name              handle
#         .core.name                     display name
#         .core.created_at               "Wed Jun 01 12:00:00 +0000 2016"
#         .relationship_counts.followers exact integer, not a rendered string
#         .relationship_counts.following "following" on Twitter's naming
#         .tweet_counts.tweets           post count
#         .location.location
#         .avatar.image_url
#         .profile_bio.description       bio text
#         .privacy.protected
#         .verification.verified / .is_blue_verified
#
#     SearchTimeline -> instructions[].entries[] with entryId "user-<id>",
#         each holding the same user_results.result shape.

SEARCH_QUERY = "SearchTimeline"
USER_QUERIES = ("UserByScreenName", "UserByRestId")
# X renames these. Confirmed live 2026-08-18: a profile visit now fires
# `UserOriginalsTimeline` (199KB, 37 tweet nodes) and NOT `UserTweets`, so
# matching the old name alone captured nothing and every last-post read
# fell through to the DOM tier -- which is what raised a critical
# "last-post extraction is broken" incident across 17 of 33 profiles.
#
# Both names are kept: the old one still appears on some account types and
# costs nothing to keep matching. Add to this tuple rather than replacing
# it when the name moves again.
TWEETS_QUERIES = ("UserTweets", "UserOriginalsTimeline")

# Back-compat alias; prefer TWEETS_QUERIES.
TWEETS_QUERY = TWEETS_QUERIES[0]

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
    verified_type: str = ""
    protected: bool = False
    description: str = ""
    external_url: str = ""

    @property
    def url(self) -> str:
        return f"https://x.com/{self.handle}" if self.handle else ""

    @property
    def has_custom_pic(self) -> bool:
        return bool(self.avatar) and not RE_DEFAULT_PIC.search(self.avatar)


# DOM fallback
# The SearchTimeline payload above is the primary and much richer source.
# It is also the single point of failure: when X rotates the query id or
# reshapes the response, `search_state()` recognises nothing, `by_id` stays
# empty, and the sweep reports 0 results as a clean success, silent, total,
# and indistinguishable from "this keyword genuinely matches nobody".
#
# This reads the same result feed off the rendered page instead. It carries
# strictly less (no follower counts, no join date, those are not in the
# results feed at all), but it keeps discovery producing profile URLs, which
# is discovery's actual job; analysis fills the rest in per-profile later.
#
# Anchored on `data-testid="UserCell"`, X's own long-lived hook for one
# account row, rather than on generated class names, which change constantly.
JS_DOM_USERS = """
() => {
  const seen = new Set();
  const out = [];
  for (const cell of document.querySelectorAll('[data-testid="UserCell"]')) {
    // the first /handle link in a cell is the account it belongs to
    const link = cell.querySelector('a[role="link"][href^="/"]');
    if (!link) continue;
    const handle = (link.getAttribute('href') || '').split('?')[0].replace(/^\\//, '');
    // reject anything that is not a bare profile path -- /i/..., /search,
    // /hashtag/... and status permalinks all appear inside these cells
    if (!handle || handle.includes('/') || /^(i|home|explore|search|hashtag|notifications|messages)$/.test(handle)) continue;
    if (seen.has(handle.toLowerCase())) continue;
    seen.add(handle.toLowerCase());

    const img = cell.querySelector('img[src*="profile_images"]');
    // the display name is the first text span that is not the @handle
    let name = '';
    for (const sp of cell.querySelectorAll('span')) {
      const t = (sp.textContent || '').trim();
      if (t && !t.startsWith('@') && t.toLowerCase() !== handle.toLowerCase()) { name = t; break; }
    }
    out.push({
      handle,
      name: name || handle,
      avatar: img ? img.getAttribute('src') : '',
      verified: !!cell.querySelector('[data-testid="icon-verified"]'),
    });
  }
  return out;
}
"""


async def dom_users(page) -> list[TwitterUser]:
    """Scrape the rendered results feed. Used only when the network payload
    yielded nothing, see Discovery.sweep."""
    rows = await page.evaluate(JS_DOM_USERS)
    users: list[TwitterUser] = []
    for r in rows or []:
        handle = (r.get("handle") or "").strip()
        if not handle:
            continue
        users.append(TwitterUser(
            # the numeric rest id is not in the DOM; the handle IS a stable
            # identity for dedup, and profile_repository keys on url when
            # entity_id is blank
            entity_id="",
            handle=handle,
            name=(r.get("name") or "").strip(),
            avatar=(r.get("avatar") or "").strip(),
            verified=bool(r.get("verified")),
        ))
    return users


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

    # X has been migrating fields out of `legacy` one at a time, confirmed
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

    verified_type = str(
        legacy.get("verified_type")
        or verification.get("verified_type")
        or ("blue" if res.get("is_blue_verified") else "")
    ).strip().lower()

    raw_entities = legacy.get("entities") or {}
    url_entities = (raw_entities.get("url") or {}).get("urls") or []
    external_url = str(url_entities[0].get("expanded_url") or url_entities[0].get("url", "") if url_entities else "").strip()

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
        verified_type=verified_type,
        protected=bool(legacy.get("protected") or privacy.get("protected")),
        description=description.strip(),
        external_url=external_url,
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


def pinned_tweet_ids(blob: Any) -> set[str]:
    """Every tweet id currently pinned to the top of a profile, per THIS
    response's own pin marker.

    Confirmed live (captured UserTweets response, 2026): a pinned tweet is
    not flagged on the tweet object itself, it is wrapped by a distinct
    top-level instruction, `instructions[N] == {"type": "TimelinePinEntry",
    "entry": {...tweet...}}`, sitting alongside the normal
    `TimelineAddEntries` instruction that holds the regular timeline. (The
    same account's profile response separately carries
    `data.user.result.pinned_items.tweet_ids_str`, a second, independent
    confirmation of the same id, not used here to avoid a race: that's a
    DIFFERENT network response, and there's no guaranteed ordering between
    it and this one arriving.)

    Structure-agnostic on purpose: searches for the `TimelinePinEntry` type
    marker rather than hardcoding `instructions[1]`'s index or the path down
    to it, since this response's shape has migrated before (see this
    module's docstring on `legacy` fields moving to sibling objects) and
    will again.
    """
    ids: set[str] = set()
    for d in iter_dicts(blob):
        if d.get("type") != "TimelinePinEntry":
            continue
        entry = d.get("entry")
        if not isinstance(entry, dict):
            continue
        for sub in iter_dicts(entry):
            rid = sub.get("rest_id")
            if rid:
                ids.add(str(rid))
    return ids


def latest_post(blob: Any, handle: str = "", entity_id: str = "") -> str:
    """Newest ORGANIC post date in a timeline payload, as ISO.

    Scoped to the profile's own posts, excluding two things that would make
    a dormant account look active if counted:
      - retweets/quotes of someone else, `retweeted_status_result` is the
        marker, an unambiguous field on the tweet itself.
      - a pinned tweet. NOT a marker on the tweet itself (confirmed live:
        no key containing "pin" anywhere in a pinned tweet's own `legacy`
        dict), so pinned_tweet_ids() above finds it via its wrapping
        instruction instead, and this function drops any tweet whose id is
        in that set. Verified live: without this, a real account's reported
        last-post date was 2026-08-10 (its pinned tweet) instead of
        2026-08-07 (its actual newest post).
    """
    pinned = pinned_tweet_ids(blob)
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
        tweet_id = str(legacy.get("id_str") or d.get("rest_id") or "")
        if tweet_id and tweet_id in pinned:
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


# Crawling / pagination

SEARCH_URL = "https://x.com/search?q={q}&src=typed_query&f=user"


@dataclass
class Sweep:
    keyword: str
    tab: str = "people"
    hits: list[Hit] = field(default_factory=list)
    users: list[TwitterUser] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""
    # which extraction method actually produced `users`, "graphql" normally,
    # "dom" when the network payload yielded nothing and the rendered feed
    # had to stand in. Carried onto each Hit so a card can say where it came
    # from rather than implying every field was equally well sourced.
    source: str = "graphql"
    # the full blame trail (see shared/extraction.py) when any strategy
    # failed, so discovery_service can put file+line into the incident/email
    extraction: Optional["ExtractionResult"] = None

    def summary(self) -> str:
        base = f"{len(self.hits)} hits, {self.pages} pages, {self.stopped}"
        return f"{base}, via {self.source}" if self.source != "graphql" else base


class Discovery:
    """Runs keyword sweeps on an already-started browser session."""

    def __init__(self, args, ctx):
        self.a = args
        self.ctx = ctx

    async def sweep(self, keyword: str, tab: str = "people") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        # A client's keyword groups run concurrently (discovery_service.py's
        # discovery_concurrency, default 2), so two Discovery.sweep() calls
        # routinely fire their first search request within ~1s of each other
        # on the SAME logged-in session/IP. Confirmed live (2026-08-13): a
        # single isolated sweep parses the network payload perfectly,
        # 20/20 users, every field, but real production runs, which are
        # always this same concurrency, degrade to the dom:UserCell fallback
        # on the large majority of sweeps (57 ExtractionDegraded incidents
        # in one morning). That split, clean in isolation, degraded only
        # under concurrency, is the signature of X's own anti-automation
        # throttling reacting to near-simultaneous search requests from one
        # account, not a payload-shape change; nothing about search_state()
        # itself needed fixing. This jitter doesn't remove the concurrency
        # (still worth the throughput), it just keeps two requests from ever
        # landing in the same instant.
        await asyncio.sleep(random.uniform(1.5, 4.0))
        page = await self.ctx.new_page()

        by_id: dict[str, TwitterUser] = {}
        cursor = ""
        arrived = asyncio.Event()

        async def on_response(resp):
            nonlocal cursor
            try:
                if SEARCH_QUERY not in resp.url:
                    return
                text = await resp.text()
            except Exception:
                return
            for blob in parse_lines(text):
                st = search_state(blob)
                for u in st.users:
                    by_id.setdefault(u.entity_id or u.handle, u)
                if st.bottom_cursor:
                    cursor = st.bottom_cursor
            out.pages += 1
            arrived.set()

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            await page.goto(
                SEARCH_URL.format(q=quote(keyword)),
                wait_until="domcontentloaded",
                timeout=self.a.timeout * 1000,
            )
            try:
                await page.wait_for_function(
                    "() => document.body.innerText.length > 400",
                    timeout=self.a.settle * 1000,
                )
            except Exception:
                pass

            # Wait for the first SearchTimeline response to arrive. X's
            # search payload can lag behind the DOM render, especially under
            # load or when the query requires server-side ranking. Without
            # this, by_id stays empty, the patience loop stalls out, and
            # run_strategies falls back to the weaker dom:UserCell method.
            if not by_id:
                try:
                    await asyncio.wait_for(arrived.wait(), timeout=self.a.settle)
                except asyncio.TimeoutError:
                    pass

            stalls, last_cursor = 0, ""
            while True:
                if self.a.max_results and len(by_id) >= self.a.max_results:
                    out.stopped = "cap:results"
                    break
                if self.a.max_seconds and time.time() - started >= self.a.max_seconds:
                    out.stopped = "cap:seconds"
                    break

                before = len(by_id)
                arrived.clear()
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    await asyncio.wait_for(arrived.wait(), timeout=self.a.page_wait)
                except asyncio.TimeoutError:
                    pass

                if len(by_id) > before:
                    stalls = 0
                    if out.pages % self.a.progress_every == 0:
                        print(
                            f"    [x/{tab}] {keyword!r}: {len(by_id)} so far, "
                            f"page {out.pages}, {time.time()-started:.0f}s",
                            file=sys.stderr,
                        )
                else:
                    stalls += 1
                    # the same bottom cursor twice with no new users is the end
                    if cursor and cursor == last_cursor and stalls >= 2:
                        out.stopped, out.complete = "exhausted", True
                        break
                    if stalls >= self.a.patience:
                        out.stopped = "stalled"
                        break
                    await page.wait_for_timeout(600)
                last_cursor = cursor

            # Network payload first (richer), rendered DOM second. The DOM
            # pass only runs when the payload produced nothing at all, so a
            # healthy sweep never pays for it, and when the payload shape
            # rotates, discovery degrades to "fewer fields" instead of to
            # "zero results, reported as success".
            chain = await run_strategies(
                f"twitter/search[{keyword!r}]",
                [
                    ("network:SearchTimeline", lambda: list(by_id.values())),
                    ("dom:UserCell", lambda: dom_users(page)),
                ],
            )
            out.users = chain.value or []
            out.extraction = chain
            if chain.degraded:
                out.source = "dom"

            out.hits = [
                Hit(
                    entity_id=u.entity_id,
                    name=u.name,
                    url=u.url,
                    avatar=u.avatar,
                    has_custom_pic=u.has_custom_pic,
                    verified=u.verified,
                    entity_type="profile",
                    keyword=keyword,
                    tab=tab,
                    rank=i,
                    source=out.source,
                )
                for i, u in enumerate(out.users)
                if u.url
            ]
            if self.a.max_results:
                # Confirmed live: the loop-break check above
                # (`len(by_id) >= max_results`) only fires at the TOP of the
                # next iteration, after a whole response page has already
                # been absorbed into by_id. X returns ~20 users per
                # response, so a configured cap of 5 still returned 20 hits
                # with nothing here to trim it back down. The break check
                # bounds how much MORE gets fetched; it was never what
                # bounds what gets reported.
                out.hits = out.hits[: self.a.max_results]
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            try:
                await page.close()
            except Exception:
                pass
            out.seconds = time.time() - started
        return out

    async def run(
        self, keywords: list[str], tabs: Optional[list[str]] = None
    ) -> list[Sweep]:
        """X has one people-search surface, so `tabs` is accepted and ignored."""
        sem = asyncio.Semaphore(max(1, self.a.concurrency))

        async def one(i: int, keyword: str) -> tuple[int, Sweep]:
            async with sem:
                await asyncio.sleep(i % max(1, self.a.concurrency) * 1.0)
                s = await self.sweep(keyword)
                print(
                    f"  [x/people] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
                    file=sys.stderr,
                )
                return i, s

        pairs = await asyncio.gather(*(one(i, k) for i, k in enumerate(keywords)))
        return [s for _, s in sorted(pairs, key=lambda p: p[0])]

