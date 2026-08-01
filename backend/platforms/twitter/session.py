"""A browser session that knows what a broken X session looks like."""

from __future__ import annotations

import re

from backend.stealth.browser import Session

HOME = "https://x.com/home"

RE_LOGIN = re.compile(
    r"(Sign in to X|Log in to Twitter|Sign up for X|" r"Don't miss what's happening)",
    re.I,
)
RE_CHECKPOINT = re.compile(
    r"(Verify your identity|unusual login activity|"
    r"Your account has been locked|Confirm your)",
    re.I,
)
RE_GONE = re.compile(
    r"(This account doesn.t exist|Account suspended|" r"page doesn.t exist)", re.I
)


class TwitterSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(HOME, RE_LOGIN, RE_CHECKPOINT)
