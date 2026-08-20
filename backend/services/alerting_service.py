"""Email alerting: an interrupt for incidents that mean the pipeline has
stopped doing its job, and a daily digest for everything else.

Routing (which incidents earn an email, and how repeats are collapsed)
lives in services/alert_policy.py; the fix instructions that make an alert
actionable live in services/remediation.py. This module is delivery and
presentation only -- it decides nothing about severity itself.
"""

from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from backend.config.settings import settings
from backend.services import alert_policy, remediation
from backend.shared.logging import get_logger

log = get_logger("services.alerting")

# Kept as a module attribute because it is part of this module's public
# surface (the old per-platform debounce); the real de-duplication is now
# per-break, in alert_policy.AlertRouter.
DEBOUNCE_SECONDS = alert_policy.CRITICAL_REALERT_SECONDS

SMTP_TIMEOUT = 20  # never let a hung SMTP server park a worker thread forever

# Severity -> (accent colour, tint, label). Chosen for contrast against
# white in the major mail clients; email CSS support is too uneven for
# custom properties or a dark-mode media query, so these are literal.
_SEVERITY_STYLE = {
    alert_policy.CRITICAL: ("#b91c1c", "#fef2f2", "#fecaca", "Action required"),
    alert_policy.WARNING: ("#b45309", "#fffbeb", "#fde68a", "Needs attention"),
    alert_policy.INFO: ("#1d4ed8", "#eff6ff", "#bfdbfe", "For information"),
}


def _esc(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_email(incident: dict) -> EmailMessage:
    """The full alert: plain text for any client, HTML for most.

    Both parts carry the same information in the same order -- what broke,
    why, what to do about it, and exactly where -- so nothing depends on
    the recipient's client rendering HTML.
    """
    platform_name = incident["platform"].title()
    is_session_invalid = incident.get("error_type") == "SessionInvalid"
    severity = incident.get("severity") or alert_policy.severity_of(incident)
    book = remediation.playbook_for(incident.get("error_type", ""), incident.get("platform", ""))
    repeats = int(incident.get("suppressed_since_last") or 0)

    msg = EmailMessage()
    # SessionInvalid keeps its own unambiguous subject: it is the one
    # incident type that always means the same concrete thing (the scraper
    # hit a login/checkpoint wall instead of the profile) and always needs
    # the same concrete action (paste fresh cookies for THIS platform), so
    # it says that instead of the generic pipeline-incident framing.
    if is_session_invalid:
        msg["Subject"] = (
            f"[Brand Intelligence] Login page detected -- please update the session for {platform_name}"
        )
    else:
        msg["Subject"] = f"[Brand Intelligence] {platform_name} Pipeline Incident -- {book.headline}"
    msg["From"] = settings.alert_from
    msg["To"] = ", ".join(settings.alert_emails)

    source_file = incident.get("source_file", "")
    where = incident.get("where", "")

    opening = (
        f"{platform_name}'s saved session was checked while running {incident['kind']} and found to be "
        f"logged out or challenged -- the scraper landed on a login/checkpoint page instead of the profile "
        f"it was reading. This account is now paused for {platform_name} until the session is replaced.\n\n"
        f"Please update the session for {platform_name} (paste fresh cookies under Sessions) before the "
        f"next run.\n\n"
        if is_session_invalid else
        f"{book.headline}\n\n"
        f"This happened in the '{incident['platform']}' {incident['kind']} pipeline.\n\n"
    )

    steps_text = "".join(f"  {i}. {s}\n" for i, s in enumerate(book.steps, 1))
    repeat_text = (
        f"\nNOTE: this same failure also occurred {repeats} more time(s) since the last email "
        f"about it; those were collapsed into this one alert.\n"
        if repeats else ""
    )

    body = (
        opening
        + f"Scope: {incident['scope']}\n"
        f"Job ID: {incident['job_id']}\n"
        f"Target URL: {incident.get('url', '')}\n\n"
        f"--- What happened ---\n"
        f"Error Type: {incident['error_type']}\n"
        f"Message: {incident['message']}\n"
        + repeat_text
        + f"\n--- Why ---\n{incident['cause']}\n"
        + (f"\n--- What to do ---\n{steps_text}" if steps_text else "")
        + (f"\nSummary fix: {incident['fix']}\n" if incident.get("fix") else "")
        + (f"\n--- Where ---\nWork happens in: {book.folder}\n" if book.folder else "")
        + (f"Likely source file: {source_file}\n" if source_file else "")
        + (
            "\n--- Exactly where it broke ---\n"
            "Each extraction method that failed, with the file and line to open.\n"
            f"{where}\n"
            if where else ""
        )
    )
    msg.set_content(body)
    msg.add_alternative(
        _build_html(incident, platform_name, severity, book, repeats), subtype="html",
    )
    return msg


def _build_html(incident: dict, platform_name: str, severity: str, book, repeats: int) -> str:
    accent, tint, border, sev_label = _SEVERITY_STYLE.get(
        severity, _SEVERITY_STYLE[alert_policy.CRITICAL],
    )
    source_file = incident.get("source_file", "")
    where = incident.get("where", "")

    meta_rows = "".join(
        f'<tr>'
        f'<td style="padding:7px 14px;color:#6b7280;font-size:13px;white-space:nowrap;'
        f'border-bottom:1px solid #f3f4f6;">{_esc(k)}</td>'
        f'<td style="padding:7px 14px;color:#111827;font-size:13px;'
        f'border-bottom:1px solid #f3f4f6;word-break:break-all;">{_esc(v)}</td>'
        f'</tr>'
        for k, v in (
            ("Platform", platform_name),
            ("Pipeline", incident.get("kind", "")),
            ("Failure type", incident.get("error_type", "")),
            ("Client / scope", incident.get("scope", "")),
            ("Job ID", incident.get("job_id", "")),
            ("Target URL", incident.get("url", "")),
        )
        if v
    )

    steps_html = "".join(
        f'<li style="margin:0 0 9px 0;padding-left:4px;line-height:1.55;">{_esc(s)}</li>'
        for s in book.steps
    )
    steps_block = (
        f'''
      <tr><td style="padding:8px 30px 0 30px;">
        <div style="font-size:12px;font-weight:700;color:#166534;letter-spacing:.06em;text-transform:uppercase;margin-bottom:10px;">What to do</div>
        <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:16px 18px 16px 8px;">
          <ol style="margin:0;padding-left:28px;color:#14532d;font-size:14px;">{steps_html}</ol>
        </div>
      </td></tr>'''
        if steps_html else ""
    )

    folder_block = (
        f'''
      <tr><td style="padding:16px 30px 0 30px;">
        <div style="font-size:12px;font-weight:700;color:#374151;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px;">Where to make the change</div>
        <div style="font-family:ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;font-size:13px;color:#111827;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:11px 14px;word-break:break-all;">{_esc(book.folder)}</div>
      </td></tr>'''
        if book.folder else ""
    )

    source_block = (
        f'''
      <tr><td style="padding:10px 30px 0 30px;">
        <div style="font-size:13px;color:#6b7280;">Most likely file:
          <span style="font-family:ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;color:#111827;background:#f3f4f6;padding:2px 7px;border-radius:5px;word-break:break-all;">{_esc(source_file)}</span>
        </div>
      </td></tr>'''
        if source_file else ""
    )

    where_block = (
        f'''
      <tr><td style="padding:16px 30px 0 30px;">
        <div style="font-size:12px;font-weight:700;color:#9a3412;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px;">Exactly where it broke</div>
        <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:14px 16px;">
          <div style="font-size:12px;color:#9a3412;margin-bottom:8px;">Every extraction method that failed, with the file and line to open.</div>
          <pre style="margin:0;font-family:ui-monospace,SFMono-Regular,Consolas,Menlo,monospace;font-size:12px;color:#7c2d12;white-space:pre-wrap;word-break:break-word;">{_esc(where)}</pre>
        </div>
      </td></tr>'''
        if where else ""
    )

    repeat_block = (
        f'''
      <tr><td style="padding:14px 30px 0 30px;">
        <div style="background:#f3f4f6;border-left:3px solid #9ca3af;border-radius:0 6px 6px 0;padding:10px 14px;font-size:13px;color:#4b5563;">
          This same failure occurred <strong>{repeats}</strong> more time(s) since the last email about it. Those were collapsed into this alert.
        </div>
      </td></tr>'''
        if repeats else ""
    )

    return f"""\
<!doctype html>
<html>
<head><meta name="viewport" content="width=device-width,initial-scale=1"><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;-webkit-font-smoothing:antialiased;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" width="640" style="max-width:640px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 1px 4px rgba(16,24,40,.08);">

        <tr><td style="height:5px;background:{accent};font-size:0;line-height:0;">&nbsp;</td></tr>

        <tr><td style="padding:24px 30px 4px 30px;">
          <div style="display:inline-block;background:{tint};border:1px solid {border};color:{accent};font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:5px 11px;border-radius:999px;">{_esc(sev_label)}</div>
          <div style="color:#9ca3af;font-size:12px;margin-top:14px;letter-spacing:.04em;text-transform:uppercase;font-weight:600;">Brand Intelligence Suite</div>
          <h1 style="margin:6px 0 0 0;font-size:21px;line-height:1.35;color:#111827;font-weight:650;">{_esc(book.headline)}</h1>
          <div style="margin-top:7px;font-size:14px;color:#6b7280;">{_esc(platform_name)} &middot; {_esc(incident.get('kind', ''))} pipeline</div>
        </td></tr>

        <tr><td style="padding:20px 30px 0 30px;">
          <div style="font-size:12px;font-weight:700;color:#991b1b;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px;">Why this happened</div>
          <div style="background:{tint};border:1px solid {border};border-radius:10px;padding:14px 16px;font-size:14px;line-height:1.6;color:#111827;">{_esc(incident.get('cause', ''))}</div>
        </td></tr>
{repeat_block}{steps_block}{folder_block}{source_block}{where_block}
        <tr><td style="padding:20px 30px 0 30px;">
          <div style="font-size:12px;font-weight:700;color:#374151;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px;">What the system observed</div>
          <div style="font-size:13px;line-height:1.6;color:#4b5563;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:13px 15px;word-break:break-word;">{_esc(incident.get('message', ''))}</div>
        </td></tr>

        <tr><td style="padding:20px 30px 0 30px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;border-collapse:separate;overflow:hidden;">{meta_rows}</table>
        </td></tr>

        <tr><td style="padding:24px 30px 26px 30px;">
          <div style="border-top:1px solid #e5e7eb;padding-top:14px;font-size:12px;line-height:1.6;color:#9ca3af;">
            Automated alert from the Brand Intelligence pipeline. You receive these only when the pipeline
            has stopped doing its job &mdash; a dead session, a broken parser, an exhausted quota.
            Repeats of the same failure are collapsed; lower-severity issues appear in the daily digest instead.
          </div>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _smtp_send(msg: EmailMessage) -> None:
    """One place that knows how to talk to the configured SMTP server.

    STARTTLS is attempted whenever credentials are configured; a server
    that does not offer it still gets the message (the local dev relay on
    port 1025 has no TLS and no auth), because refusing to alert at all is
    a worse failure than alerting over a trusted local hop.
    """
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT) as server:
        if settings.smtp_user and settings.smtp_pass:
            try:
                server.starttls()
            except smtplib.SMTPNotSupportedError:
                log.warning("SMTP server does not support STARTTLS -- sending unencrypted")
            server.login(settings.smtp_user, settings.smtp_pass)
        server.send_message(msg)


def _send_sync(incident: dict) -> None:
    if not settings.alert_emails:
        log.info(f"skipping alert for {incident['platform']} (no ALERT_EMAILS configured)")
        return
    if not settings.smtp_host:
        log.info(f"[MOCK ALERT] {incident['platform']}: {incident['error_type']} - {incident['cause']}")
        return
    try:
        _smtp_send(_build_email(incident))
        log.info(
            f"sent {incident.get('severity', '?')} alert for {incident['platform']} "
            f"{incident['error_type']}"
        )
    except Exception as e:
        # An alert that cannot be delivered must never propagate into the
        # job that raised it; the incident is already durably recorded in
        # Mongo and will still appear in the dashboard and the digest.
        log.error(f"failed to send email alert: {type(e).__name__}: {e}")


def _is_critical(incident: dict) -> bool:
    """Retained for callers/tests that ask this question directly; the
    routing itself goes through alert_policy."""
    return alert_policy.severity_of(incident) == alert_policy.CRITICAL


async def notify_incident(incident: dict) -> None:
    """Route one incident. Never raises: alerting is best-effort by design."""
    try:
        decision = alert_policy.router.decide(incident)
    except Exception as e:
        log.error(f"alert routing failed, sending anyway: {type(e).__name__}: {e}")
        await asyncio.to_thread(_send_sync, incident)
        return

    incident["severity"] = decision.severity
    incident["fingerprint"] = decision.fingerprint
    if not decision.send:
        log.debug(
            f"holding {incident.get('platform')} {incident.get('error_type')} "
            f"[{decision.fingerprint}]: {decision.reason}"
        )
        return
    incident["suppressed_since_last"] = decision.suppressed_since_last
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
    msg.add_alternative(
        """\
<!doctype html><html><body style="margin:0;padding:28px 12px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" width="560" style="max-width:560px;width:100%;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 1px 4px rgba(16,24,40,.08);">
<tr><td style="height:5px;background:#15803d;font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td style="padding:26px 30px 28px 30px;">
<div style="color:#9ca3af;font-size:12px;letter-spacing:.04em;text-transform:uppercase;font-weight:600;">Brand Intelligence Suite</div>
<h1 style="margin:6px 0 12px 0;font-size:20px;color:#111827;font-weight:650;">Test email delivered</h1>
<div style="font-size:14px;line-height:1.6;color:#4b5563;">Your SMTP settings are working. Incident and session alerts will reach this address.</div>
</td></tr></table></td></tr></table></body></html>""",
        subtype="html",
    )
    try:
        _smtp_send(msg)
        return True, f"Sent to {', '.join(settings.alert_emails)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def send_daily_digest() -> None:
    """Once a day: everything that happened, including what was
    deliberately not emailed at the time.

    This is the other half of the anti-spam design -- a warning that did
    not justify an interrupt still has to surface somewhere, or
    suppressing it would just be hiding it.
    """
    from backend.database.repositories import incident_repository as incidents_db

    if not settings.alert_emails:
        log.info("skipping daily digest (no ALERT_EMAILS configured)")
        return

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        incidents = await incidents_db.since(yesterday)
    except Exception as e:
        log.error(f"daily digest could not read incidents: {type(e).__name__}: {e}")
        return
    total = len(incidents)

    buckets: dict[str, list[dict]] = {
        alert_policy.CRITICAL: [], alert_policy.WARNING: [], alert_policy.INFO: [],
    }
    for i in incidents:
        buckets[i.get("severity") or alert_policy.severity_of(i)].append(i)

    def _group(rows: list[dict]) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for r in rows:
            key = (r.get("platform", "?"), r.get("error_type", "?"))
            out[key] = out.get(key, 0) + 1
        return out

    msg = EmailMessage()
    n_crit = len(buckets[alert_policy.CRITICAL])
    headline = (
        f"{n_crit} issue(s) needing action" if n_crit else
        (f"{total} minor event(s), nothing needing action" if total else "All clear")
    )
    msg["Subject"] = f"[Brand Intelligence] Daily digest -- {headline}"
    msg["From"] = settings.alert_from
    msg["To"] = ", ".join(settings.alert_emails)

    text = f"Brand Intelligence -- Daily Health Digest\n\n{headline}.\n"
    text += f"{total} event(s) recorded in the last 24 hours.\n"
    for sev in (alert_policy.CRITICAL, alert_policy.WARNING, alert_policy.INFO):
        grouped = _group(buckets[sev])
        if not grouped:
            continue
        text += f"\n--- {sev.upper()} ---\n"
        for (plat, etype), count in sorted(grouped.items(), key=lambda kv: -kv[1]):
            text += f"  {plat.title()}: {etype} x{count}\n"
    msg.set_content(text)

    sections = ""
    for sev in (alert_policy.CRITICAL, alert_policy.WARNING, alert_policy.INFO):
        grouped = _group(buckets[sev])
        if not grouped:
            continue
        accent, tint, border, label = _SEVERITY_STYLE[sev]
        rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 14px;font-size:13px;color:#111827;border-bottom:1px solid #f3f4f6;">{_esc(plat.title())}</td>'
            f'<td style="padding:8px 14px;font-size:13px;color:#4b5563;border-bottom:1px solid #f3f4f6;">{_esc(etype)}</td>'
            f'<td style="padding:8px 14px;font-size:13px;color:#111827;text-align:right;border-bottom:1px solid #f3f4f6;font-weight:600;">{count}</td>'
            f'</tr>'
            for (plat, etype), count in sorted(grouped.items(), key=lambda kv: -kv[1])
        )
        sections += f'''
        <tr><td style="padding:18px 30px 0 30px;">
          <div style="display:inline-block;background:{tint};border:1px solid {border};color:{accent};font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:4px 10px;border-radius:999px;margin-bottom:10px;">{_esc(label)}</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:10px;border-collapse:separate;overflow:hidden;">{rows}</table>
        </td></tr>'''

    if not sections:
        sections = '''
        <tr><td style="padding:18px 30px 0 30px;">
          <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:16px;font-size:14px;color:#14532d;">No incidents recorded in the last 24 hours.</div>
        </td></tr>'''

    msg.add_alternative(f"""\
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:28px 12px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" width="640" style="max-width:640px;width:100%;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 1px 4px rgba(16,24,40,.08);">
<tr><td style="height:5px;background:{'#b91c1c' if n_crit else '#15803d'};font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td style="padding:24px 30px 0 30px;">
  <div style="color:#9ca3af;font-size:12px;letter-spacing:.04em;text-transform:uppercase;font-weight:600;">Brand Intelligence Suite</div>
  <h1 style="margin:6px 0 0 0;font-size:21px;color:#111827;font-weight:650;">Daily health digest</h1>
  <div style="margin-top:7px;font-size:14px;color:#6b7280;">{_esc(headline)} &middot; {total} event(s) in 24 hours</div>
</td></tr>
{sections}
<tr><td style="padding:24px 30px 26px 30px;">
  <div style="border-top:1px solid #e5e7eb;padding-top:14px;font-size:12px;line-height:1.6;color:#9ca3af;">
    Items marked <strong>Action required</strong> were also emailed individually when they happened.
    Lower-severity items appear here only, so the inbox stays quiet without anything being dropped.
  </div>
</td></tr>
</table></td></tr></table></body></html>""", subtype="html")

    def _send():
        if not settings.smtp_host:
            log.info(f"[MOCK DIGEST] {total} incidents recorded today.")
            return
        try:
            _smtp_send(msg)
            log.info("sent daily digest email")
        except Exception as e:
            log.error(f"failed to send daily digest: {type(e).__name__}: {e}")

    await asyncio.to_thread(_send)
    alert_policy.router.reset_suppressed()
