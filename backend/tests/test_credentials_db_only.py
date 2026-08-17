"""YouTube/Telegram credentials are no longer written to .env -- Mongo
(sessions_db) is the sole durable store, and sessions/manager.py::state_for
already re-hydrates os.environ from it on every startup (see that
function). Writing to .env on top of that was redundant persistence with a
real cost: a plaintext credential on disk, in a file `write_env` itself
refuses to touch outside development. Setting os.environ in-process (never
touching disk) is what these call sites do now instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.sessions import manager as sessions_manager
from backend.services import telegram_login_service


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("YOUTUBE_API_KEY", "TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.asyncio
async def test_save_api_key_never_touches_env_file(monkeypatch):
    with patch.object(sessions_manager.sessions_db, "save_api_key_session", new=AsyncMock(return_value={})), \
         patch.object(sessions_manager, "status", new=AsyncMock(return_value={})), \
         patch("backend.config.settings.write_env") as write_env_mock:
        await sessions_manager.save_api_key("youtube", "AIza-fake-key-for-a-dummy-account")

    write_env_mock.assert_not_called()
    assert __import__("os").environ["YOUTUBE_API_KEY"] == "AIza-fake-key-for-a-dummy-account"


@pytest.mark.asyncio
async def test_update_session_credentials_never_touches_env_file(monkeypatch):
    with patch.object(sessions_manager.sessions_db, "update_session_credentials", new=AsyncMock(return_value={"ok": True})), \
         patch.object(sessions_manager, "status", new=AsyncMock(return_value={})), \
         patch("backend.config.settings.write_env") as write_env_mock:
        await sessions_manager.update_session_credentials("youtube", "sess1", api_key="AIza-rotated-fake-key")

    write_env_mock.assert_not_called()
    assert __import__("os").environ["YOUTUBE_API_KEY"] == "AIza-rotated-fake-key"


@pytest.mark.asyncio
async def test_telegram_send_code_never_touches_env_file(monkeypatch, tmp_path):
    class _FakeClient:
        async def connect(self):
            pass

        async def send_code_request(self, phone):
            return type("Sent", (), {"phone_code_hash": "hash123"})()

    monkeypatch.setattr(telegram_login_service, "HAVE_TELETHON", True)
    monkeypatch.setattr(telegram_login_service, "TelegramClient", lambda *a, **k: _FakeClient())
    # send_code() does real filesystem work (mkdir + stale-session cleanup)
    # against settings.session_blob_path -- point it at a throwaway temp
    # dir so this test never touches the real repo's session/ folder.
    monkeypatch.setattr(telegram_login_service.settings, "session_blob_path", tmp_path, raising=False)

    with patch("backend.config.settings.write_env") as settings_write_env_mock:
        await telegram_login_service.send_code(api_id=12345, api_hash="dummyhash", phone="+10000000000")

    settings_write_env_mock.assert_not_called()
    import os
    assert os.environ["TELEGRAM_API_ID"] == "12345"
    assert os.environ["TELEGRAM_API_HASH"] == "dummyhash"

    await telegram_login_service.cancel()
