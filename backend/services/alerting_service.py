"""Email alerting: a debounced alert per incident, plus a daily digest."""

from __future__ import annotations

import asyncio
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from backend.config.settings import settings
from backend.shared.logging import get_logger

log = get_logger("services.alerting")

_LAST_ALERT: dict[str, float] = {}
DEBOUNCE_SECONDS = 3600  # 1 hour per platform


def _build_email(incident: dict) -> EmailMessage:
    platform_name = incident["platform"].title()
    is_session_invalid = incident.get("error_type") == "SessionInvalid"
    msg = EmailMessage()
    # SessionInvalid gets its own, unambiguous subject/opening line; this
    # is the one incident type that always means the same concrete thing
    # (discovery/analysis conclusively hit a login or checkpoint wall
    # instead of the profile it was scraping) and always needs the same
    # concrete action (paste fresh cookies/credentials for THIS platform),
    # so it says exactly that instead of the generic "pipeline incident"
    # framing every other error type shares.
    if is_session_invalid:
        msg["Subject"] = f"[Brand Intelligence] Login page detected -- please update the session for {platform_name}"
    else:
        msg["Subject"] = f"[Brand Intelligence] Alert: {platform_name} Pipeline Incident"
    msg["From"] = settings.alert_from
    msg["To"] = ", ".join(settings.alert_emails)
    source_file = incident.get("source_file", "")
    opening = (
        f"{platform_name}'s saved session was checked while running {incident['kind']} and found to be "
        f"logged out or challenged -- the scraper landed on a login/checkpoint page instead of the profile "
        f"it was reading. This account is now paused for {platform_name} until the session is replaced.\n\n"
        f"Please update the session for {platform_name} (paste fresh cookies under Sessions) before the "
        f"next run.\n\n"
        if is_session_invalid else
        f"An incident occurred in the '{incident['platform']}' {incident['kind']} pipeline.\n\n"
    )
    body = (
        opening
        + f"Scope: {incident['scope']}\n"
        f"Job ID: {incident['job_id']}\n"
        f"Target URL: {incident.get('url', '')}\n\n"
        f"--- Error Details ---\n"
        f"Error Type: {incident['error_type']}\n"
        f"Message: {incident['message']}\n\n"
        f"--- System Diagnosis ---\n"
        f"Cause: {incident['cause']}\n"
        f"Fix: {incident['fix']}\n"
        + (f"Likely source file: {source_file}\n" if source_file else "")
        + (
            "\n--- Exactly where it broke ---\n"
            "Each extraction method that failed, with the file and line to open.\n"
            f"{incident['where']}\n"
            if incident.get("where") else ""
        )
    )
    msg.set_content(body)
    return msg


def _send_sync(incident: dict) -> None:
    if not settings.alert_emails:
        log.info(f"skipping alert for {incident['platform']} (no ALERT_EMAILS configured)")
        return
    if not settings.smtp_host:
        log.info(f"[MOCK ALERT] {incident['platform']}: {incident['error_type']} - {incident['cause']}")
        return
    try:
        msg = _build_email(incident)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_user and settings.smtp_pass:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_pass)
            server.send_message(msg)
        log.info(f"sent email alert for {incident['platform']} incident")
    except Exception as e:
        log.error(f"failed to send email alert: {e}")


def _is_critical(incident: dict) -> bool:
    cause = incident.get("cause", "")
    if "no profile matched" in cause:
        return False
    if "The page failed to load in time" in cause:
        return False
    return True


async def notify_incident(incident: dict) -> None:
    if not _is_critical(incident):
        return
    now = time.time()
    last = _LAST_ALERT.get(incident["platform"], 0)
    if now - last < DEBOUNCE_SECONDS:
        log.debug(f"debouncing alert for {incident['platform']}")
        return
    _LAST_ALERT[incident["platform"]] = now
    await asyncio.to_thread(_send_sync, incident)


def send_test_email() -> tuple[bool, str]:
    """Synchronous on purpose, called from an admin "Send test email"
    button, which wants to know right away whether it worked, not a
    fire-and-forget best-effort like the real alert path. Returns
    (sent, detail) so the Mail settings UI can show the actual SMTP error
    (wrong port, auth rejected, ...) instead of a generic failure."""
    if not settings.alert_emails:
        return False, "No recipient email configured -- add at least one under Alert Emails first."
    if not settings.smtp_host:
        return False, "No SMTP host configured."
    msg = EmailMessage()
    msg["Subject"] = "[Brand Intelligence] Test email"
    msg["From"] = settings.alert_from
    msg["To"] = ", ".join(settings.alert_emails)
    msg.set_content(
        "This is a test email from the Brand Intelligence Suite's Mail settings tab.\n\n"
        "If you received this, incident/session alerts will reach this address."
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_user and settings.smtp_pass:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_pass)
            server.send_message(msg)
        return True, f"Sent to {', '.join(settings.alert_emails)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def send_daily_digest() -> None:
    from backend.database.repositories import incident_repository as incidents_db

    if not settings.alert_emails:
        log.info("skipping daily digest (no ALERT_EMAILS configured)")
        return

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    incidents = await incidents_db.since(yesterday)
    total = len(incidents)

    msg = EmailMessage()
    msg["Subject"] = "[Brand Intelligence] Daily Health Digest"
    msg["From"] = settings.alert_from
    msg["To"] = ", ".join(settings.alert_emails)

    body = "Brand Intelligence Pipeline - Daily Health Digest\n\n"
    body += f"In the past 24 hours, the system recorded {total} total incidents/minor errors.\n\n"
    if total > 0:
        platforms: dict[str, int] = {}
        for i in incidents:
            platforms[i["platform"]] = platforms.get(i["platform"], 0) + 1
        body += "--- Errors by Platform ---\n"
        for plat, count in platforms.items():
            body += f"{plat.title()}: {count}\n"
    msg.set_content(body)

    def _send():
        if not settings.smtp_host:
            log.info(f"[MOCK DIGEST] {total} incidents recorded today.")
            return
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                if settings.smtp_user and settings.smtp_pass:
                    server.starttls()
                    server.login(settings.smtp_user, settings.smtp_pass)
                server.send_message(msg)
            log.info("sent daily digest email")
        except Exception as e:
            log.error(f"failed to send daily digest: {e}")

    await asyncio.to_thread(_send)
