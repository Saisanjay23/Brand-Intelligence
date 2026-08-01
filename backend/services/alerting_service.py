"""Email alerting: a debounced alert per incident, plus a daily digest."""

from __future__ import annotations

import asyncio
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from backend.config.settings import settings
from backend.utils.logging import get_logger

log = get_logger("services.alerting")

_LAST_ALERT: dict[str, float] = {}
DEBOUNCE_SECONDS = 3600  # 1 hour per platform


def _build_email(incident: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[Brand Intelligence] Alert: {incident['platform'].title()} Pipeline Incident"
    msg["From"] = settings.alert_from
    msg["To"] = ", ".join(settings.alert_emails)
    body = (
        f"An incident occurred in the '{incident['platform']}' {incident['kind']} pipeline.\n\n"
        f"Scope: {incident['scope']}\n"
        f"Job ID: {incident['job_id']}\n"
        f"Target URL: {incident.get('url', '')}\n\n"
        f"--- Error Details ---\n"
        f"Error Type: {incident['error_type']}\n"
        f"Message: {incident['message']}\n\n"
        f"--- System Diagnosis ---\n"
        f"Cause: {incident['cause']}\n"
        f"Fix: {incident['fix']}\n"
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


async def send_daily_digest() -> None:
    from backend.repositories import incident_repository as incidents_db

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
