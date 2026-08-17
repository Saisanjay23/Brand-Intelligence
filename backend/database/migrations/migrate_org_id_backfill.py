"""One-off migration: the earlier DDD-era `backend` schema (clients
scoped under an org, one Mongo database per platform) -> the current flat
schema (one `client_id`, one `brand_intel` database, `platform` as a
document field).

Read-only against the OLD databases (`{prefix}_core.clients`, each
platform's own `{prefix}_{platform}.profiles` collection), this script
never writes to or deletes from anything there. It only adds to the NEW
`brand_intel` database's `clients`/`profiles` collections.

The org tier is dropped entirely: every org's clients are flattened into
one `clients` collection, keyed by the DDD client's own generated `_id`
(kept as-is, it's already a stable, unique id). If your SaaS backend
wants that id to line up with its own customer id instead, reconcile it
afterwards with `POST /clients` using the SaaS's id and this client's name.

Idempotent: every write is an upsert keyed on the original document's own
`_id` (for both clients and profiles), so running this twice is safe and
just no-ops the second time.

Usage:
    python -m backend.database.migrations.migrate_org_id_backfill --dry-run
    python -m backend.database.migrations.migrate_org_id_backfill

`--dry-run` only reads and reports counts; nothing is written.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from motor.motor_asyncio import AsyncIOMotorClient

from backend.platforms.registry import PLATFORMS
from backend.config.settings import settings

OLD_DB_PREFIX = "brand_intel"  # the DDD-era backend's own db prefix


async def migrate(dry_run: bool) -> None:
    client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    old_core = client[f"{OLD_DB_PREFIX}_core"]
    new_db = client[settings.mongo_db_name]

    old_clients = await old_core["clients"].find({}).to_list(length=10_000)
    print(f"found {len(old_clients)} client(s) in the old '{OLD_DB_PREFIX}_core.clients'")

    if not old_clients:
        print("nothing to migrate -- no old clients found.")
        client.close()
        return

    # 1) migrate clients, org tier dropped, the client's own _id becomes client_id
    for old in old_clients:
        client_id = old["_id"]
        existing = await new_db["clients"].find_one({"_id": client_id})
        if existing:
            print(f"  client {old.get('name')!r} ({client_id}): already migrated")
            continue
        print(f"  client {old.get('name')!r}: {'[dry-run] would create' if dry_run else 'creating'} -> {client_id}")
        if not dry_run:
            await new_db["clients"].update_one(
                {"_id": client_id},
                {"$set": {
                    "name": old.get("name", ""), "keywords": old.get("keywords", []),
                    "cron": old.get("cron"), "created_at": old.get("created_at"),
                }},
                upsert=True,
            )

    # 2) migrate each platform's profiles out of its own database into the
    #    single new database, stamping `platform` on the way in
    for platform_id in PLATFORMS:
        old_db = client[f"{OLD_DB_PREFIX}_{platform_id}"]
        old_coll_names = await old_db.list_collection_names()
        if "profiles" not in old_coll_names:
            continue

        total = await old_db["profiles"].count_documents({})
        if total == 0:
            continue
        print(f"\n{platform_id}: {total} profile(s) in the old '{OLD_DB_PREFIX}_{platform_id}.profiles'")

        migrated = already_present = 0
        new_coll = new_db["profiles"]
        async for doc in old_db["profiles"].find({}):
            new_doc = dict(doc)
            new_doc["platform"] = platform_id
            new_doc.pop("org_id", None)

            if await new_coll.find_one({"_id": doc["_id"]}):
                already_present += 1
                continue

            migrated += 1
            if not dry_run:
                await new_coll.insert_one(new_doc)

        verb = "would migrate" if dry_run else "migrated"
        print(f"  {platform_id}: {verb} {migrated}, already present {already_present}")

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
