"""Facebook analysis engine: validation, metadata analysis, and impersonation
signal extraction, profile URL -> a scored Row.

Session/login-checking and URL/identity normalization live in
discovery_engine.py (imported below) since discovery produces them first;
this file owns everything specific to reading and scoring a validated visit:
field readers, payload scoping (Harvest), and the browser-drive loop
(Scraper).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from backend.shared.models.row import Row
from backend.shared.text import (MONTHS, epoch_to_dt, find_ints,
                               is_place, iter_dicts, iter_kv, name_score,
                               parse_count, parse_joined)
from backend.platforms.facebook.discovery_engine import (RE_CHECKPOINT,
                                                          RE_DEFAULT_PIC,
                                                          RE_GONE, RE_LOGIN,
                                                          FacebookSession,
                                                          hd_picture_url,
                                                          normalize_url,
                                                          profile_id, tab_url)

# Field-reading constants
# Keys and patterns used to read fields off a profile.
#
# The K_* tuples are GraphQL key names; the RE_* patterns read rendered text.
# Both drift when Facebook ships changes, when a field goes blank across
# every profile, suspect these first.

MAX_FOLLOWERS = 5_000_000_000

K_FOLLOWERS = (
    "follower_count",
    "followers_count",
    "fan_count",
    "subscriber_count",
    "follower_count_int",
)
K_JOINED = (
    "joined_date",
    "profile_creation_time",
    "account_creation_time",
    "date_joined",
    "creation_date",
    "page_creation_date",
    "profile_created_time",
)
# "created_time" deliberately excluded. Confirmed live, in the raw payload:
# it belongs to COMMENT objects specifically:
# `"comment":{"created_time":...}` under an `XFBCommentTimestampBadge`
# typename, not to posts. `creation_time` (kept) is confirmed genuinely
# post-scoped in the same capture: it appears alongside the post's own
# `post_id` field. See read_last_post()/_post_stamps() below for the fuller
# fix, a key name match alone (even "creation_time") isn't proof of a
# real post; _post_stamps() additionally requires the post_id sibling.
K_POST_TIME = ("publish_time", "creation_time", "publish_time_ts")
K_LOCATION = (
    "current_city_name",
    "single_line_address",
    "full_address",
    "street_address",
    "city_name",
    "hometown_name",
)
# "short_name" is deliberately absent: it is the first name only, so it scores
# under NAME_THRESHOLD, and it is the key that leaks other entities' names
K_NAME = ("profile_name", "page_name", "name_for_display")
K_PIC = ("profile_picture", "profile_pic_url", "uri", "photo_image")

RE_JOINED = re.compile(
    r"(?:Joined Facebook|Joined|Date joined)\s*[-–—:•]?\s*"
    r"([A-Z][a-z]{2,9}\s+\d{4}|[A-Z][a-z]{2,9}\s+\d{1,2},?\s+\d{4})",
    re.I,
)
RE_FOLLOWERS = re.compile(
    r"([\d][\d.,\s]{0,15}[KMB]?)\s*(?:followers|people follow this)", re.I
)
# one header counter chip, e.g. "154M followers" / "53 friends" / "1.2K likes"
RE_CHIP = re.compile(
    r"^([\d][\d.,\s]{0,15}[KMB]?)\s*"
    r"(followers?|following|friends?|likes?|people follow this)\b",
    re.I,
)
# Anchored to the start of a line because these are About-tab FIELD labels,
# not prose. Unanchored, "From" matched mid-sentence marketing copy, a
# live Page produced "From classrooms to cement plants, from learning
# concepts to witnessing them" as a candidate location. `is_place` rejected
# it, so nothing wrong was ever stored, but relying on the validator alone
# to catch a matcher this loose is one plausible-looking city name away from
# publishing a fabricated location on a client-facing incident.
RE_LIVES_IN = re.compile(r"(?:^|\n)\s*Lives in\s+([^\n·|]{2,70})", re.M)
RE_FROM = re.compile(r"(?:^|\n)\s*From\s+([^\n·|]{2,70})", re.M)
RE_NO_POSTS = re.compile(
    r"(No posts yet|hasn't (?:added|shared|posted)|nothing to show|"
    r"No posts available)",
    re.I,
)

GENERIC_NAMES = {"facebook", "notifications"}


# Harvest
# Everything collected from one profile visit, and how to read it back.
#
# `scoped()` is the important part: it narrows the collected payloads to the
# entity that IS this profile, which is what keeps other people's names,
# follower counts and post timestamps out of the record.
#
# Embedded payloads are kept as raw text and parsed on demand. A profile page
# ships ~180 of them and only a handful mention the profile at all, so scoping
# substring-filters first and parses second, the same answer for a fraction
# of the work.


class Harvest:
    def __init__(self):
        self.gql: list[Any] = []  # parsed XHR /api/graphql lines
        self.raw: list[str] = []  # unparsed script[type=application/json]
        self.html: dict[str, str] = {}
        self.text: dict[str, str] = {}
        self.ents: list[dict] = []  # dicts that ARE this profile (id == pid)
        self.dom: dict[str, Any] = {}  # header fields read straight off the page
        self._scopes: dict[str, "Harvest"] = {}

    # ---------- collection ----------

    def add_gql(self, body: str) -> None:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    self.gql.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def add_embedded(self, texts) -> None:
        self.raw.extend(t for t in texts or [] if t)
        self._scopes.clear()  # new payloads invalidate cached views

    # ---------- lookup ----------

    def gql_raw(self) -> str:
        try:
            return json.dumps(self.gql)
        except (TypeError, ValueError):
            return ""

    def mentioning(self, needle: str) -> Iterator[Any]:
        """Parsed payloads whose text contains `needle`, embedded, then XHR.

        The needle is the bare id, not `"id":"<id>"`. Matching the keyed form
        would depend on the payload having no space after the colon, which is
        true of Facebook's compact JSON today and would silently return
        nothing the day it stops being true.
        """
        for t in self.raw:
            if needle in t:
                try:
                    yield json.loads(t)
                except (json.JSONDecodeError, ValueError):
                    continue
        for blob in self.gql:
            try:
                if needle in json.dumps(blob):
                    yield blob
            except (TypeError, ValueError):
                continue

    def scoped(self, pid: str) -> "Harvest":
        """A view of the payloads narrowed to this profile.

        `ents` holds the dicts whose own id is the profile id, the GraphQL
        objects that ARE this profile. Reading a field off one of those is
        unambiguous. The wider page carries the notification flyout, friend
        suggestions and sponsored payloads, all with their own name, follower
        and timestamp keys; anything read from the unfiltered pile belongs to
        whoever happened to load first.

        An unverifiable id scopes to nothing, on purpose: blank beats wrong.
        """
        if pid in self._scopes:
            return self._scopes[pid]

        v = Harvest()
        v.html, v.text, v.dom = self.html, self.text, self.dom
        if pid and pid.isdigit():
            for blob in self.mentioning(pid):
                for d in iter_dicts(blob):
                    if d.get("id") == pid and len(d) > 1:
                        v.ents.append(d)
            for blob in self.gql:
                try:
                    if pid in json.dumps(blob):
                        v.gql.append(blob)
                except (TypeError, ValueError):
                    continue
        self._scopes[pid] = v
        return v

    # ---------- entity readers ----------

    def ent_scalar(self, key: str) -> str:
        """A field read directly off the profile entity, no nesting, no guessing."""
        for d in self.ents:
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    def ent_path(self, *paths: str) -> str:
        for d in self.ents:
            for p in paths:
                cur: Any = d
                for part in p.split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if isinstance(cur, str) and cur.strip():
                    return cur.strip()
        return ""

    def ent_social(self) -> list[str]:
        """The header chips: '70 followers', '8 following', '328 friends'.

        Facebook ships these already rendered, so this is the only GraphQL form
        of the follower count, there is no integer field anywhere in the
        entity. Two shapes occur: content[].text.text, and content[].text as a
        bare string under header_top_row.profile_user.
        """
        out: list[str] = []
        for _k, v in self._entity_kv({"profile_social_context"}):
            if not isinstance(v, dict):
                continue
            for item in v.get("content") or []:
                t = item.get("text") if isinstance(item, dict) else None
                if isinstance(t, dict):
                    t = t.get("text")
                if isinstance(t, str) and t.strip() and t.strip() not in out:
                    out.append(t.strip())
        return out

    def ent_ints(self, keys) -> list[int]:
        out = []
        for _k, v in self._entity_kv(set(keys)):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(int(v))
            elif isinstance(v, str) and v.isdigit():
                out.append(int(v))
        return out

    def ent_strs(self, keys) -> list[str]:
        return [
            v.strip()
            for _k, v in self._entity_kv(set(keys))
            if isinstance(v, str) and v.strip()
        ]

    def _entity_kv(self, want: set[str]) -> Iterator[tuple[str, Any]]:
        for d in self.ents:
            for k, v in iter_kv(d):
                if k in want:
                    yield k, v

    # ---------- unscoped fallbacks ----------

    def gql_ints(self, keys) -> list[int]:
        want, out = set(keys), []
        for blob in self.gql:
            for k, v in iter_kv(blob):
                if k not in want or isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    out.append(int(v))
                elif isinstance(v, str) and v.isdigit():
                    out.append(int(v))
        return out

    def gql_strs(self, keys) -> list[str]:
        want, out = set(keys), []
        for blob in self.gql:
            for k, v in iter_kv(blob):
                if k in want and isinstance(v, str) and v.strip():
                    out.append(v.strip())
        return out

    def all_html(self) -> str:
        return "\n".join(self.html.values())

    def all_text(self) -> str:
        return "\n".join(self.text.values())


# Readers
# Turning a Harvest into a Row: one function per report field.
#
# Every reader follows the same order, the profile's own GraphQL entity
# first, then the rendered header, then progressively looser fallbacks, and
# records which one answered via `row.mark()`. That provenance is what makes
# a filled cell auditable and a blank one meaningful.
#
# These are pure functions of (row, harvest): no browser, no network. That is
# what makes them testable against a saved payload.


def read_name(row: Row, h: Harvest) -> None:
    # the entity's own "name" is the full display name; "short_name" is the
    # first name only and would fall under NAME_THRESHOLD, so never use it.
    # og:title is absent on logged-in renders and <title> is "(2) Facebook".
    # the third flag says whether a generic name is believable from that
    # source: an entity really can be called "Facebook", but a <title>
    # reading "Facebook" is just the browser tab.
    cands = [
        ("graphql", h.ent_scalar("name"), True),
        ("dom-header", h.dom.get("name"), True),
        ("dom-post-label", h.dom.get("postAuthor"), True),
    ]
    main = h.html.get("main", "")
    for tag, m in (
        (
            "og:title",
            re.search(
                r'property=["\']og:title["\'][^>]+content=["\']' r'([^"\']+)', main
            ),
        ),
        ("title-tag", re.search(r"<title>([^<|]{1,140})", main)),
    ):
        if m:
            cands.append(
                (
                    tag,
                    re.sub(
                        r"\s*\|\s*Facebook\s*$",
                        "",
                        re.sub(r"^\(\d+\)\s*", "", m.group(1)),
                    ).strip(),
                    False,
                )
            )
    cands += [("graphql-loose", v, True) for v in h.gql_strs(K_NAME)]
    for tag, c, trusted in cands:
        c = (c or "").strip()
        if not c or (not trusted and c.lower() in GENERIC_NAMES):
            continue
        row.profile_name = c
        row.mark("name", tag)
        break
    row.name_score = name_score(row.profile_name, row.target)


def take_chip(row: Row, chip: str, source: str) -> None:
    """One header counter, '154M followers', '53 friends', '1.2K likes'.

    Pages publish followers (and older ones only likes), personal profiles
    usually publish friends instead, creator profiles publish both.
    """
    m = RE_CHIP.match(chip)
    if not m:
        return
    val, exact = parse_count(m.group(1))
    if val is None or not (0 <= val < MAX_FOLLOWERS):
        return
    kind = m.group(2).lower()
    if kind.startswith("friend"):
        if row.friends is None:
            row.friends = val
            row.mark("friends", source)
        return
    if kind.startswith("following"):
        return
    if row.followers is not None:
        return
    row.followers = val
    row.followers_exact = "yes" if exact else "no"
    row.mark("followers", source)
    if kind.startswith("like"):
        row.note(f"page publishes likes, not followers ({chip})")
    if not exact:
        row.note(f"followers rounded ({chip})")


def followers_from_friends(row: Row, chips: "list[tuple[str, str]]") -> None:
    """A personal profile's FRIEND count is its audience number, so it
    belongs in `followers` when the profile published no follower count of
    its own.

    Facebook publishes a follower count on Pages and on creator profiles,
    and a friend count instead on an ordinary personal profile. The report
    has exactly ONE audience column (`followers`) -- there is no friends
    column in the grid or in any export -- so a friend count left only in
    `row.friends` was read correctly and then thrown away: 123 of the 165
    personal profiles in a single 663-URL run had a perfectly good friend
    count stored and still showed a blank Followers cell.

    Runs LAST, after every genuine follower tier in read_counts, so a real
    follower count always wins on a creator profile that publishes both.
    The source is tagged `...-friends` and a note is added so the two stay
    distinguishable to anyone reading the row rather than the column.
    """
    for chip, src in chips:
        m = RE_CHIP.match(chip)
        if not m or not m.group(2).lower().startswith("friend"):
            continue
        val, exact = parse_count(m.group(1))
        if val is None or not (0 <= val < MAX_FOLLOWERS):
            continue
        row.followers = val
        row.followers_exact = "yes" if exact else "no"
        row.mark("followers", f"{src}-friends")
        row.note(f"profile publishes friends, not followers ({chip})")
        if not exact:
            row.note(f"followers rounded ({chip})")
        return


def read_counts(row: Row, h: Harvest) -> None:
    """The audience number, whichever of the three Facebook publishes."""
    chips = [(s, "graphql-social-context") for s in h.ent_social()]
    # the header line holds the same counters when the entity is unreadable
    chips += [
        (part.strip(), "dom-header")
        for part in re.split(r"[•·|]", str(h.dom.get("counter") or ""))
    ]
    for chip, src in chips:
        take_chip(row, chip, src)
    if row.followers is not None:
        return
    ents = [n for n in h.ent_ints(K_FOLLOWERS) if 0 <= n < MAX_FOLLOWERS]
    if ents:
        row.followers = max(ents)
        row.followers_exact = "yes"
        row.mark("followers", "graphql")
        return
    ints = [n for n in h.gql_ints(K_FOLLOWERS) if 0 <= n < MAX_FOLLOWERS]
    if ints:
        row.followers = max(ints)
        row.followers_exact = "yes"
        row.mark("followers", "graphql-loose")
        return
    if m := RE_FOLLOWERS.search(h.text.get("main", "")):
        val, exact = parse_count(m.group(1))
        if val is not None:
            row.followers = val
            row.followers_exact = "yes" if exact else "no"
            row.mark("followers", "page-text")
            if not exact:
                row.note(f"followers rounded ({m.group(1).strip()})")
            return
    followers_from_friends(row, chips)


def read_created(row: Row, h: Harvest) -> None:
    for v in h.ent_strs(K_JOINED):
        if iso := parse_joined(v):
            row.created_iso = iso
            return
    for n in h.ent_ints(K_JOINED):
        if dt := epoch_to_dt(n):
            row.created_iso = dt.date().isoformat()
            return
    if m := RE_JOINED.search(h.all_text()):
        if iso := parse_joined(m.group(1)):
            row.created_iso = iso
            return
    for v in h.gql_strs(K_JOINED):
        if iso := parse_joined(v):
            row.created_iso = iso
            return
        if re.match(r"^\d{4}-\d{2}", v):
            row.created_iso = v[:10] if len(v) >= 10 else v[:7]
            return
    for n in h.gql_ints(K_JOINED) + find_ints(h.all_html(), K_JOINED):
        if dt := epoch_to_dt(n):
            row.created_iso = dt.date().isoformat()
            return


def _post_stamps(roots) -> list[int]:
    """K_POST_TIME values that belong to a genuine post object, not just
    any nested dict that happens to reuse the key name.

    This used to trust ANY dict carrying a K_POST_TIME key, on the
    assumption that being inside the profile's own entity subtree (`ents`)
    already meant "this profile's own post". That assumption was wrong,
    confirmed live: a comment on one of this profile's posts is ALSO
    nested inside that same entity subtree, and its own `created_time`
    field was being counted as if it were a post, letting a stranger's
    comment on an old post make a dormant page's last-post date look like
    today. (A different manifestation of the same root cause, trusting a
    key name with no structural check, was closed earlier by dropping
    "created_time" from K_POST_TIME entirely, once it was confirmed to
    always be a comment field. This closes the general case: even
    `creation_time`, confirmed genuinely post-scoped, still needs a real
    post to hang off of.)

    A genuine post's own dict was confirmed live to always carry a
    `post_id` sibling in the SAME dict, e.g.
    `{"post_id": "...", "creation_time": 1786353437, "attachments": [...]}`.
    Requiring that sibling is what actually scopes this to posts, not the
    entity-subtree membership that was doing that job before.
    """
    out: list[int] = []
    for root in roots:
        for d in iter_dicts(root):
            if "post_id" not in d:
                continue
            for key in K_POST_TIME:
                v = d.get(key)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    out.append(int(v))
                elif isinstance(v, str) and v.isdigit():
                    out.append(int(v))
    return out


def read_last_post(row: Row, h: Harvest) -> None:
    # Three tiers, precise-and-narrow first, proven-but-looser as the safety
    # net, never silently empty just because the precise method's data
    # hasn't arrived yet over XHR.
    #
    # 1. Entity-scoped, post_id-gated: this profile's own subtree, only
    #    dicts confirmed (live) to be a real post (they carry a `post_id`
    #    sibling, notifications and comments do not, so this is what
    #    actually excludes them, not the entity-subtree membership alone).
    # 2. Unscoped, still post_id-gated: broader reach across every parsed
    #    payload (embedded script tags + XHR), same real-post proof
    #    required.
    # 3. The original un-gated text-regex scan, INCLUDING the rendered
    #    page's own HTML, kept, not removed, because it is the only
    #    source available before certain XHR responses (e.g. the timeline
    #    feed units query) have necessarily arrived, and losing it produced
    #    an empty result live where tiers 1-2 alone found nothing yet. Only
    #    reached when 1-2 come up empty, and "created_time" is still
    #    excluded from K_POST_TIME (the confirmed comment field), so the
    #    specific leak that motivated this rewrite stays closed even here.
    stamps, tag = _post_stamps(h.ents), "graphql"
    if not stamps:
        raw_parsed = []
        for t in h.raw:
            try:
                raw_parsed.append(json.loads(t))
            except (json.JSONDecodeError, ValueError):
                continue
        stamps = _post_stamps(h.gql) + _post_stamps(raw_parsed)
        tag = "graphql-unscoped"
    if not stamps:
        stamps = find_ints(h.gql_raw() + h.html.get("main", ""), K_POST_TIME)
        tag = "payload-regex-ungated"
    dts = [epoch_to_dt(t) for t in stamps]
    dts = [d for d in dts if d]
    # a join/creation date can surface under creation_time, drop it
    if row.created_iso:
        dts = [d for d in dts if not d.date().isoformat().startswith(row.created_iso)]
    if dts:
        row.last_post_iso = max(dts).date().isoformat()
        row.posts_seen = "yes"
        row.mark("last_post", tag)
    elif RE_NO_POSTS.search(h.all_text()):
        row.posts_seen = "no"
        row.mark("last_post", "no-posts-notice")


# DOM last-post fallback
# read_last_post above works from payload timestamps. When Facebook does not
# ship those for a given profile, the date is still right there on screen:
# every post's permalink carries the exact publish time in its aria-label,
# put there for screen readers. Captured live:
#
#     <a href=".../posts/pfbid032..." aria-label="Friday 7 August 2026 at 14:14">3d</a>
#
# That is a precise absolute timestamp, not the "3d" relative text a person
# sees, so it needs no arithmetic against "now" and cannot drift.
#
# `/reel/` is in the selector for a Page's own Reel posts, matched the same
# way as a regular post, confirmed live via a genuine case
# (`permalink_url: ".../reel/1711498136706603/"`, authored by the page
# itself per its `actors` field). It does NOT make this fallback see every
# Reel, though: confirmed live on that same profile, Facebook does not
# render Reels inline in the default chronological timeline at all, the
# only `/reel/` link on that view was the nav shortcut to the separate
# Reels tab (`href="/reel/?s=tab"`, aria-label "Reels", which fails
# parse_aria_date below harmlessly). Reaching an out-of-timeline Reel would
# need a second page visit to that tab, which this fallback does not do.
# That gap is acceptable here because it is the least-trusted of three
# tiers: read_last_post()'s payload-based extraction runs first and is
# confirmed to capture Reel timestamps correctly (it reads structured data,
# not the rendered page), this DOM path only ever fires when that has
# already returned nothing.
JS_POST_TIMES = """
() => {
  const out = [];
  const sel = 'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/videos/"], a[href*="/reel/"], a[href*="permalink"]';
  for (const a of document.querySelectorAll(sel)) {
    const al = a.getAttribute('aria-label') || a.getAttribute('title') || '';
    if (al) out.push(al);
    const inner = a.querySelector('[aria-label]');
    if (inner) {
      const t = inner.getAttribute('aria-label') || '';
      if (t) out.push(t);
    }
  }
  return out.slice(0, 60);
}
"""

_MONTHS = {m.lower(): i for i, m in enumerate(MONTHS, start=1)}
# "Friday 7 August 2026 at 14:14" / "7 August 2026" / "August 7, 2026"
RE_ARIA_DMY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
RE_ARIA_MDY = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b")


def parse_aria_date(label: str) -> Optional[str]:
    """An aria-label timestamp -> 'YYYY-MM-DD', or None if it isn't one."""
    for rx, order in ((RE_ARIA_DMY, "dmy"), (RE_ARIA_MDY, "mdy")):
        if m := rx.search(label or ""):
            day, mon, year = (m.group(1), m.group(2), m.group(3)) if order == "dmy" \
                else (m.group(2), m.group(1), m.group(3))
            month = _MONTHS.get(mon.lower()[:3]) or _MONTHS.get(mon.lower())
            if not month:
                # try full-name match (MONTHS holds full names)
                month = next((i for i, name in enumerate(MONTHS, 1)
                              if name.lower().startswith(mon.lower()[:3])), None)
            if not month:
                continue
            try:
                dt = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
            except ValueError:
                continue
            # a post cannot be in the future; a stamp before Facebook existed
            # is not a post date either
            now = datetime.now(timezone.utc)
            if 2004 <= dt.year and dt <= now:
                return dt.date().isoformat()
    return None


async def dom_last_post(page) -> str:
    """Newest post date read off post-permalink aria-labels. '' when the
    page shows no dated post."""
    try:
        labels = await page.evaluate(JS_POST_TIMES)
    except Exception:
        return ""
    dates = [d for d in (parse_aria_date(x) for x in labels or []) if d]
    return max(dates) if dates else ""


def read_location(row: Row, h: Harvest) -> None:
    for v in h.ent_strs(K_LOCATION):
        if is_place(v):
            row.location = v.strip()
            return
    for rx in (RE_LIVES_IN, RE_FROM):
        if m := rx.search(h.all_text()):
            if is_place(m.group(1)):
                row.location = m.group(1).strip(" ,·|")
                return
    for v in h.gql_strs(K_LOCATION):
        if is_place(v):
            row.location = v.strip()
            return


def read_pic(row: Row, h: Harvest) -> None:
    # the entity's own picture, else the header avatar, not whichever
    # fbcdn URL happened to appear first in the pile
    url = h.ent_path(
        "profile_picture.uri",
        "profile_picture_for_sticky_bar.uri",
        "delegate_page.profile_picture.uri",
    )
    tag = "graphql"
    if not url:
        url, tag = str(h.dom.get("avatar") or "").strip(), "dom-avatar"
    main = h.html.get("main", "")
    if not url:
        if m := re.search(
            r'property=["\']og:image["\'][^>]+content=["\']' r'([^"\']+)', main
        ):
            url, tag = m.group(1).replace("&amp;", "&"), "og:image"
    if not url:
        for v in h.gql_strs(K_PIC):
            if v.startswith("http") and "fbcdn" in v:
                url, tag = v, "graphql-loose"
                break
    if url:
        row.mark("logo", tag)
        # whatever the source, fbcdn signs the crop range up to `cstp`, not
        # the tiny `ctp` thumbnail actually requested, this recovers the
        # real uploaded photo instead of a 40-60px snippet thumbnail
        row.profile_pic_url = hd_picture_url(url)
        row.has_custom_pic = not bool(RE_DEFAULT_PIC.search(url))
    elif RE_DEFAULT_PIC.search(main):
        row.has_custom_pic = False


def read_verified(row: Row, h: Harvest) -> None:
    # only ever set True on an actual detection, never write False, since
    # a scroll/settle timing miss on one visit must not erase a badge this
    # or an earlier visit already confirmed (see Row.verified's docstring).
    if h.dom.get("verified"):
        row.verified = True
        row.mark("verified", "dom-header")


def read_profile(row: Row, h: Harvest) -> None:
    """Everything available from the timeline visit."""
    read_name(row, h)
    read_counts(row, h)
    read_last_post(row, h)
    read_pic(row, h)
    read_verified(row, h)


# Scraper
# Drives a logged-in browser over Facebook profiles and reads their fields.
#
# This module owns the visit sequence. The browser session itself is
# discovery_engine.py's FacebookSession; turning what a visit collected into
# report fields is read_* above; holding and scoping the payloads is Harvest.


class Scraper:
    """One logged-in browser session, driven over a list of profiles."""

    # callers normalise URLs without knowing which platform they are holding
    normalize_url = staticmethod(normalize_url)

    def __init__(
        self,
        args,
        cookies: list[dict],
        session_id: str = "",
        proxy: Optional[dict] = None,
    ):
        self.a = args
        self.evidence = args.evidence or None  # GridFS key prefix, not a path
        # evidence screenshots need images, so the session must not block them
        self.session = FacebookSession(
            args,
            cookies,
            load_images=bool(self.evidence),
            session_id=session_id,
            proxy=proxy,
        )

    # ───────────────────────────── browser ────────────────────────────── #

    @property
    def ctx(self):
        return self.session.ctx

    async def start(self):
        await self.session.start()

    async def stop(self):
        await self.session.stop()

    async def pause(self, mult=1.0):
        await self.session.pause(mult)

    async def check_session(self) -> bool:
        return await self.session.check_session()

    # ─────────────────────────── page scripts ─────────────────────────── #

    # Ready when the profile's own payload has landed: the social-context block
    # plus, once we know it, the entity id. Everything we extract is present at
    # that point, typically under 2s, so waiting a fixed 3.5s is dead time.
    JS_READY = """
    (needle) => {
      let ctx = false, id = !needle;
      for (const el of document.querySelectorAll('script[type="application/json"]')) {
        const t = el.textContent || "";
        if (!ctx && t.includes('profile_social_context')) ctx = true;
        if (!id && t.includes('"id":"' + needle + '"')) id = true;
        if (ctx && id) return true;
      }
      return false;
    }
    """

    # The server render ships far more GraphQL in <script type="application/json">
    # than the XHR traffic does, that is where the profile entity lives.
    JS_EMBEDDED = (
        "() => Array.from(document.querySelectorAll("
        "'script[type=\"application/json\"]')).map(s => s.textContent)"
        ".filter(t => t && t.length > 40)"
    )

    # Reads the profile header itself: the name is the line directly above the
    # counter chip, the avatar is the largest <image> on the page, and
    # "set=pb.<id>." photo links give us the owner's numeric id even when the
    # URL is a vanity slug.
    JS_HEADER = """
    () => {
      const lines = (document.body.innerText || "").split("\\n").map(s => s.trim());
      let name = "", followers = "", counter = "";
      for (let i = 0; i < lines.length; i++) {
        // the header counter line: "70 followers - 8 following" on creators,
        // "53 friends" on personal profiles, "154M followers" on pages
        const m = /^([\\d][\\d.,\\s]{0,15}[KMB]?)\\s*(followers?|friends?|likes)\\b/i.exec(lines[i]);
        if (!m) continue;
        counter = lines[i];
        const fm = /([\\d][\\d.,\\s]{0,15}[KMB]?)\\s*followers?\\b/i.exec(lines[i]);
        if (fm) followers = fm[1];
        for (let j = i - 1; j >= 0 && j >= i - 4; j--) {
          const c = lines[j];
          if (c && c.length < 80 && !/^[\\d,.]+$/.test(c) &&
              !/notification|^search$|^facebook$|^add friend$|^follow$/i.test(c)) {
            name = c; break;
          }
        }
        break;
      }
      let postAuthor = "";
      for (const e of document.querySelectorAll('[aria-label]')) {
        const l = e.getAttribute('aria-label') || "";
        if (/^Actions for this post by /i.test(l)) {
          postAuthor = l.replace(/^Actions for this post by /i, "").trim();
          break;
        }
      }
      let avatar = "", best = -1;
      for (const im of document.querySelectorAll('svg image')) {
        const href = im.getAttribute('xlink:href') || im.getAttribute('href') || "";
        const w = im.getBoundingClientRect().width;
        if (href && w > best) { best = w; avatar = href; }
      }
      const pbIds = Array.from(document.querySelectorAll('a[href*="set=pb."]'))
        .map(a => ((a.getAttribute('href') || "").match(/set=pb\\.(\\d+)\\./) || [])[1])
        .filter(Boolean);
      // the real, platform-issued badge -- confirmed live against
      // facebook.com/facebook: <svg role="img" title="Verified account">
      const verified = !!document.querySelector('svg[title="Verified account"]');
      return {name, followers, counter, postAuthor, avatar, pbIds, verified};
    }
    """

    # ──────────────────────────── collection ──────────────────────────── #

    async def visit(
        self, page, url, h: Harvest, tag, scrolls=0, needle: Optional[str] = None
    ) -> bool:
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000
            )
        except Exception:
            return False
        try:
            if needle is not None:
                try:
                    await page.wait_for_function(
                        self.JS_READY, arg=needle, timeout=self.a.settle * 1000
                    )
                except Exception:
                    # gone, login-walled or an unusual layout, fall through and
                    # let the field readers and status checks report what they see
                    await page.wait_for_timeout(1500)
            else:
                await page.wait_for_timeout(1500)
            for _ in range(scrolls):
                await page.mouse.wheel(0, 2600)
                await page.wait_for_timeout(1200)
        except Exception:
            pass
        try:
            h.html[tag] = await page.content()
        except Exception:
            h.html[tag] = ""
        try:
            h.text[tag] = await page.inner_text("body")
        except Exception:
            h.text[tag] = re.sub(r"<[^>]+>", " ", h.html.get(tag, ""))
        try:
            h.add_embedded(await page.evaluate(self.JS_EMBEDDED))
        except Exception:
            pass
        return bool(h.html[tag])

    async def read_dom(self, page, h: Harvest, scrolled: bool = False) -> None:
        try:
            if scrolled:
                # scrolling can unmount the intro block, go back up first
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(700)
            h.dom = await page.evaluate(self.JS_HEADER) or {}
        except Exception:
            h.dom = {}

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
            # JS_READY (above) is a DATA-readiness check, it can pass while
            # the screen is still a bare loading splash, since it reads
            # embedded JSON, never the rendered page. See
            # Session.wait_for_visible_content for why this is separate.
            await self.session.wait_for_visible_content(page)
            data = await page.screenshot(full_page=False)
            from backend.database.repositories import evidence_repository
            await evidence_repository.save(key, data)
            row.screenshot = key
        except Exception:
            pass

    # ───────────────────────────── identity ───────────────────────────── #

    @staticmethod
    def entity_id_for(h: Harvest, url: str) -> str:
        """Numeric id for a vanity URL, taken from the payloads.

        The entity that owns the page carries its own canonical url/vanity, so
        matching the slug against those gives the id without trusting the DOM.
        """
        slug = profile_id(url).lower()
        if not slug or slug.isdigit():
            return ""
        for blob in h.mentioning(slug):
            for d in iter_dicts(blob):
                i = d.get("id")
                if not (isinstance(i, str) and i.isdigit()):
                    continue
                for k in ("url", "profile_url"):
                    v = d.get(k)
                    if isinstance(v, str) and v.lower().rstrip("/").endswith(
                        "/" + slug
                    ):
                        return i
                for k in ("vanity", "userVanity", "username"):
                    v = d.get(k)
                    if isinstance(v, str) and v.lower() == slug:
                        return i
        return ""

    @staticmethod
    def owner_id(h: Harvest) -> str:
        """Most frequent id in the profile's own photo-album links."""
        ids = h.dom.get("pbIds") or []
        return max(set(ids), key=ids.count) if ids else ""

    def resolve_id(self, row: Row, h: Harvest, url: str) -> str:
        """Vanity URLs carry no numeric id, ask the payloads, then the DOM."""
        if row.profile_id.isdigit():
            return row.profile_id
        pid = self.entity_id_for(h, url) or self.owner_id(h)
        if not pid:
            row.note("id unresolved -- fields not scope-verified")
        else:
            row.mark("id", pid)
            # adopt the numeric id as this row's identity. Discovery stores the
            # numeric id, so leaving the vanity slug here would file the same
            # profile twice, once per URL shape.
            row.profile_id = pid
        return pid

    @staticmethod
    def blocked_status(row: Row, page_url: str, txt: str) -> bool:
        """Session or availability problems that make field reading pointless."""
        if "/checkpoint" in page_url or RE_CHECKPOINT.search(txt):
            row.status, msg = "CHECKPOINT", "session checkpointed"
        elif "/login" in page_url or RE_LOGIN.search(txt):
            row.status, msg = "LOGIN_REQUIRED", "cookies rejected/expired"
        elif RE_GONE.search(txt):
            row.status = "GONE"
            msg = "removed or unavailable -- may already be taken down"
        else:
            return False
        row.note(msg)
        return True

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = profile_id(url)

        page = await self.ctx.new_page()
        h = Harvest()

        async def on_response(resp):
            try:
                if "/api/graphql" in resp.url and resp.request.resource_type in (
                    "xhr",
                    "fetch",
                ):
                    h.add_gql(await resp.text())
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            needle = row.profile_id if row.profile_id.isdigit() else ""
            if not await self.visit(
                page, url, h, "main", scrolls=self.a.scrolls, needle=needle
            ):
                row.status = "ERROR"
                row.note("main nav failed")
                return row

            txt = h.text.get("main", "")
            if self.blocked_status(row, page.url, txt):
                if row.status == "GONE":
                    read_name(row, h)
                return row

            await self.read_dom(page, h, scrolled=self.a.scrolls > 0)
            pid = self.resolve_id(row, h, url)
            hs = h.scoped(pid)

            # the URL itself is the most reliable signal for a group visit:
            # /groups/<id>/ is unambiguous, unlike sniffing a payload
            # __typename that may not even be present (see the Page check
            # right below, which exists precisely because that sniff is a
            # fallback, not a sure thing)
            if "/groups/" in url or (hs.ent_scalar("__typename") or "").lower() == "group" or (
                not hs.ents and re.search(r'"__typename"\s*:\s*"Group"', h.all_html())
            ):
                row.entity_type = "group"
            elif (hs.ent_scalar("__typename") or "").lower() == "page" or (
                not hs.ents
                and re.search(
                    r'"__typename"\s*:\s*"Page"|Page transparency', h.all_html()
                )
            ):
                row.entity_type = "page"

            read_profile(row, hs)
            await self.screenshot(page, row)

            # Payload timestamps first (read_profile -> read_last_post,
            # above); the rendered page second. Only runs when the payload
            # carried no usable post time, so a normal visit costs nothing
            # extra, and it reads an exact absolute timestamp out of the
            # permalink's aria-label rather than doing arithmetic on the "3d"
            # a human sees.
            #
            # Deliberately BEFORE the About-tab visits below, not after:
            # confirmed live this used to be dead code in production
            # (ScanOptions.about defaults False, but the two About-tab page
            # visits happen unconditionally regardless, only the FIELD
            # READ off them is opt-in). By the time this used to run at the
            # end of process(), page.url had already moved to
            # ".../about", a page with no post permalinks at all, so this
            # fallback always found nothing and never actually fell back to
            # anything. Running it here, while the page is still the
            # timeline that was just visited, is what makes it work.
            if not row.last_post_iso and row.posts_seen != "no":
                iso = await dom_last_post(page)
                if iso:
                    row.last_post_iso = iso
                    row.posts_seen = "yes"
                    row.mark("last_post", "dom-aria")

            # The main profile page rarely carries a location. Facebook Pages
            # put their city/country on the About tab instead, so this visit
            # happens unconditionally: accuracy on a field the report actually
            # promises beats saving one page load. Join date is a different
            # story. Facebook does not expose it to an ordinary session at
            # all, not in the rendered tab and not in any payload, so that
            # attempt alone stays opt-in via --about since it essentially
            # never succeeds and isn't worth chasing by default.
            await self.pause(0.4)
            for sk in ("about_profile_transparency", "about"):
                await self.visit(page, tab_url(url, sk), h, sk)
                if self.a.about:
                    read_created(row, h.scoped(pid))
                    if row.created_iso:
                        break
                await self.pause(0.3)
            if self.a.about and not row.created_iso:
                row.note("join date not visible on About this account")
            elif not self.a.about:
                row.note("join date not attempted (pass --about)")

            read_location(row, h.scoped(pid))

            row.status = "OK" if row.profile_name else "PARTIAL"
            return row
        finally:
            try:
                await page.close()
            except Exception:
                pass

    # ─────────────────────────── orchestration ────────────────────────── #

    async def one(self, u: str, tgt: str, feed: str) -> Row:
        """process() with a failed profile turned into a reportable row."""
        try:
            return await self.process(u, tgt, feed)
        except Exception as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.profile_id = profile_id(row.url)
            row.status = "ERROR"
            row.note(f"{type(e).__name__}: {e}")
            return row

    @staticmethod
    def report(i: int, total: int, u: str, row: Row) -> None:
        from backend.shared.logging import get_logger as _gl
        _log = _gl("platforms.facebook.analysis")
        _log.info(
            f"[{i}/{total}] {u} | {row.status} name={row.profile_name[:22]} "
            f"followers={row.followers if row.followers is not None else '-'} "
            f"friends={row.friends if row.friends is not None else '-'} "
            f"active={row.active_yes or '-'} risk={row.risk} {row.priority}"
        )

    async def run(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        from backend.shared.logging import get_logger as _gl
        _run_log = _gl("platforms.facebook.analysis")
        if getattr(self.a, "concurrency", 1) > 1:
            return await self.run_parallel(jobs)
        rows: list[Row] = []
        for i, (u, tgt, feed) in enumerate(jobs, 1):
            row = await self.one(u, tgt, feed)
            rows.append(row)
            self.report(i, len(jobs), u, row)
            if row.status == "CHECKPOINT" and not self.a.keep_going:
                _run_log.warning("CHECKPOINT -- aborting to avoid burning the session.")
                break
            if i < len(jobs):
                await self.pause()
        return rows

    async def run_parallel(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        """Several tabs at once, same session. Faster, and more conspicuous."""
        sem = asyncio.Semaphore(self.a.concurrency)
        done = 0

        async def worker(idx: int, job: tuple[str, str, str]) -> tuple[int, Row]:
            nonlocal done
            async with sem:
                await asyncio.sleep(idx % self.a.concurrency * 1.5)  # stagger
                row = await self.one(*job)
                done += 1
                self.report(done, len(jobs), job[0], row)
                await self.pause(0.5)
                return idx, row

        pairs = await asyncio.gather(*(worker(i, j) for i, j in enumerate(jobs)))
        return [r for _, r in sorted(pairs, key=lambda p: p[0])]
