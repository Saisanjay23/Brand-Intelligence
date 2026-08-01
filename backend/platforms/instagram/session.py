"""A browser session that knows what a broken Instagram session looks like."""

from __future__ import annotations

import re

from backend.stealth.browser import Session

ME = "https://www.instagram.com/accounts/edit/"

RE_LOGIN = re.compile(
    r"(Log in to Instagram|Sign up to see|Log In\b.*Sign Up|"
    r"Phone number, username, or email)",
    re.I,
)
RE_CHECKPOINT = re.compile(
    r"(challenge_required|Suspicious Login|"
    r"We Detected An Unusual Login|confirm it.s you|"
    r"Help Us Confirm)",
    re.I,
)
RE_GONE = re.compile(
    r"(Sorry, this page isn.t available|" r"user not found|page not found)", re.I
)


class InstagramSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(ME, RE_LOGIN, RE_CHECKPOINT)
