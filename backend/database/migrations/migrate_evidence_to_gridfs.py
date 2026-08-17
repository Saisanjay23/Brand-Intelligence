"""One-off migration: evidence screenshots under `settings.evidence_path`
(loose PNG files on this server's disk) -> Mongo GridFS.

The key each file is uploaded under is exactly the string a profile document
already stores in its `screenshot` field (the path relative to
`evidence_path`, e.g. `acme/facebook/100012345.png`), see
database/repositories/evidence_repository.py. No profile documents need to
change at all; only where the bytes physically live moves. Because of that,
this is safe to run without cross-referencing Mongo at all: it just walks
the directory and uploads whatever it finds under the same relative key.

Read-only against the files, nothing on disk is modified or deleted here.
Delete `evidence_path` yourself once you've confirmed the migration worked
(e.g. by loading a few `GET /profiles/{id}/screenshot` responses).

Idempotent: re-running overwrites (same content, same key) rather than
duplicating, `evidence_repository.save()` already replaces any existing
capture at a given key.

Usage:
    python -m backend.database.migrations.migrate_evidence_to_gridfs --dry-run
    python -m backend.database.migrations.migrate_evidence_to_gridfs
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from backend.config.settings import settings
from backend.database.repositories import evidence_repository


async def migrate(dry_run: bool) -> None:
    root = settings.evidence_path
    if not root.exists():
        print(f"no evidence directory at {root}, nothing to migrate")
        return

    files = sorted(root.rglob("*.png"))
    if not files:
        print(f"{root} has no .png files, nothing to migrate")
        return

    migrated = failed = 0
    for path in files:
        key = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
        except OSError as e:
            print(f"  FAILED to read {key}: {e}")
            failed += 1
            continue

        print(f"{'[dry-run] would upload' if dry_run else 'uploading'} {key} ({len(data):,} bytes)")
        if not dry_run:
            try:
                await evidence_repository.save(key, data)
                migrated += 1
            except Exception as e:
                print(f"  FAILED to upload {key}: {type(e).__name__}: {e}")
                failed += 1
        else:
            migrated += 1

    print(f"\n{migrated} file(s) {'would be ' if dry_run else ''}migrated, {failed} failed")
    print("dry run complete -- nothing written." if dry_run else
          f"migration complete. Verify a few captures load, then delete {root} yourself.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args.dry_run))
    except KeyboardInterrupt:
        sys.exit(1)
