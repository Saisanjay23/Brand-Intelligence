"""Which incidents are worth an email, and how often.

The old routing had one rule: at most one email per PLATFORM per hour, for
anything that wasn't a timeout or an empty queue. That is wrong in both
directions at once, which is why this module exists.

Too loud: `ExtractionDegraded` means "the primary parser stopped working
but the fallback caught it, results are still flowing". It is worth
knowing, it is not worth an interrupt -- and it fires in bursts. One live
morning (2026-08-13, Twitter concurrency throttling) produced 57 of them.
Under the old rule that is an inbox full of a problem nobody needs to act
on within the hour, which is how people learn to filter these to trash --
taking the genuinely urgent ones with it.

Too quiet: the hourly window was keyed on the platform alone, so a
`SessionInvalid` on Facebook silently swallowed a `ParserDrift` on
Facebook for the next 59 minutes. Two unrelated breaks, one of them
never reported. Suppressing a DIFFERENT problem is not de-duplication,
it is data loss.

So the rule here is: de-duplicate on WHAT BROKE (platform + error type +
the exact code location), not on the platform; and only interrupt for
severities that mean someone must act. Everything else is still recorded
and still reaches the daily digest -- suppressed, never discarded, and
counted so the next mail can say "this also happened 57 more times".

    CRITICAL -> email immediately. The pipeline is not doing its job:
                a session is dead, a parser stopped recognising the page,
                a field stopped extracting, a quota is gone, config is
                broken. Someone has to act.
    WARNING  -> digest only. Still producing correct output, on a weaker
                path that will age out. Act this week, not this minute.
    INFO     -> never emailed. Transient/expected (one slow page, an
                empty queue). Recorded for the dashboard only.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Optional

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

# Per-fingerprint quiet period. A given break re-alerts at most this often;
# a DIFFERENT break is never blocked by it.
CRITICAL_REALERT_SECONDS = 6 * 3600

# Flood ceiling across everything. If a whole-estate outage produces
# critical incidents on six platforms at once, six emails is a useful
# page; sixty is a denial of service on the operator. Past this many in a
# rolling hour, the rest are suppressed into the digest with a count.
MAX_EMAILS_PER_HOUR = 8


# Error types that mean "the tool is not doing its job right now".
# Matched exactly against `incident["error_type"]`, which is set at every
# `incident_service.record()` call site, so this stays a closed vocabulary
# rather than a guess against free-text message wording.
_CRITICAL_TYPES = frozenset({
    # a saved session is dead/challenged -- explicitly what the operator
    # asked to always be told about
    "SessionInvalid",
    "SessionExpired",
    # scraping logic stopped recognising the platform
    "ParserDrift",
    "FieldExtractionDrift",
    "LastPostExtractionDrift",
    # the platform itself is refusing us
    "PlatformBlocked",
    "QuotaExceeded",
    "CredentialsInvalid",
    # the deployment itself is misconfigured in a way that silently
    # changes results (see services/preflight_service.py)
    "ConfigDrift",
})

# Still working, on a weaker path. Digest, never an interrupt.
_WARNING_TYPES = frozenset({
    "ExtractionDegraded",
})

# Free-text causes that are transient or expected and must never page.
# These are matched against the diagnosed `cause`, because they arrive
# under a variety of error types (a timeout can surface as almost
# anything) and the diagnosis is what normalises them.
_INFO_CAUSE_PATTERNS = (
    re.compile(r"no profile matched", re.I),
    re.compile(r"the page failed to load in time", re.I),
)


def severity_of(incident: dict) -> str:
    """CRITICAL | WARNING | INFO for one incident.

    Error type wins over cause text: the type is set deliberately at the
    call site, the cause is pattern-matched from a message and is the
    weaker signal. A `SessionInvalid` whose message happens to mention a
    timeout is still a dead session.
    """
    error_type = (incident.get("error_type") or "").strip()
    if error_type in _CRITICAL_TYPES:
        return CRITICAL
    if error_type in _WARNING_TYPES:
        return WARNING
    cause = incident.get("cause") or ""
    message = incident.get("message") or ""
    for pattern in _INFO_CAUSE_PATTERNS:
        if pattern.search(cause) or pattern.search(message):
            return INFO
    # An unrecognised error type is, by definition, something nobody has
    # triaged yet. Treat it as critical: a new failure mode that nobody
    # gets told about is exactly the silent-drift class this whole
    # subsystem exists to prevent. The fingerprint de-dup below keeps
    # that from becoming a flood.
    return CRITICAL


def fingerprint(incident: dict) -> str:
    """A stable id for *this specific break*, so repeats de-duplicate and
    distinct problems never suppress each other.

    Built from platform + phase + error type + the first line of the blame
    trail (the file:line that actually broke). Deliberately NOT the message,
    which embeds row counts, example URLs and job ids that differ on every
    occurrence of the same underlying break and would defeat de-duplication
    entirely.
    """
    where_head = (incident.get("where") or "").strip().splitlines()
    location = where_head[0].strip() if where_head else (incident.get("source_file") or "")
    raw = "|".join((
        incident.get("platform") or "",
        incident.get("kind") or "",
        incident.get("error_type") or "",
        location,
    ))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class _Tracked:
    last_sent: float = 0.0
    suppressed: int = 0
    first_suppressed_at: float = 0.0


@dataclass
class Decision:
    """Whether to send, and what the mail should say about repeats."""

    send: bool
    severity: str
    fingerprint: str
    suppressed_since_last: int = 0
    reason: str = ""  # why it was held back, for the log


class AlertRouter:
    """In-process de-dup + flood control.

    In-process is the right scope here for the same reason job state is
    (see services/job_service.py's module docstring): this deployment runs
    a single API process, and the cost of a forgotten quiet-period across
    a restart is one extra email about a real problem -- strictly the safe
    direction to fail.
    """

    def __init__(
        self,
        realert_seconds: int = CRITICAL_REALERT_SECONDS,
        max_per_hour: int = MAX_EMAILS_PER_HOUR,
    ) -> None:
        self._seen: dict[str, _Tracked] = {}
        self._sent_at: list[float] = []
        self._realert = realert_seconds
        self._max_per_hour = max_per_hour

    def _prune_send_log(self, now: float) -> None:
        cutoff = now - 3600
        self._sent_at = [t for t in self._sent_at if t >= cutoff]

    def decide(self, incident: dict, now: Optional[float] = None) -> Decision:
        now = time.time() if now is None else now
        severity = severity_of(incident)
        fp = fingerprint(incident)
        tracked = self._seen.setdefault(fp, _Tracked())

        if severity != CRITICAL:
            tracked.suppressed += 1
            return Decision(False, severity, fp, reason=f"{severity} -- digest only")

        if tracked.last_sent and (now - tracked.last_sent) < self._realert:
            if not tracked.first_suppressed_at:
                tracked.first_suppressed_at = now
            tracked.suppressed += 1
            mins = int((now - tracked.last_sent) // 60)
            return Decision(
                False, severity, fp,
                reason=f"already alerted {mins}m ago for this exact break",
            )

        self._prune_send_log(now)
        if len(self._sent_at) >= self._max_per_hour:
            tracked.suppressed += 1
            return Decision(
                False, severity, fp,
                reason=f"flood ceiling ({self._max_per_hour}/hr) reached -- deferred to digest",
            )

        repeats = tracked.suppressed
        tracked.last_sent = now
        tracked.suppressed = 0
        tracked.first_suppressed_at = 0.0
        self._sent_at.append(now)
        return Decision(True, severity, fp, suppressed_since_last=repeats)

    def suppressed_counts(self) -> dict[str, int]:
        """Fingerprint -> occurrences held back since its last email.
        The digest reads this so a suppressed burst is reported, not lost."""
        return {fp: t.suppressed for fp, t in self._seen.items() if t.suppressed}

    def reset_suppressed(self) -> None:
        for tracked in self._seen.values():
            tracked.suppressed = 0
            tracked.first_suppressed_at = 0.0


# The process-wide router used by alerting_service.
router = AlertRouter()
