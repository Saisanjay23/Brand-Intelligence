"""Where a run's results go.

The engine's own contract is "return the data", these are conveniences
for callers who want it on disk in a particular shape, and one optional
adapter for callers who DO have Mongo and want the standalone run to land
in the same collections the API and frontend already read.

That last one is the whole reason this module imports nothing at import
time: `to_mongo` pulls in `profile_repository` (and therefore Motor)
INSIDE the function. A machine with no database can import this module,
call every other function in it, and never touch Motor, while a machine
that has one gets full interoperability from the same code path. That is
what "works whether or not the DB is there" means in practice, and it is
why the import is where it is rather than at the top of the file.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from backend.engine.models import EngineResult
from backend.shared.logging import get_logger

log = get_logger("engine.sinks")

# Column order for CSV/XLSX-bound output. Not every key every run produces
#, just the ones a human reads first, in the order they read them; any
# remaining keys are appended alphabetically so nothing is ever silently
# dropped from a spreadsheet.
PREFERRED_COLUMNS = (
    "platform", "display_name", "username", "url", "entity_type", "keyword",
    "priority", "risk_score", "name_score", "has_logo", "verified", "is_active",
    "followers", "friends", "location", "last_post_date", "analysis_status",
    "entity_id", "profile_image_url", "screenshot", "comments",
)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(result: EngineResult, path: str | Path) -> Path:
    """The whole run, profiles, per-platform outcomes, timings, errors.

    The default, because the per-platform outcomes are not decoration: a
    file of 40 profiles means something different when one platform was
    skipped for a dead cookie, and only this shape carries that.
    """
    out = Path(path)
    _ensure_parent(out)
    out.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log.info(f"wrote {result.found} profile(s) -> {out}")
    return out


def write_jsonl(result: EngineResult, path: str | Path) -> Path:
    """One profile per line, nothing else, for piping into something that
    reads a record at a time. Run-level context is lost by design; use
    `write_json` when it matters."""
    out = Path(path)
    _ensure_parent(out)
    with out.open("w", encoding="utf-8") as fh:
        for profile in result.profiles:
            fh.write(json.dumps(profile, ensure_ascii=False, default=str) + "\n")
    log.info(f"wrote {result.found} profile(s) -> {out}")
    return out


def _columns(profiles: list[dict]) -> list[str]:
    present = {k for p in profiles for k in p}
    ordered = [c for c in PREFERRED_COLUMNS if c in present]
    return ordered + sorted(present - set(ordered))


def write_csv(result: EngineResult, path: str | Path) -> Path:
    """Flat table, for a spreadsheet or a quick eyeball.

    `newline=""` is required, not stylistic: without it the csv module's
    own line terminator lands on top of Windows' translation and every row
    is separated by a blank one.
    """
    out = Path(path)
    _ensure_parent(out)
    columns = _columns(result.profiles)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for profile in result.profiles:
            writer.writerow({
                # dicts (Row.src) and lists have no useful CSV rendering;
                # JSON-encoding them keeps the cell readable and reversible
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                for k, v in profile.items()
            })
    log.info(f"wrote {len(result.profiles)} row(s) -> {out}")
    return out


WRITERS = {"json": write_json, "jsonl": write_jsonl, "csv": write_csv}


def write(result: EngineResult, path: str | Path, fmt: str = "") -> Path:
    """Write in `fmt`, or in whatever the filename's extension implies."""
    out = Path(path)
    chosen = (fmt or out.suffix.lstrip(".") or "json").lower()
    writer = WRITERS.get(chosen)
    if writer is None:
        raise ValueError(f"unknown output format {chosen!r} -- known: {', '.join(WRITERS)}")
    return writer(result, out)


async def _reachable() -> bool:
    """Is Mongo usable from the event loop running RIGHT NOW?

    `database/connection.py` caches one Motor client in a module global,
    and a Motor client is bound to the loop it was constructed on. A
    library caller doing two separate `asyncio.run(...)` calls, which is
    exactly what `runner.run()` encourages, therefore inherits a client
    tied to the first, already-closed loop, and every operation on it fails
    as though the database were down.

    A failed ping is retried once against a freshly-built client (`close()`
    drops the global, the next call rebuilds it on the current loop) so
    that misdiagnosis can't cost a caller results that were scraped
    successfully. A second failure means the database really is gone.
    """
    from backend.database.connection import close, ping

    if await ping():
        return True
    log.info("mongo ping failed; rebuilding the client on this event loop and retrying once")
    await close()
    return await ping()


async def to_mongo(result: EngineResult, client_id: str) -> dict[str, Any]:
    """Persist a standalone run into the same `profiles` collection the API
    and frontend read, when a database happens to be available.

    Opt-in only. The engine never calls this on its own: a run that quietly
    wrote to a database the caller did not ask about would break the one
    guarantee this package makes.

    Records are keyed by `client_id`, so a standalone run and the API path
    can share a client and reconcile, or use different ids and stay apart.
    Deduplication, phase advancement and the analyst's `status` are all
    `profile_repository.save`'s existing behaviour, unchanged, which is
    exactly why this defers to it rather than writing documents itself.
    """
    from backend.database.repositories import profile_repository as profiles_db

    if not await _reachable():
        raise RuntimeError("mongo unreachable -- results were NOT written (they are still in the result object)")

    phase = profiles_db.PHASE_ANALYSIS if result.kind == "analysis" else profiles_db.PHASE_DISCOVERY
    by_platform: dict[str, list[dict]] = {}
    for profile in result.profiles:
        by_platform.setdefault(profile.get("platform", ""), []).append(
            {k: v for k, v in profile.items() if k != "platform"}
        )

    await profiles_db.ensure_indexes()
    saved = new = 0
    for platform_id, items in by_platform.items():
        if not platform_id:
            log.warning(f"skipping {len(items)} record(s) with no platform")
            continue
        s, n = await profiles_db.save_many(client_id, platform_id, phase, items)
        saved += s
        new += n
    log.info(f"mongo: {saved} saved, {new} new (client {client_id})")
    return {"saved": saved, "new": new, "client_id": client_id, "phase": phase}
