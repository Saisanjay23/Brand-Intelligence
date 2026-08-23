"""One-off migration: re-judge stored rows against the CURRENT completeness
rules, so rows held in the retry queue by a rule that has since changed can
leave it without being re-scraped.

WHY THIS IS NEEDED
    `analysis_complete` is computed once, at analysis time, and then stored.
    When `shared/completeness.py` learns that a field is structurally
    unobtainable on a platform, every row already on disk keeps the older,
    stricter verdict -- and cannot correct itself, because a row over
    MAX_ANALYSIS_ATTEMPTS is excluded by `urls_for` and so is never
    re-analysed. The stale verdict is therefore permanent without this.

    Measured live 2026-08-23, before the rule change: 86 rows in the retry
    queue, every one `analysis_status: OK`. Eleven of them were held
    exclusively by a screenshot that their platform cannot take (telegram
    speaks MTProto, youtube is the official Data API -- neither analysis
    engine has a screenshot method), plus one Telegram USER row held by a
    member count and a message feed the protocol does not expose for users.

WHAT IT WILL AND WILL NOT CLEAR
    Only rows it can judge CONFIDENTLY from what is on disk.

    `notes` is not persisted to Mongo, and two carve-outs read it -- the
    Facebook "publishes no audience count" case and private/protected
    timelines. A naive recompute would therefore mark those rows as still
    missing their followers and keep them queued. So the stored
    `field_status` verdict is honoured as authoritative wherever it exists:
    a field already recorded as `none-exist` or `not-collected` is treated
    as settled, because that verdict was written by a run that COULD see
    the notes.

    A row with no `field_status` at all (written before that feature) and a
    genuinely blank field is left exactly as it is. It needs a real
    re-visit, which this script deliberately does not trigger.

    Rows it does clear also get `analysis_attempts` reset to 0, matching
    what `profile_repository.save()` does for any complete reading -- so a
    row that later goes incomplete starts from a full budget rather than an
    already-exhausted one.

Idempotent: re-running only ever re-derives the same verdict from the same
stored fields, so a second run reports 0 changed.

Usage:
    python -m backend.database.migrations.migrate_recompute_completeness --dry-run
    python -m backend.database.migrations.migrate_recompute_completeness
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient

from backend.config.settings import settings
from backend.shared.completeness import field_report, missing_fields
from backend.shared.models.row import Row

# A stored per-field verdict meaning "settled, not a miss". Written by a run
# that could still see the row's notes, which this script cannot.
SETTLED = ("read", "none-exist", "not-collected")

# missing_fields' label for a field -> the field_status key it corresponds
# to, so a settled verdict can cancel a recomputed "missing".
LABEL_TO_KEY = {
    "display name": "display_name",
    "followers": "followers",
    "last post date": "last_post_date",
    "screenshot": "screenshot",
}


def _row_from(doc: dict) -> Row:
    """Rebuild enough of a Row from a stored document for the completeness
    rules to run. `notes` cannot be recovered (never persisted); the stored
    field_status stands in for it -- see the module docstring."""
    row = Row(url=doc.get("url", ""), target=doc.get("target", ""),
              original_feed=doc.get("official_feed", ""))
    row.status = doc.get("analysis_status") or "OK"
    row.entity_type = doc.get("entity_type") or "profile"
    row.profile_name = doc.get("display_name") or ""
    row.followers = doc.get("followers")
    row.friends = doc.get("friends")
    row.location = doc.get("location") or ""
    row.last_post_iso = doc.get("last_post_date") or ""
    row.posts_seen = doc.get("posts_seen") or ""
    row.screenshot = doc.get("screenshot") or ""
    return row


def _still_missing(doc: dict, platform: str, row: Row) -> list[str]:
    """Fields the current rules call missing, minus any the stored verdict
    already settled."""
    stored = doc.get("field_status") or {}
    out = []
    for label in missing_fields(platform, row, want_screenshot=True):
        key = LABEL_TO_KEY.get(label, label)
        if stored.get(key) in SETTLED:
            continue
        out.append(label)
    return out


async def migrate(dry_run: bool) -> None:
    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    coll = client[settings.mongo_db_name]["profiles"]

    q = {"phase": "analysis", "analysis_complete": False}
    total = await coll.count_documents(q)
    print(f"incomplete analysed rows: {total}\n")

    cleared = Counter()
    kept = Counter()
    kept_why: Counter = Counter()

    async for doc in coll.find(q):
        platform = doc.get("platform") or ""
        row = _row_from(doc)
        remaining = _still_missing(doc, platform, row)
        if remaining:
            kept[platform] += 1
            kept_why[(platform, tuple(remaining))] += 1
            continue

        cleared[platform] += 1
        if not dry_run:
            await coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "analysis_complete": True,
                    "analysis_attempts": 0,
                    "field_status": field_report(platform, row, want_screenshot=True),
                }},
            )

    verb = "would clear" if dry_run else "cleared"
    print(f"=== {verb} (leave the retry queue) ===")
    for p, n in cleared.most_common():
        print(f"   {p:<10} {n}")
    print(f"   {'TOTAL':<10} {sum(cleared.values())}")

    print("\n=== still incomplete (need a real re-visit) ===")
    for p, n in kept.most_common():
        print(f"   {p:<10} {n}")
    print(f"   {'TOTAL':<10} {sum(kept.values())}")

    if kept_why:
        print("\n   why:")
        for (p, fields), n in kept_why.most_common(12):
            print(f"     {p:<10} x{n:<4} {', '.join(fields)}")

    client.close()
    print("\ndry run complete -- nothing written." if dry_run else "\nmigration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args.dry_run))
    except KeyboardInterrupt:
        sys.exit(1)
