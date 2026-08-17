"""Request/response shapes for the mail-alerting settings resource."""

from __future__ import annotations

from pydantic import BaseModel


class MailSettingsIn(BaseModel):
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    # blank means "leave the currently-saved password untouched". GET
    # never returns the real password, so a save that always required
    # re-entering it would make editing the other fields impossible
    # without also having the password on hand.
    smtp_pass: str = ""
    alert_emails: list[str] = []
    alert_from: str = ""


class MailSettingsOut(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass_set: bool
    alert_emails: list[str]
    alert_from: str


class TestEmailResult(BaseModel):
    sent: bool
    detail: str = ""
