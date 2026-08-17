"""Where a sweep left off, so the next run can resume instead of restarting.

One document per (client, platform, keyword, tab): the last pagination
cursor that sweep saw, and whether it had reached the end. A broad keyword
whose results run past one time budget is otherwise capped forever at
whatever one budget reaches, because every re-run starts at page 1 again
(see platforms/replay.py's own module docstring for why the tail was
previously unreachable).

Only the CURSOR is stored, never the captured request it came from: the
signed request carries session-scoped tokens that expire within hours, so
persisting them would be both useless and a credential at rest. A cursor is
an opaque pagination pointer, not a credential.

TTL-bounded at 7 days. A cursor much older than that is likely to have been
invalidated by the platform reranking its own results anyway, and a stale
cursor that silently returns nothing is worse than starting over -- so it
expires rather than being trusted indefinitely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.database.connection import db

CURSORS = "sweep_cursors"
RETENTION_DAYS = 7


def _key(client_id: str, platform: str, keyword: str, tab: str) -> dict:
    # keyword is matched case-insensitively at the callers' level by being
    # normalised here; two analysts typing "Acme" and "acme" are searching
    # for the same thing and must share one resume position rather than
    # quietly keeping two half-finished ones.
    return {
        "client_id": str(client_id),
        "platform": str(platform),
        "keyword": str(keyword).strip().lower(),
        "tab": str(tab),
    }


async def get(client_id: str, platform: str, keyword: str, tab: str) -> Optional[dict]:
    """The stored resume point, or None. Never raises: resume is an
    optimisation, and a database hiccup must degrade to "sweep from the
    start", never to a failed job."""
    try:
        return await db()[CURSORS].find_one(_key(client_id, platform, keyword, tab))
    except Exception:
        return None


async def save(
    client_id: str, platform: str, keyword: str, tab: str,
    *, cursor: str, page_number: Optional[int] = None, exhausted: bool = False,
) -> None:
    """Record where this sweep stopped.

    `exhausted` means the platform said it had no next page -- the next run
    should start fresh rather than resume, because there is nothing past
    this point to resume INTO. Storing that explicitly (rather than deleting
    the row) keeps the fact visible for debugging "why did this keyword
    restart from the top".
    """
    try:
        await db()[CURSORS].update_one(
            _key(client_id, platform, keyword, tab),
            {"$set": {
                "cursor": str(cursor or ""),
                "page_number": page_number,
                "exhausted": bool(exhausted),
                "ts": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception:
        pass  # never let bookkeeping fail a sweep that already did its work


async def clear(client_id: str, platform: str, keyword: str, tab: str) -> None:
    try:
        await db()[CURSORS].delete_one(_key(client_id, platform, keyword, tab))
    except Exception:
        pass


async def delete_for_client(client_id: str) -> int:
    """Part of the client-deletion cascade, same as the other repositories."""
    try:
        res = await db()[CURSORS].delete_many({"client_id": str(client_id)})
        return res.deleted_count
    except Exception:
        return 0


async def ensure_indexes() -> None:
    await db()[CURSORS].create_index(
        [("client_id", 1), ("platform", 1), ("keyword", 1), ("tab", 1)],
        unique=True, name="sweep_position",
    )
    await db()[CURSORS].create_index(
        "ts", expireAfterSeconds=RETENTION_DAYS * 86400, name="ttl_ts",
    )
