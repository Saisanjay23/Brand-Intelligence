"""When something breaks, say why in plain English and how to fix it.

`health` answers "is this platform degraded" as one number. This answers
"what exactly broke, and what does an operator do about it", one row per
failure, diagnosed against known failure shapes (a stale session, a
checkpoint, a quota, a flood-wait, ...) rather than left as a bare
traceback for someone to interpret from scratch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.database.repositories import incident_repository as incidents_db


@dataclass
class Diagnosis:
    cause: str
    fix: str


_RULES: list[tuple[re.Pattern, Diagnosis]] = [
    (
        re.compile(r"account suspended|account disabled|verify your phone|action blocked", re.I),
        Diagnosis(
            "The platform has actively suspended, disabled, or locked this account.",
            "This session is burned. Remove it from the Sessions panel and authenticate a completely different account before retrying.",
        ),
    ),
    (
        re.compile(r"session invalid or checkpointed", re.I),
        Diagnosis(
            "The saved session cookies were rejected, or the platform challenged the account (a security checkpoint) mid-run.",
            "Re-export fresh cookies from a logged-in browser for this platform and re-upload them under Sessions, then retry.",
        ),
    ),
    (
        re.compile(r"credentials (invalid|rejected)|session (incomplete|missing|not ready)|credentials", re.I),
        Diagnosis(
            "This platform's session or API credentials are not configured, invalid, or are missing a required cookie.",
            "Add or re-export the required session/API key under Sessions before running this platform again.",
        ),
    ),
    (
        re.compile(r"no urls to analyse", re.I),
        Diagnosis(
            "Analysis was asked to run but no profile matched the requested status filter for this client.",
            "Approve at least one discovered profile first (Analysis only ever runs on validated profiles), then re-run.",
        ),
    ),
    (
        re.compile(r"quotaexceeded|quota exceeded|quota exhaust", re.I),
        Diagnosis(
            "YouTube's daily API quota (10,000 units) has been used up.",
            "Wait for the quota to reset (midnight Pacific time) or switch to a different API key, then retry.",
        ),
    ),
    (
        re.compile(r"floodwait|flood.?wait|asked (us|for) .*wait", re.I),
        Diagnosis(
            "Telegram's own rate limit was hit -- it explicitly asked the client to slow down.",
            "Wait out the cooldown period Telegram specified before running Telegram jobs again; running sooner risks a longer penalty.",
        ),
    ),
    (
        re.compile(r"navigation failed|timeout", re.I),
        Diagnosis(
            "The page failed to load in time -- a slow connection, a dead profile URL, or the platform throttling this session.",
            "Retry the same profile on its own; if it keeps happening for many profiles, the session may be rate-limited -- pace runs further apart.",
        ),
    ),
]

_GENERIC = Diagnosis(
    "An unexpected error occurred that doesn't match a known failure pattern -- most often a platform changing its page/response layout.",
    "Check the platform's session is valid and retry. If it keeps happening, the scraper's field-reading code likely needs updating for a layout change on this platform.",
)


def diagnose(error_type: str, message: str) -> Diagnosis:
    text = f"{error_type}: {message}"
    for pattern, diagnosis in _RULES:
        if pattern.search(text):
            return diagnosis
    return _GENERIC


def _source_file(platform: str, kind: str) -> str:
    """The exact scraper module a "what broke" email should point an
    operator at, e.g. `backend.platforms.facebook.discovery_engine:Discovery`
   ; so a fix means opening one named file, not guessing across five
    platforms' worth of engine code. `platform="all"` (the breaker
    incidents; no single platform down, or the whole pool exhausted) has
    no one file to name."""
    if platform == "all":
        return ""
    from backend.platforms import registry

    plat = registry.PLATFORMS.get(platform)
    if not plat:
        return ""
    # "session-check" (the periodic live-probe) exercises the same
    # check_session() the analysis engine uses, see
    # sessions/manager.py::verify_session_item.
    return plat.discovery_path if kind == "discovery" else plat.analysis_path


async def record(
    platform: str, kind: str, scope: str, job_id: str,
    error_type: str, message: str, url: str = "",
    where: str = "",
) -> None:
    """`where` is the precise blame trail from an extraction strategy chain
    (see shared/extraction.py::ExtractionResult.report), the file, line
    and source text of each method that failed. `source_file` alone names
    the module; this names the line inside it to change, which is the
    difference between "Facebook discovery broke" and an actionable ticket."""
    diagnosis = diagnose(error_type, message)
    doc = {
        "platform": platform, "kind": kind, "scope": scope, "job_id": job_id,
        "error_type": error_type, "message": message,
        "cause": diagnosis.cause, "fix": diagnosis.fix, "url": url,
        "source_file": _source_file(platform, kind),
        "where": where,
        "ts": datetime.now(timezone.utc),
    }
    await incidents_db.record(doc)
    try:
        import asyncio

        from backend.services.alerting_service import notify_incident
        asyncio.create_task(notify_incident(doc))
    except Exception:
        pass


async def ensure_indexes() -> None:
    await incidents_db.ensure_indexes()
