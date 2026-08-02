"""Discovery use case: a client's keywords -> candidate profiles -> Mongo,
swept across EVERY platform that has a ready session -- the caller never
names a platform; this figures out which platforms are usable and visits
all of them in one job.

Saves results incrementally per completed sweep rather than batching
everything at the end, so a caller polling `GET /jobs/{id}` sees new
profiles within seconds of scraping, not only once the whole thing ends.
One platform's session dying mid-run does not abort the others -- each
platform's sweep is caught and noted independently.
"""

from __future__ import annotations

import asyncio
import inspect

from backend.database.repositories import profile_repository as profiles_db
from backend.sessions import manager as sessions_engine
from backend.services.job_service import Job
from backend.platforms.scan_options import DiscoveryOptions
from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.discovery")


def _hit_to_fields(hit, platform: str) -> dict:
    """A discovery Hit -> the plain field dict `profile_repository.save` expects."""
    username = ""
    url = hit.url or ""
    if platform == "facebook":
        from backend.platforms.facebook.discovery_engine import profile_id
        username = profile_id(url)
    else:
        parts = [s for s in url.rstrip("/").split("/") if s]
        if parts:
            candidate = parts[-1].lstrip("@")
            if "?" not in candidate and "#" not in candidate:
                username = candidate
    return {
        "url": url, "entity_id": hit.entity_id, "keyword": hit.keyword,
        "username": username, "display_name": hit.name, "entity_type": hit.entity_type,
        "discovery_source": hit.source, "profile_image_url": getattr(hit, "avatar", ""),
        "has_logo": bool(getattr(hit, "has_custom_pic", False)),
        "verified": bool(getattr(hit, "verified", False)),
    }


async def _ready_platforms() -> list[str]:
    from backend.platforms import registry

    out = []
    for platform_id, plat in registry.PLATFORMS.items():
        if not plat.enabled or not plat.can_discover:
            continue
        if await registry.session_state(plat) == "ready":
            out.append(platform_id)
    return out


async def run_discovery(job: Job) -> None:
    from backend.services.job_service import JobManager
    from backend.platforms import registry
    from backend.database.repositories import client_repository as clients_db

    mgr = JobManager()
    p = job.params
    keywords = [k.strip() for k in p["keywords"] if k.strip()]
    tabs = p.get("tabs", ["people", "pages"])

    ready = await _ready_platforms()
    if not ready:
        raise RuntimeError("no platform has a ready session to sweep")

    # per-platform result caps saved on the client (see dto/client_dto.py);
    # a platform missing here is uncapped -- "scrape all" for that platform.
    client = await clients_db.try_get(job.client_id)
    platform_limits = (client or {}).get("platform_limits") or {}

    await mgr.emit(job, "progress", f"sweeping {len(ready)} platform(s) for {len(keywords)} keyword(s)", total=len(ready))

    sweep_units = max(1, len(keywords) * len(tabs))
    for platform_id in ready:
        await mgr.emit(job, "progress", platform=platform_id, platform_status="pending", platform_total=sweep_units)

    total_saved = total_new = 0
    notes: list[str] = []

    async def _run_one(platform_id: str) -> tuple[str, int, int, str]:
        plat = registry.get(platform_id)
        try:
            platform_params = {**p, "max_results": platform_limits.get(platform_id, p.get("max_results", 0))}
            saved, new, note = await _sweep_platform(job, mgr, plat, keywords, tabs, platform_params)
            await mgr.emit(job, "progress", platform=platform_id, platform_status="done", platform_processed=sweep_units)
            return platform_id, saved, new, note
        except Exception as e:
            log.error(f"job {job.id}: {platform_id} sweep failed: {type(e).__name__}: {e}")
            await mgr.emit(job, "progress", platform=platform_id, platform_status="failed")
            return platform_id, 0, 0, f"FAILED ({type(e).__name__}: {e})"

    results = await asyncio.gather(*(_run_one(pid) for pid in ready))
    for platform_id, saved, new, note in results:
        total_saved += saved
        total_new += new
        if note:
            notes.append(f"{platform_id}: {note}")
        await mgr.emit(job, "progress", f"{platform_id} done", found=total_saved)

    job.message = f"{total_saved} stored, {total_new} new" + (f" -- {'; '.join(notes)}" if notes else "")


async def _sweep_platform(job: Job, mgr, plat, keywords: list[str], tabs: list[str], params: dict) -> tuple[int, int, str]:
    options = DiscoveryOptions(
        concurrency=params.get("concurrency", settings.discovery_concurrency),
        max_results=params.get("max_results", 0),
        max_seconds=params.get("max_seconds", settings.discovery_max_seconds),
        headful=not settings.headless,
    )

    saved = new = 0
    completed_units = 0
    sweep_units = max(1, len(keywords) * len(tabs))
    already_saved: set[str] = set()
    all_sweeps: list = []

    await mgr.emit(job, "progress", platform=plat.id, platform_status="running", platform_total=sweep_units)

    async def _save_hits(hits: list, label: str) -> None:
        nonlocal saved, new
        fresh = [h for h in hits if h.entity_id not in already_saved]
        if not fresh:
            return
        s, n = await profiles_db.save_many(job.client_id, plat.id, "discovery", [_hit_to_fields(h, plat.id) for h in fresh])
        already_saved.update(h.entity_id for h in fresh)
        saved += s
        new += n
        job.new_profiles += n
        await mgr.emit(job, "item", f"[{plat.id}] {label}: {len(fresh)} profile(s) ({saved} total, {new} new)", found=saved)

    async def _on_page_hits(keyword: str, tab: str, found_count: int, page_num: int, new_hits: list) -> None:
        await _save_hits(new_hits, f"{tab} {keyword!r} (page {page_num})")

    async def _on_sweep_done(sweep) -> None:
        nonlocal completed_units
        all_sweeps.append(sweep)
        hits = sweep.hits or []
        if hits:
            await _save_hits(hits, f"{sweep.keyword!r} done")
        completed_units += 1
        await mgr.emit(job, "progress", platform=plat.id, platform_status="running", platform_processed=completed_units)

    plat_obj, session_item = await sessions_engine.session_for_job(plat.id)
    if not plat_obj.session_path:
        discoverer = plat_obj.discoverer()(options, None)
        await _run_incremental(discoverer, keywords, tabs, _on_sweep_done, _on_page_hits)
    else:
        session = plat_obj.session_cls()(
            options, session_item.get("cookies", []),
            session_id=session_item.get("id", ""), proxy=session_item.get("proxy"),
        )
        await session.start()
        try:
            if not await session.check_session():
                await sessions_engine.mark_session_failed(plat.id, session_item.get("id", ""), "expired")
                raise RuntimeError(f"session {session_item.get('identifier')} invalid or checkpointed")
            discoverer = plat_obj.discoverer()(options, session.ctx)
            await _run_incremental(discoverer, keywords, tabs, _on_sweep_done, _on_page_hits)
        finally:
            await session.stop()

    incomplete = [s for s in all_sweeps if not s.complete]
    note = ""
    if incomplete:
        note = f"{len(incomplete)} sweep(s) INCOMPLETE: " + ", ".join(f"{s.keyword!r}/{s.tab} ({s.stopped})" for s in incomplete)
    return saved, new, note


async def _run_incremental(discoverer, keywords: list[str], tabs: list[str], on_sweep_done, on_page_hits=None) -> list:
    """Run sweeps at the discoverer's own concurrency, calling
    on_sweep_done for each completed sweep immediately instead of waiting
    for gather, and on_page_hits for every batch of new results found
    mid-sweep."""
    jobs = [(k, t) for k in keywords for t in tabs]
    concurrency = getattr(discoverer, "a", None)
    max_conc = getattr(concurrency, "concurrency", 2) if concurrency else 2
    sem = asyncio.Semaphore(max(1, max_conc))

    async def one(i: int, keyword: str, tab: str):
        async with sem:
            await asyncio.sleep(i % max(1, max_conc) * 1.0)

            async def _progress(found_count: int, page_num: int, new_hits: list) -> None:
                if not on_page_hits:
                    return
                try:
                    await on_page_hits(keyword, tab, found_count, page_num, new_hits)
                except Exception:
                    pass

            sig = inspect.signature(discoverer.sweep)
            if "on_progress" in sig.parameters:
                sweep = await discoverer.sweep(keyword, tab, on_progress=_progress)
            else:
                sweep = await discoverer.sweep(keyword, tab)
            await on_sweep_done(sweep)
            return sweep

    return list(await asyncio.gather(*(one(i, k, t) for i, (k, t) in enumerate(jobs))))
