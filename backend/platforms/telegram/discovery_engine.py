"""Telegram discovery engine: search and entity extraction -- keywords in,
candidate users/channels/groups out, over MTProto (Telethon).

Also owns the MTProto connection itself (the `Telegram` class): it is
produced here first and re-used by analysis_engine.py, which imports it
rather than redefining it, so there is exactly one definition across the two
files.

Telegram publishes the real protocol, so there is no browser, no page to
render and nothing to intercept -- this talks the same wire the official
clients do. That makes it the most accurate surface of the six and, like
YouTube, one with no fingerprint to detect.

WHAT LIMITS IT is FloodWait, not rate limiting: ask too fast and the server
replies "wait N seconds" rather than blocking you. That is a first-class
signal here -- it is surfaced as a checkpoint so a run stops rather than
digging the account deeper into a limit.

AUTH is a saved session plus the account's api_id/api_hash. Logging in needs a
phone code, which is interactive, so this never attempts it: an unauthorised
session is reported, not repaired.

Telegram's global search returns a single capped page per keyword -- there is
no cursor and no "load more" -- so a sweep is one request and is genuinely
complete when it returns. Unlike the browser platforms there is nothing to
scroll and nothing to miss. A sweep is only incomplete if Telegram asked us
to wait (FloodWait), which is reported rather than retried.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.utils.logging import get_logger
from backend.platforms.facebook.discovery_engine import Hit

log = get_logger("telegram")

try:
    from telethon import TelegramClient
    from telethon.errors import (ChannelPrivateError, FloodWaitError,
                                 UsernameInvalidError,
                                 UsernameNotOccupiedError)
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.contacts import SearchRequest
    from telethon.tl.functions.users import GetFullUserRequest

    HAVE_TELETHON = True
except ImportError:  # pragma: no cover
    HAVE_TELETHON = False
    TelegramClient = None  # type: ignore

    class FloodWaitError(Exception):  # type: ignore
        seconds = 0

    class ChannelPrivateError(Exception):  # type: ignore
        pass

    class UsernameInvalidError(Exception):  # type: ignore
        pass

    class UsernameNotOccupiedError(Exception):  # type: ignore
        pass


# ─────────────────────────── connection / auth state ───────────────────────


class NotAuthorised(RuntimeError):
    """The saved session is not logged in. Only an interactive login fixes it."""


class FloodWait(RuntimeError):
    """Telegram asked us to slow down. Treated like a checkpoint."""

    def __init__(self, seconds: int):
        super().__init__(f"telegram asked for a {seconds}s pause")
        self.seconds = seconds


@dataclass
class TelegramEntity:
    """A user, channel or group, flattened to what the report needs."""

    entity_id: str = ""
    username: str = ""
    title: str = ""
    kind: str = "profile"  # profile | channel | group
    members: Optional[int] = None
    about: str = ""
    created_iso: str = ""  # channels carry a creation date; users do not
    verified: bool = False
    scam: bool = False
    restricted: bool = False
    avatar: str = ""
    has_photo: bool = False
    last_post_iso: str = ""

    @property
    def url(self) -> str:
        if self.username:
            return f"https://t.me/{self.username}"
        return f"https://t.me/c/{self.entity_id}" if self.entity_id else ""


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    return ""


def entity_from(obj: Any) -> Optional[TelegramEntity]:
    """Telethon User/Channel/Chat -> TelegramEntity."""
    if obj is None:
        return None
    username = getattr(obj, "username", "") or ""
    if title := getattr(obj, "title", ""):
        broadcast = bool(getattr(obj, "broadcast", False))
        kind = "channel" if broadcast else "group"
        name = title
    else:
        first = getattr(obj, "first_name", "") or ""
        last = getattr(obj, "last_name", "") or ""
        name = f"{first} {last}".strip()
        kind = "profile"
    if not username and not name:
        return None
    # Telethon's own signal for "does this entity actually have a photo" --
    # both User and Chat/Channel report a *PhotoEmpty variant when there is
    # none, so guessing from the avatar URL (which resolves for any public
    # username regardless of whether a photo exists) was never accurate.
    photo = getattr(obj, "photo", None)
    has_photo = photo is not None and "Empty" not in type(photo).__name__
    return TelegramEntity(
        entity_id=str(getattr(obj, "id", "") or ""),
        username=username,
        title=name,
        kind=kind,
        members=getattr(obj, "participants_count", None),
        # channels expose their creation date; user accounts never do
        created_iso=_iso(getattr(obj, "date", None)) if kind != "profile" else "",
        verified=bool(getattr(obj, "verified", False)),
        scam=bool(getattr(obj, "scam", False)),
        restricted=bool(getattr(obj, "restricted", False)),
        avatar=f"https://t.me/i/userpic/320/{username}.jpg" if username and has_photo else "",
        has_photo=has_photo,
    )


class Telegram:
    """A connected MTProto session. One at a time -- the session file is a lock."""

    def __init__(self, options=None):
        if not HAVE_TELETHON:
            raise RuntimeError("pip install telethon")
        self.o = options
        self.api_id = int(os.environ.get("TELEGRAM_API_ID", "0") or 0)
        self.api_hash = os.environ.get("TELEGRAM_API_HASH", "")
        from backend.config.settings import settings

        self.session_file = str(settings.session_blob_path / "telegram")
        self.client: Any = None

    async def start(self) -> None:
        if not (self.api_id and self.api_hash):
            raise NotAuthorised("TELEGRAM_API_ID / TELEGRAM_API_HASH not set")
        self.client = TelegramClient(self.session_file, self.api_id, self.api_hash)
        # connect(), never start(): start() prompts for a phone code on stdin
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.stop()
            raise NotAuthorised(
                "telegram session is not logged in -- run an interactive login "
                "once to create session/telegram.session"
            )

    async def stop(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    async def check_session(self) -> bool:
        try:
            me = await self.client.get_me()
        except Exception as e:
            log.error(f"session check failed: {type(e).__name__}: {e}")
            return False
        if me is None:
            return False
        log.info(f"session valid -> @{me.username or me.id}")
        return True

    async def _call(self, request):
        """Every request goes through here so FloodWait is handled in one place."""
        try:
            return await self.client(request)
        except FloodWaitError as e:
            raise FloodWait(int(getattr(e, "seconds", 0))) from e

    async def search(self, keyword: str, limit: int = 50) -> list[TelegramEntity]:
        """Global search. Telegram returns one capped page -- there is no cursor."""
        res = await self._call(SearchRequest(q=keyword, limit=limit))
        out: list[TelegramEntity] = []
        for obj in list(getattr(res, "users", [])) + list(getattr(res, "chats", [])):
            if ent := entity_from(obj):
                out.append(ent)
        return out

    async def resolve(self, username: str) -> Optional[TelegramEntity]:
        """@name -> entity, with the detail that needs a second call filled in."""
        try:
            obj = await self.client.get_entity(username)
        except (UsernameInvalidError, UsernameNotOccupiedError, ValueError):
            return None
        except FloodWaitError as e:
            raise FloodWait(int(getattr(e, "seconds", 0))) from e
        except ChannelPrivateError:
            return None

        ent = entity_from(obj)
        if ent is None:
            return None

        try:
            if ent.kind == "profile":
                full = await self._call(GetFullUserRequest(obj))
                ent.about = (getattr(full.full_user, "about", "") or "").strip()
            else:
                full = await self._call(GetFullChannelRequest(obj))
                chat = full.full_chat
                ent.about = (getattr(chat, "about", "") or "").strip()
                if ent.members is None:
                    ent.members = getattr(chat, "participants_count", None)
        except FloodWait:
            raise
        except Exception as e:
            log.debug(f"no full detail for @{username}: {type(e).__name__}")

        ent.last_post_iso = await self.last_post(obj)
        return ent

    async def last_post(self, entity: Any) -> str:
        """Newest message date. Users' own messages are not readable; channels' are."""
        try:
            msgs = await self.client.get_messages(entity, limit=1)
        except FloodWaitError as e:
            raise FloodWait(int(getattr(e, "seconds", 0))) from e
        except Exception:
            return ""
        if not msgs:
            return ""
        return _iso(getattr(msgs[0], "date", None))

    async def pause(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


# ───────────────────────────── crawling / search ────────────────────────────

# global search caps well below this; asking for more costs nothing
SEARCH_LIMIT = 100


@dataclass
class Sweep:
    keyword: str
    tab: str = "all"
    hits: list[Hit] = field(default_factory=list)
    entities: list[TelegramEntity] = field(default_factory=list)
    pages: int = 0
    stopped: str = ""
    complete: bool = False
    seconds: float = 0.0
    error: str = ""

    def summary(self) -> str:
        return f"{len(self.hits)} hits, {self.stopped}"


class Discovery:
    """`ctx` is accepted and unused -- MTProto needs no browser."""

    def __init__(self, args, ctx=None):
        self.a = args
        self.tg: Telegram | None = None

    async def sweep(self, keyword: str, tab: str = "all") -> Sweep:
        out = Sweep(keyword=keyword, tab=tab)
        started = time.time()
        try:
            found = await self.tg.search(keyword, SEARCH_LIMIT)
            out.pages = 1
            out.entities = found
            out.hits = [
                Hit(
                    entity_id=e.entity_id or e.username,
                    name=e.title,
                    url=e.url,
                    avatar=e.avatar,
                    # Telethon's own PhotoEmpty check -- see the Telegram
                    # class above -- not a guess from whether the avatar URL
                    # happens to resolve
                    has_custom_pic=e.has_photo,
                    entity_type=e.kind,
                    keyword=keyword,
                    tab=e.kind,
                    rank=i,
                    source="mtproto",
                )
                for i, e in enumerate(found)
                if e.url
            ]
            if self.a.max_results:
                out.hits = out.hits[: self.a.max_results]
            out.stopped, out.complete = "exhausted", True
        except FloodWait as e:
            out.stopped, out.error = "flood-wait", str(e)
        except Exception as e:
            out.stopped, out.error = "error", f"{type(e).__name__}: {e}"
        finally:
            out.seconds = time.time() - started
        return out

    async def run(self, keywords: list[str], tabs=None) -> list[Sweep]:
        """Sequential on purpose: one session, and search is what gets limited."""
        self.tg = Telegram(self.a)
        sweeps: list[Sweep] = []
        try:
            await self.tg.start()
            if not await self.tg.check_session():
                raise NotAuthorised("telegram session rejected")
            for i, keyword in enumerate(keywords):
                s = await self.sweep(keyword)
                sweeps.append(s)
                print(
                    f"  [telegram] {keyword!r}: {s.summary()} ({s.seconds:.1f}s)",
                    file=sys.stderr,
                )
                if s.stopped == "flood-wait":
                    print(
                        "  telegram asked us to slow down -- stopping the sweep",
                        file=sys.stderr,
                    )
                    break
                if i < len(keywords) - 1:
                    await asyncio.sleep(2.0)  # unhurried between searches
        finally:
            await self.tg.stop()
        return sweeps


def merge(sweeps: list[Sweep]) -> list[Hit]:
    seen: dict[str, Hit] = {}
    for s in sweeps:
        for h in s.hits:
            seen.setdefault(h.entity_id or h.url, h)
    return list(seen.values())
