from __future__ import annotations

from fastapi import APIRouter

from backend.controllers import settings_controller
from backend.dto.settings_dto import MailSettingsIn, MailSettingsOut, TestEmailResult

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/mail", response_model=MailSettingsOut)
async def get_mail_settings() -> MailSettingsOut:
    return await settings_controller.get_mail_settings()


@router.put("/mail", response_model=MailSettingsOut)
async def update_mail_settings(body: MailSettingsIn) -> MailSettingsOut:
    return await settings_controller.update_mail_settings(body)


@router.post("/mail/test", response_model=TestEmailResult)
async def send_test_email() -> TestEmailResult:
    return await settings_controller.send_test_email()
