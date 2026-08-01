"""Turning a Harvest into a Row: one function per report field.

Every reader follows the same order -- the profile's own GraphQL entity first,
then the rendered header, then progressively looser fallbacks -- and records
which one answered via `row.mark()`. That provenance is what makes a filled
cell auditable and a blank one meaningful.

These are pure functions of (row, harvest): no browser, no network. That is
what makes them testable against a saved payload.
"""

from __future__ import annotations

import re

from backend.engine.row import Row
from backend.shared.text import (epoch_to_dt, find_ints, is_place, name_score,
                               parse_count, parse_joined)
from backend.platforms.facebook.analysis.constants import (K_FOLLOWERS,
                                                           K_JOINED,
                                                           K_LOCATION, K_NAME,
                                                           K_PIC, K_POST_TIME,
                                                           MAX_FOLLOWERS,
                                                           RE_CHIP,
                                                           RE_DEFAULT_PIC,
                                                           RE_FOLLOWERS,
                                                           RE_FROM, RE_JOINED,
                                                           RE_LIVES_IN,
                                                           RE_NO_POSTS)
from backend.platforms.facebook.analysis.harvest import Harvest
from backend.platforms.facebook.urls import hd_picture_url

GENERIC_NAMES = {"facebook", "notifications"}


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
    """One header counter -- '154M followers', '53 friends', '1.2K likes'.

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


def read_counts(row: Row, h: Harvest) -> None:
    """Followers and friends -- whichever the profile publishes."""
    for s in h.ent_social():
        take_chip(row, s, "graphql-social-context")
    # the header line holds the same counters when the entity is unreadable
    for part in re.split(r"[•·|]", str(h.dom.get("counter") or "")):
        take_chip(row, part.strip(), "dom-header")
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


def read_last_post(row: Row, h: Harvest) -> None:
    # the entity's timeline_list_feed_units are this profile's own stories,
    # so their creation_time needs no filtering; the regex fallback over
    # raw payloads does, hence the scoped view and main-tab-only HTML.
    stamps, tag = h.ent_ints(K_POST_TIME), "graphql"
    if not stamps:
        stamps = find_ints(h.gql_raw() + h.html.get("main", ""), K_POST_TIME)
        tag = "payload-regex"
    dts = [epoch_to_dt(t) for t in stamps]
    dts = [d for d in dts if d]
    # a join/creation date can surface under creation_time -- drop it
    if row.created_iso:
        dts = [d for d in dts if not d.date().isoformat().startswith(row.created_iso)]
    if dts:
        row.last_post_iso = max(dts).date().isoformat()
        row.posts_seen = "yes"
        row.mark("last_post", tag)
    elif RE_NO_POSTS.search(h.all_text()):
        row.posts_seen = "no"
        row.mark("last_post", "no-posts-notice")


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
    # the entity's own picture, else the header avatar -- not whichever
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
        # the tiny `ctp` thumbnail actually requested -- this recovers the
        # real uploaded photo instead of a 40-60px snippet thumbnail
        row.profile_pic_url = hd_picture_url(url)
        row.has_custom_pic = not bool(RE_DEFAULT_PIC.search(url))
    elif RE_DEFAULT_PIC.search(main):
        row.has_custom_pic = False


def read_profile(row: Row, h: Harvest) -> None:
    """Everything available from the timeline visit."""
    read_name(row, h)
    read_counts(row, h)
    read_last_post(row, h)
    read_pic(row, h)
