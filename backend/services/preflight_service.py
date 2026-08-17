"""Startup self-check: the misconfigurations that change results without
ever raising.

Every other detector in this codebase watches a running job. These are the
faults that are already in place before the first job starts, produce no
exception, and are invisible in the output -- the worst class to debug
because nothing looks wrong. Each is checked once at startup and reported
through the same incident/alert path as a scraper break, so a bad deploy
is an email on the day it ships rather than a discrepancy someone
eventually notices in a report.

Deliberately does NOT check "is Mongo up" or "is a session valid" -- those
already have their own health checks and their own alerts, and duplicating
them here would just double the noise on a bad morning.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.shared.logging import get_logger

log = get_logger("services.preflight")

SCOPE = "-- all clients --"  # matches the session-check convention


@dataclass(frozen=True)
class Finding:
    key: str
    message: str


def _check_fuzzy_matching() -> Finding | None:
    """`rapidfuzz` is optional at import (shared/text.py), and the fallback
    path is a DIFFERENT algorithm -- token overlap plus SequenceMatcher on
    sorted tokens, rather than token_set_ratio.

    That means the same profile scores differently depending on whether a
    library happens to be installed, with no error either way. A host that
    lost it in a rebuild silently reclassifies matches: profiles that
    should be flagged High land in Medium, drop below the review
    threshold, and are never seen. Nothing in the output says so.
    """
    from backend.shared import text

    if text.HAVE_RF:
        return None
    return Finding(
        "rapidfuzz-missing",
        "The 'rapidfuzz' library is not installed on this host, so name matching has silently "
        "fallen back to a different, less accurate algorithm (see backend/shared/text.py, the "
        "import guard at the top and name_score() at the bottom). The same profile will score "
        "differently here than on a host that has it, which moves profiles across the "
        "High/Medium match boundary and can hide real impersonators from review. "
        "Fix: run 'pip install -r requirements.txt' on this host and restart the service.",
    )


def _check_browser_fingerprint() -> Finding | None:
    """The user-agent must match a browser that actually exists here.

    stealth/fingerprint.py's own design note is that a UA claiming a
    version no local binary has is a worse tell than every session sharing
    one. When no browser is found at any known path, it advertises a
    hardcoded version that only gets staler -- which over time makes this
    fleet MORE identifiable, not less, and shows up as rising blocks and
    checkpoints rather than as an error.
    """
    from backend.stealth import fingerprint

    if fingerprint.CHROME_VERSION_DETECTED:
        return None
    return Finding(
        "chrome-version-stale",
        f"No Chrome/Chromium binary was found at any known path on this host, so every browser "
        f"session is advertising the hardcoded fallback version "
        f"{fingerprint.FALLBACK_CHROME_FULL} (see backend/stealth/fingerprint.py, CHROME_PATHS "
        f"and _detect_chrome_version()). Claiming a version no local binary has is exactly the "
        f"fingerprint tell that module exists to avoid, and it gets worse as the pinned version "
        f"ages -- expect rising bot-detection challenges on Facebook, Instagram and TikTok. "
        f"Fix: install Chrome/Chromium on this host, or add its real path to CHROME_PATHS.",
    )


def _check_alerting_configured() -> Finding | None:
    """If alerting itself is not configured, every check above is mute.

    This one can only be logged, never emailed -- there is nowhere to send
    it. It is still worth failing loudly in the log, because a deployment
    with no alert route is one where every other detector in this codebase
    silently does nothing.
    """
    from backend.config.settings import settings

    missing = []
    if not settings.alert_emails:
        missing.append("ALERT_EMAILS (no recipient)")
    if not settings.smtp_host:
        missing.append("SMTP_HOST (no mail server)")
    if not missing:
        return None
    return Finding(
        "alerting-unconfigured",
        "Alerting is not fully configured (" + ", ".join(missing) + "). Incidents will still be "
        "recorded and visible in the dashboard, but no email will be sent for a dead session or "
        "a broken parser. Fix: set these under Admin -> Mail settings, then use 'Send test email' "
        "to confirm delivery.",
    )


_CHECKS = (
    _check_alerting_configured,
    _check_fuzzy_matching,
    _check_browser_fingerprint,
)


async def run() -> list[Finding]:
    """Run every check. Never raises: a failure to self-check must not be
    what stops the service from starting."""
    findings: list[Finding] = []
    for check in _CHECKS:
        try:
            if finding := check():
                findings.append(finding)
        except Exception as e:
            log.error(f"preflight check {check.__name__} failed to run: {type(e).__name__}: {e}")

    if not findings:
        log.info("preflight: configuration OK")
        return []

    from backend.services import incident_service as incidents_engine

    for finding in findings:
        log.error(f"preflight [{finding.key}]: {finding.message}")
        # "alerting-unconfigured" is recorded but cannot be emailed by
        # definition; recording it still puts it in the dashboard and the
        # digest for whenever mail does get configured.
        try:
            await incidents_engine.record(
                "all", "config", SCOPE, "startup", "ConfigDrift", finding.message,
                where=finding.key,
            )
        except Exception as e:
            log.error(f"preflight could not record {finding.key}: {type(e).__name__}: {e}")
    return findings
