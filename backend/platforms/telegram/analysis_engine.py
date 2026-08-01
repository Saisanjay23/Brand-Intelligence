"""Telegram analysis engine: validation -- t.me URL -> scored Row, over
MTProto.

The MTProto connection (`Telegram`) and its entity/error types live in
discovery_engine.py (imported below) since discovery produces/needs them
first; this file owns URL normalization for the analysis entry point and the
drive loop (Scraper).

Resolving a username gives the entity, one full-detail call gives the
description and member count, and the newest message gives the activity date.

WHAT TELEGRAM GIVES that the browser platforms do not: channels and groups
carry a real creation date, so the Created Date column is filled for them.
User accounts have no such field anywhere in the protocol, so it stays blank
for people -- the honest answer rather than a guess.
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from backend.shared.models.row import Row
from backend.shared.text import fmt_created, name_score
from backend.platforms.telegram.discovery_engine import (FloodWait,
                                                          NotAuthorised,
                                                          Telegram,
                                                          TelegramEntity)

BAD_SEGMENTS = {"s", "c", "joinchat", "addstickers", "share", "proxy", "i"}


def normalize_url(url: str) -> str:
    url = (url or "").strip().strip("\"'")
    if not url:
        return ""
    if url.startswith("@"):
        return f"https://t.me/{url[1:]}"
    if not url.startswith(("http://", "https://", "tg://")):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    if host in ("telegram.me", "telegram.dog", "t.me"):
        host = "t.me"
    return f"https://{host}{p.path.rstrip('/')}"


def username_of(url: str) -> str:
    seg = [s for s in urlparse(normalize_url(url)).path.split("/") if s]
    if not seg:
        return ""
    name = seg[0].lstrip("@")
    if name.lower() in BAD_SEGMENTS:
        # https://t.me/s/<name> is the web preview of the same channel
        return seg[1] if name.lower() == "s" and len(seg) > 1 else ""
    return name


class Scraper:
    """Same surface as the browser scanners; MTProto behind it."""

    normalize_url = staticmethod(normalize_url)

    def __init__(self, args, cookies=None, session_id: str = "", proxy=None):
        # MTProto, not cookies -- session_id/proxy exist only so jobs.py can
        # call every platform's Scraper with the same signature.
        self.a = args
        self.tg = Telegram(args)

    async def start(self):
        await self.tg.start()

    async def stop(self):
        await self.tg.stop()

    async def pause(self, mult: float = 1.0):
        await self.tg.pause(getattr(self.a, "delay", 2.0) * mult)

    async def check_session(self) -> bool:
        try:
            return await self.tg.check_session()
        except NotAuthorised as e:
            print(f"SESSION: {e}", file=sys.stderr)
            return False

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        username = username_of(url)
        row.profile_id = username

        if not username:
            row.status = "ERROR"
            row.note("no @username in the URL -- private links cannot be resolved")
            return row

        ent = await self.tg.resolve(username)
        if ent is None:
            row.status = "GONE"
            row.note("no such user or channel -- may already be taken down")
            return row

        self.fill(row, ent)
        row.status = "OK" if row.profile_name else "PARTIAL"
        return row

    @staticmethod
    def fill(row: Row, e: TelegramEntity) -> None:
        row.profile_id = e.username or e.entity_id
        row.entity_type = e.kind
        row.profile_name = e.title
        row.mark("name", "mtproto")
        row.name_score = name_score(e.title, row.target)

        if e.members is not None:
            # members are this platform's reach figure; exact, from the protocol
            row.followers = e.members
            row.followers_exact = "yes"
            row.mark("followers", "mtproto")
        if e.created_iso:
            row.created_iso = e.created_iso
            row.mark("created", "mtproto")
        elif e.kind == "profile":
            row.note("telegram exposes no creation date for user accounts")
        if e.avatar:
            row.profile_pic_url = e.avatar
        # has_photo comes straight from Telethon's own PhotoEmpty check, not
        # a guess from whether the avatar URL happened to resolve
        row.has_custom_pic = e.has_photo
        row.mark("logo", "mtproto")
        if e.last_post_iso:
            row.last_post_iso = e.last_post_iso
            row.posts_seen = "yes"
            row.mark("last_post", "mtproto")
        if e.about:
            row.note(f"bio: {e.about[:120]}")
        if e.verified:
            row.note("verified by telegram")
        if e.scam:
            row.note("FLAGGED BY TELEGRAM AS SCAM")
        if e.restricted:
            row.note("restricted by telegram")

    # ─────────────────────────── orchestration ────────────────────────── #

    async def one(self, u: str, tgt: str, feed: str) -> Row:
        try:
            return await self.process(u, tgt, feed)
        except FloodWait as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.profile_id = username_of(row.url)
            row.status = "CHECKPOINT"  # stop the run, same as a challenge
            row.note(str(e))
            return row
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
            f"members={row.followers if row.followers is not None else '-':<9} "
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
            if row.status == "CHECKPOINT":
                print(
                    "\nFLOOD WAIT -- stopping to protect the account.", file=sys.stderr
                )
                break
            if i < len(jobs):
                await self.pause()
        return rows
