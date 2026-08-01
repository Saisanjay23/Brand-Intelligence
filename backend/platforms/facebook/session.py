"""A browser session that knows what a broken Facebook session looks like."""

from __future__ import annotations

from backend.platforms.facebook.constants import RE_CHECKPOINT, RE_LOGIN
from backend.stealth.browser import Session

ME = "https://www.facebook.com/me"


class FacebookSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(ME, RE_LOGIN, RE_CHECKPOINT)
