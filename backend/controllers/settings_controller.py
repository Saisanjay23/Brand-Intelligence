from __future__ import annotations

from backend.dto.settings_dto import MailSettingsIn, MailSettingsOut, TestEmailResult
from backend.services import settings_service


async def get_mail_settings() -> MailSettingsOut:
    return settings_service.get_mail_settings()


async def update_mail_settings(body: MailSettingsIn) -> MailSettingsOut:
    return settings_service.update_mail_settings(body)


async def send_test_email() -> TestEmailResult:
    return settings_service.send_test_email()
