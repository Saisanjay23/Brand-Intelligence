"""Quick Analysis Service -- In-Memory (RAM-only) URL scraping & analysis.

Runs ad-hoc profile analysis for direct URLs provided by an analyst.
Nothing is saved to MongoDB (profiles, incidents, clients collections are untouched).
All jobs, scraped records, and screenshot buffers reside strictly in Python RAM
with auto-expiry (30 min TTL) and are completely cleared on server restart or page refresh.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import time
import traceback
from typing import Any, Optional
from urllib.parse import urlparse
import uuid

from backend.config.settings import settings
from backend.platforms import registry
from backend.platforms.scan_options import ScanOptions
from backend.services import incident_publisher
from backend.sessions import manager as sessions_engine
from backend.shared.completeness import field_report, missing_fields
from backend.shared.logging import get_logger
from backend.shared.models.row import Row
from backend.shared.models.scoring import (
    ACTIVE_WINDOW_DAYS,
    NAME_THRESHOLD,
    compute_incident_risk_score,
    compute_score,
    resolve_match,
)
from backend.shared.resilience import classify_failure, is_transient, retry_async

log = get_logger("services.quick_analysis")

# Host -> Platform ID mapping
_PLATFORM_HOSTS: dict[str, str] = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "fb.me": "facebook",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "instagram.com": "instagram",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "t.me": "telegram",
    "telegram.me": "telegram",
    "tiktok.com": "tiktok",
}

PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "facebook": "Facebook",
    "twitter": "Twitter / X",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "telegram": "Telegram",
    "tiktok": "TikTok",
}


def parse_direct_url(raw: str) -> Optional[tuple[str, str, str]]:
    """(platform, normalized_url, entity_id) for one direct URL or None."""
    raw = raw.strip()
    if not raw:
        return None
    url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        # Strip port if any
        if ":" in host:
            host = host.split(":")[0]
    except Exception:
        return None

    platform = _PLATFORM_HOSTS.get(host, "")
    if not platform:
        return None

    if platform == "facebook":
        try:
            from backend.platforms.facebook.discovery_engine import normalize_url, profile_id
            url = normalize_url(url)
            return platform, url, profile_id(url)
        except Exception:
            pass

    parts = [s for s in parsed.path.rstrip("/").split("/") if s]
    entity_id = parts[-1].lstrip("@") if parts else ""
    return platform, url, entity_id


def to_ddmmyyyy(iso: Optional[str]) -> str:
    """'2026-07-16...' -> '16-07-2026'."""
    if not iso:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else str(iso)


def _tri_yes_no(v: Optional[bool]) -> str:
    return "Yes" if v is True else "No" if v is False else ""


@dataclass
class QuickAnalysisItem:
    id: str
    raw_url: str
    url: str
    platform: str
    entity_id: str
    status: str = "pending"  # pending | running | done | error
    error: str = ""
    analysed_at: Optional[str] = None
    
    # Scraped details
    profile_name: str = ""
    followers: Optional[int] = None
    followers_exact: str = ""
    location: str = ""
    bio: str = ""
    last_post_date: str = ""
    is_active: Optional[bool] = None
    has_logo: Optional[bool] = None
    has_name_match: Optional[bool] = None
    name_score: int = 0
    risk_score: int = 2
    priority: str = "Low"
    profile_image_url: str = ""
    verified: Optional[bool] = None
    comments: str = ""
    has_screenshot: bool = False

    # Output dictionaries
    incident_row: dict[str, Any] = field(default_factory=dict)
    legacy_row: dict[str, Any] = field(default_factory=dict)


@dataclass
class QuickAnalysisJob:
    id: str
    created_at: float
    status: str = "queued"  # queued | running | done | cancelled | failed
    target_name: str = ""
    official_feed: str = ""
    total: int = 0
    completed: int = 0
    message: str = ""
    items: list[QuickAnalysisItem] = field(default_factory=list)
    platform_progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


class QuickAnalysisManager:
    """Manages in-memory ephemeral quick analysis jobs."""

    def __init__(self, ttl_seconds: int = 1800):
        self._jobs: dict[str, QuickAnalysisJob] = {}
        self._screenshots: dict[str, bytes] = {}  # key: f"{job_id}:{item_id}"
        self._ttl_seconds = ttl_seconds

    def _cleanup_expired(self) -> None:
        now = time.time()
        expired_ids = [
            jid for jid, j in self._jobs.items()
            if now - j.created_at > self._ttl_seconds
        ]
        for jid in expired_ids:
            self._jobs.pop(jid, None)
            # Remove associated screenshots
            keys_to_del = [k for k in self._screenshots if k.startswith(f"{jid}:")]
            for k in keys_to_del:
                self._screenshots.pop(k, None)

    def start_job(
        self,
        urls: list[str],
        target_name: str = "",
        official_feed: str = "",
    ) -> tuple[str, list[dict[str, str]]]:
        """Creates and launches a new in-memory Quick Analysis job."""
        self._cleanup_expired()
        job_id = str(uuid.uuid4())
        valid_items: list[QuickAnalysisItem] = []
        skipped: list[dict[str, str]] = []

        seen_urls: set[str] = set()
        for raw in urls:
            raw_str = (raw or "").strip()
            if not raw_str:
                continue
            parsed = parse_direct_url(raw_str)
            if not parsed:
                skipped.append({"url": raw_str, "reason": "Unsupported domain or invalid URL format"})
                continue
            platform, norm_url, entity_id = parsed
            if norm_url in seen_urls:
                skipped.append({"url": raw_str, "reason": "Duplicate URL in batch"})
                continue
            seen_urls.add(norm_url)

            item = QuickAnalysisItem(
                id=str(uuid.uuid4()),
                raw_url=raw_str,
                url=norm_url,
                platform=platform,
                entity_id=entity_id,
            )
            valid_items.append(item)

        if not valid_items:
            return "", skipped

        # Initialize platform progress tracking
        platform_progress: dict[str, dict[str, Any]] = {}
        for item in valid_items:
            if item.platform not in platform_progress:
                platform_progress[item.platform] = {
                    "status": "pending",
                    "total": 0,
                    "completed": 0,
                    "displayName": PLATFORM_DISPLAY_NAMES.get(item.platform, item.platform.title()),
                }
            platform_progress[item.platform]["total"] += 1

        job = QuickAnalysisJob(
            id=job_id,
            created_at=time.time(),
            status="queued",
            target_name=target_name.strip(),
            official_feed=official_feed.strip(),
            total=len(valid_items),
            completed=0,
            items=valid_items,
            platform_progress=platform_progress,
        )
        self._jobs[job_id] = job

        # Launch background task
        asyncio.create_task(self._run_job(job))
        return job_id, skipped

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        self._cleanup_expired()
        job = self._jobs.get(job_id)
        if not job:
            return None

        return {
            "id": job.id,
            "status": job.status,
            "target_name": job.target_name,
            "official_feed": job.official_feed,
            "total": job.total,
            "completed": job.completed,
            "message": job.message,
            "platform_progress": job.platform_progress,
            "items": [
                {
                    "id": item.id,
                    "url": item.url,
                    "platform": item.platform,
                    "platform_name": PLATFORM_DISPLAY_NAMES.get(item.platform, item.platform.title()),
                    "entity_id": item.entity_id,
                    "status": item.status,
                    "error": item.error,
                    "analysed_at": item.analysed_at,
                    "profile_name": item.profile_name,
                    "followers": item.followers,
                    "location": item.location,
                    "bio": item.bio,
                    "last_post_date": item.last_post_date,
                    "is_active": item.is_active,
                    "has_logo": item.has_logo,
                    "has_name_match": item.has_name_match,
                    "name_score": item.name_score,
                    "risk_score": item.risk_score,
                    "priority": item.priority,
                    "profile_image_url": item.profile_image_url,
                    "verified": item.verified,
                    "comments": item.comments,
                    "has_screenshot": item.has_screenshot,
                    "incident_row": item.incident_row,
                    "legacy_row": item.legacy_row,
                }
                for item in job.items
            ],
        }

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status in ("done", "cancelled", "failed"):
            return False
        job.status = "cancelled"
        job._cancel_event.set()
        job.message = "Analysis cancelled by user"
        return True

    def get_screenshot(self, job_id: str, item_id: str) -> Optional[bytes]:
        return self._screenshots.get(f"{job_id}:{item_id}")

    # ─────────────────────────── Execution Loop ─────────────────────────── #

    async def _run_job(self, job: QuickAnalysisJob) -> None:
        job.status = "running"
        job.message = "Running quick analysis across platforms..."
        log.info(f"[QuickAnalysis:{job.id}] Started with {job.total} URLs")

        # Group items by platform
        by_platform: dict[str, list[QuickAnalysisItem]] = {}
        for item in job.items:
            by_platform.setdefault(item.platform, []).append(item)

        async def _run_platform_items(platform_id: str, items: list[QuickAnalysisItem]) -> None:
            if job._cancel_event.is_set():
                return
            prog = job.platform_progress[platform_id]
            prog["status"] = "running"
            try:
                await self._process_platform_batch(job, platform_id, items)
                if prog["status"] == "running":
                    prog["status"] = "done"
            except Exception as e:
                log.error(f"[QuickAnalysis:{job.id}] Platform {platform_id} failed: {e}\n{traceback.format_exc()}")
                prog["status"] = "failed"
                for it in items:
                    if it.status in ("pending", "running"):
                        it.status = "error"
                        it.error = f"Platform analysis error: {str(e)}"
                        job.completed += 1
                        prog["completed"] += 1

        await asyncio.gather(*(_run_platform_items(pid, items) for pid, items in by_platform.items()))

        if not job._cancel_event.is_set():
            job.status = "done"
            job.message = f"Completed analysis for {job.completed}/{job.total} profiles"
        log.info(f"[QuickAnalysis:{job.id}] Finished: status={job.status}")

    async def _process_platform_batch(
        self, job: QuickAnalysisJob, platform_id: str, items: list[QuickAnalysisItem]
    ) -> None:
        from backend.platforms import registry as _registry

        anon_plat = _registry.get(platform_id)
        run_anonymously = (
            anon_plat.can_run_anonymously
            and await _registry.session_state(anon_plat) != "ready"
        )

        session_item: dict[str, Any] = {}
        if run_anonymously:
            plat = anon_plat
        else:
            try:
                plat, session_item = await sessions_engine.session_for_job(platform_id)
            except Exception as e:
                log.warning(f"[QuickAnalysis:{job.id}:{platform_id}] No session available: {e}")
                # Fall back to anonymous if supported, otherwise fail items
                if anon_plat.can_run_anonymously:
                    plat = anon_plat
                    run_anonymously = True
                else:
                    for it in items:
                        it.status = "error"
                        it.error = f"No healthy session available for {platform_id}"
                        job.completed += 1
                        job.platform_progress[platform_id]["completed"] += 1
                    return

        options = ScanOptions(
            evidence=None,  # Do not store in DB
            ephemeral_screenshot=True,  # Return bytes in row.screenshot_bytes
            delay=settings.analysis_delay_sec,
            concurrency=min(settings.analysis_concurrency, 3),
            headful=not settings.headless,
        )

        scraper = plat.scraper()(
            options,
            session_item.get("cookies", []),
            session_id=session_item.get("id", ""),
            proxy=session_item.get("proxy"),
            **({"anonymous": True} if run_anonymously else {}),
        )

        await scraper.start()
        try:
            if not run_anonymously:
                if not await scraper.check_session():
                    log.warning(f"[QuickAnalysis:{job.id}:{platform_id}] Session check failed")
                    if anon_plat.can_run_anonymously:
                        # Continue anonymously
                        pass
                    else:
                        for it in items:
                            it.status = "error"
                            it.error = "Platform credentials rejected or session expired"
                            job.completed += 1
                            job.platform_progress[platform_id]["completed"] += 1
                        return

            wave_size = 1 if platform_id == "telegram" else max(1, min(options.concurrency, 3))
            stagger = 1.5

            for i in range(0, len(items), wave_size):
                if job._cancel_event.is_set():
                    break
                batch = items[i : i + wave_size]
                
                async def _scrape_single(idx: int, it: QuickAnalysisItem) -> None:
                    if idx > 0:
                        await asyncio.sleep(idx * stagger)
                    if job._cancel_event.is_set():
                        return
                    it.status = "running"
                    effective_target = job.target_name or it.entity_id
                    try:
                        row = await retry_async(
                            lambda: scraper.one(it.url, effective_target, job.official_feed),
                            attempts=2,
                            base_delay=2.0,
                            max_delay=5.0,
                            retryable=is_transient,
                        )
                        self._populate_item(job, it, row)
                    except Exception as e:
                        log.warning(f"[QuickAnalysis:{job.id}] Scrape error on {it.url}: {e}")
                        it.status = "error"
                        it.error = str(e)
                        it.analysed_at = datetime.now(timezone.utc).isoformat()
                        self._build_fallback_formats(job, it)
                    finally:
                        job.completed += 1
                        job.platform_progress[platform_id]["completed"] += 1

                await asyncio.gather(*(_scrape_single(idx, it) for idx, it in enumerate(batch)))

        finally:
            try:
                await scraper.close()
            except Exception:
                pass

    def _populate_item(self, job: QuickAnalysisJob, it: QuickAnalysisItem, row: Row) -> None:
        it.status = "done"
        it.analysed_at = datetime.now(timezone.utc).isoformat()
        it.profile_name = row.profile_name or it.entity_id
        it.followers = row.followers
        it.followers_exact = row.followers_exact
        it.location = row.location
        it.bio = row.bio
        it.last_post_date = row.last_post_iso
        it.is_active = (row.active_yes == "Yes") if row.active_yes else False
        it.has_logo = (row.logo_yes == "Yes") if row.logo_yes else (False if row.logo_yes == "No" else None)
        it.has_name_match = (row.name_yes == "Yes") if row.name_yes else (False if row.name_yes == "No" else None)
        it.name_score = row.name_score
        it.profile_image_url = row.profile_pic_url
        it.verified = row.verified
        it.comments = row.notes

        # Compute internal risk score & priority
        it.risk_score = row.risk
        it.priority = row.priority

        # Store in-memory screenshot if captured
        if getattr(row, "screenshot_bytes", None):
            self._screenshots[f"{job.id}:{it.id}"] = row.screenshot_bytes
            it.has_screenshot = True

        # Build Dual Format records
        self._build_both_formats(job, it, row)

    def _build_both_formats(self, job: QuickAnalysisJob, it: QuickAnalysisItem, row: Row) -> None:
        """Constructs both Incident (Takedown) and Legacy (Raw) export rows in memory."""
        platform_name = PLATFORM_DISPLAY_NAMES.get(it.platform, it.platform.title())
        target_display = job.target_name or it.entity_id or "Suspect Account"
        asset_name = job.target_name or it.entity_id

        # 1. Incident / Platform Format (Takedown Report Format)
        # Matches frontend incidentExport.ts columns exactly
        inc_risk = compute_incident_risk_score(
            has_logo=True,
            has_name_match=True,
            followers=it.followers,
            location=it.location,
            last_post_iso=it.last_post_date,
            is_active=bool(it.is_active),
        )

        it.incident_row = {
            "OrgId": "QUICK-ANALYSIS",
            "Domain": it.platform,
            "AssetType": platform_name,
            "AssetName": asset_name,
            "Source": it.url,
            "RiskScore": inc_risk,
            "ThirdParty YES/NO": "NO",
            "Date (DD-MM-YYYY) (Optional)": "",
            "Title": f"Similar {platform_name} Account {it.profile_name} Found",
            "Description": f"Name: {it.profile_name} Url: {it.url}",
            "Active (Yes/No)": "Yes" if it.is_active else "No",
            "Name (Yes/No)": "Yes",
            "Logo (Yes/No)": "Yes",
            "Location": it.location or "",
            "Number of Followers": it.followers if it.followers is not None else "",
            "Last Post (DD-MM-YYYY) (Optional)": to_ddmmyyyy(it.last_post_date),
        }

        # 2. Legacy / Raw Analysis Format
        # Matches frontend legacyExport.ts columns exactly
        it.legacy_row = {
            "Original Name": "",
            "Original feed": "",
            "IMPERSONATED": it.url,
            "Profile name": it.profile_name,
            "Created Date": "",
            "Logo (Yes / No)": "Yes",
            "Followers": it.followers if it.followers is not None else "",
            "Active (Yes / No)": "Yes" if it.is_active else "No",
            "Name (Yes / No)": "Yes",
            "Location": it.location or "",
            "Last Post (DD-MM-YYYY) (Optional)": to_ddmmyyyy(it.last_post_date),
            "Risk Score": compute_score(
                has_logo=True,
                has_name_match=True,
                has_location=bool(it.location),
                last_post_iso=it.last_post_date
            ),
            "priority": "High",  # Logo=Yes forces High priority
            "Date": to_ddmmyyyy(it.analysed_at),
            "Comments": it.comments or "",
        }

    def _build_fallback_formats(self, job: QuickAnalysisJob, it: QuickAnalysisItem) -> None:
        """Fallback rows for failed/errored URLs."""
        platform_name = PLATFORM_DISPLAY_NAMES.get(it.platform, it.platform.title())
        it.incident_row = {
            "OrgId": "QUICK-ANALYSIS",
            "Domain": it.platform,
            "AssetType": platform_name,
            "AssetName": job.target_name or it.entity_id,
            "Source": it.url,
            "RiskScore": 2,
            "ThirdParty YES/NO": "NO",
            "Date (DD-MM-YYYY) (Optional)": "",
            "Title": f"{platform_name} Account (Error)",
            "Description": f"Url: {it.url} Error: {it.error}",
            "Active (Yes/No)": "No",
            "Name (Yes/No)": "Yes",
            "Logo (Yes/No)": "Yes",
            "Location": "",
            "Number of Followers": "",
            "Last Post (DD-MM-YYYY) (Optional)": "",
        }
        it.legacy_row = {
            "Original Name": "",
            "Original feed": "",
            "IMPERSONATED": it.url,
            "Profile name": it.entity_id or "",
            "Created Date": "",
            "Logo (Yes / No)": "Yes",
            "Followers": "",
            "Active (Yes / No)": "No",
            "Name (Yes / No)": "Yes",
            "Location": "",
            "Last Post (DD-MM-YYYY) (Optional)": "",
            "Risk Score": 2,
            "priority": "Low",
            "Date": to_ddmmyyyy(it.analysed_at),
            "Comments": f"Error: {it.error}",
        }


# Global in-memory singleton
quick_analysis_manager = QuickAnalysisManager()
