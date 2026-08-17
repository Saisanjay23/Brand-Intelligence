"""Mail-alerting settings: read/write the live `Settings` singleton plus
persist to `.env` via `write_env` (see config/settings.py) so a value
entered through the Mail admin tab survives a process restart, the same
pattern already used for API-key credentials entered through the Sessions
API (see sessions/manager.py::save_api_key).
"""

from __future__ import annotations

from backend.config.settings import settings, write_env
from backend.dto.settings_dto import MailSettingsIn, MailSettingsOut, TestEmailResult


def get_mail_settings() -> MailSettingsOut:
    return MailSettingsOut(
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user,
        smtp_pass_set=bool(settings.smtp_pass),
        alert_emails=settings.alert_emails,
        alert_from=settings.alert_from,
    )


def update_mail_settings(body: MailSettingsIn) -> MailSettingsOut:
    write_env("SMTP_HOST", body.smtp_host)
    settings.smtp_host = body.smtp_host

    write_env("SMTP_PORT", str(body.smtp_port))
    settings.smtp_port = body.smtp_port

    write_env("SMTP_USER", body.smtp_user)
    settings.smtp_user = body.smtp_user

    # blank means "leave the stored password alone", see MailSettingsIn
    if body.smtp_pass.strip():
        write_env("SMTP_PASS", body.smtp_pass)
        settings.smtp_pass = body.smtp_pass

    emails = [e.strip() for e in body.alert_emails if e.strip()]
    write_env("ALERT_EMAILS", ",".join(emails))
    settings.alert_emails = emails

    write_env("ALERT_FROM", body.alert_from)
    settings.alert_from = body.alert_from

    return get_mail_settings()


def send_test_email() -> TestEmailResult:
    from backend.services.alerting_service import send_test_email as _send

    sent, detail = _send()
    return TestEmailResult(sent=sent, detail=detail)
