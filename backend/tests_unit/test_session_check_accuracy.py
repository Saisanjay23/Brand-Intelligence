"""check_session() across platforms must return False ONLY on conclusive
proof the session/key is genuinely dead (a login/checkpoint wall, a
specific auth-rejection error) -- never for a transient/inconclusive
failure (network blip, timeout, daily quota exhaustion, FloodWait). A
false "dead" verdict here is what quarantines a perfectly good account and
fires an incorrect SessionInvalid email.

Regression coverage for two real bugs found live:
  - YouTube's check_session used to return False for BOTH daily quota
    exhaustion (a normal, expected state) and any other exception
    (including a bare network blip), identically to a genuinely rejected
    key.
  - Telegram's check_session used to catch bare `Exception`, so a dropped
    connection during the health check was indistinguishable from Telegram
    actually revoking the session.
"""

from __future__ import annotations

import os

import pytest

from backend.platforms.youtube.discovery_engine import QuotaExceeded
from backend.platforms.youtube.analysis_engine import Scraper as YouTubeScraper


class _FakeAPI:
    def __init__(self, effect):
        self._effect = effect

    async def get(self, *a, **k):
        if isinstance(self._effect, Exception):
            raise self._effect
        return self._effect


def _scraper(effect) -> YouTubeScraper:
    os.environ.setdefault("YOUTUBE_API_KEY", "test-key-for-unit-tests")
    s = YouTubeScraper(args=object())
    s.api = _FakeAPI(effect)
    return s


class TestYouTubeCheckSessionAccuracy:
    @pytest.mark.asyncio
    async def test_a_working_key_returns_true(self):
        assert await _scraper({"items": []}).check_session() is True

    @pytest.mark.asyncio
    async def test_quota_exhaustion_is_not_a_dead_key(self):
        """The bug: this used to return False, wrongly quarantining a
        perfectly good key every single day quota ran out first."""
        assert await _scraper(QuotaExceeded("daily quota exhausted")).check_session() is True

    @pytest.mark.asyncio
    async def test_a_genuinely_rejected_key_returns_false(self):
        got = await _scraper(RuntimeError("youtube channels 403: API key not valid")).check_session()
        assert got is False

    @pytest.mark.asyncio
    async def test_an_unclassified_error_is_not_conclusive(self):
        """Not evidence the KEY is bad -- must not be swallowed into a
        blanket False the way it used to be; propagates instead so the
        caller can tell "inconclusive" from "genuinely dead"."""
        with pytest.raises(RuntimeError):
            await _scraper(RuntimeError("some transient network hiccup")).check_session()
