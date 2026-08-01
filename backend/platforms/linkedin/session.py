"""A browser session that knows what a broken LinkedIn session looks like.

UNVERIFIED -- unlike twitter/session.py and facebook/session.py, this has not
been run against a real LinkedIn cookie set (none is available in this
environment). The URL and the `/checkpoint/` path are well-documented and
stable; the login/checkpoint wording below is a best-effort guess and needs
confirming against a live session before this platform's check_session() can
be trusted the way the other four already are.
"""

from __future__ import annotations

import re

from backend.stealth.browser import Session

FEED = "https://www.linkedin.com/feed/"

RE_LOGIN = re.compile(r"(Sign in|Welcome [Bb]ack|Join LinkedIn)", re.I)
RE_CHECKPOINT = re.compile(
    r"(quick security check|unusual activity|verify (it.s|that it.s) you|"
    r"help us protect your account)",
    re.I,
)


class LinkedInSession(Session):
    async def check_session(self) -> bool:  # type: ignore[override]
        return await super().check_session(FEED, RE_LOGIN, RE_CHECKPOINT)
