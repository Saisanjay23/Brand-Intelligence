"""A platform that needs no credentials must not be lost to a bad session.

Two entry points into the anonymous sweep, and the second one is easy to
miss:

  1. the pool has nothing healthy      -> decided up front, before any
                                          browser is started
  2. the pool SAYS healthy, then the session fails its own check_session()

(2) is the common one in practice. A quarantine lapses, the pooled item
goes available again, `session_state` reports "ready", and the sweep
commits to the credentialed path -- then check_session fails and the whole
platform used to die with "session <id> invalid or checkpointed", even
though it never needed the session at all. TikTok hits this every time its
cooldown expires, because its /upload probe reports a false negative for a
perfectly good account.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import discovery_service as svc
from backend.shared import keywords as kw


class _Mgr:
    async def emit(self, *a, **k):
        pass


class _Job:
    id = "j1"
    client_id = "c1"
    platform = "fake"
    new_profiles = 0
    params: dict = {}


class _Sweep:
    """Whatever the discoverer returns; only `hits` is read here."""

    def __init__(self):
        self.keyword, self.tab, self.hits = "acme", "people", []
        self.stopped, self.error = "exhausted", ""
        self.complete, self.extraction, self.users = True, None, []


class _Discoverer:
    """Records whether it was constructed for the anonymous path."""

    seen: list[bool] = []

    def __init__(self, options, ctx, anonymous: bool = False):
        _Discoverer.seen.append(anonymous)

    async def sweep(self, keyword, tab="people"):
        return _Sweep()

    async def stop(self):
        pass


class _DeadSession:
    """A pooled session that passes the pool's own opinion and then fails
    the moment it is actually exercised."""

    started = stopped = 0

    def __init__(self, *a, **k):
        self.ctx = object()

    async def start(self):
        _DeadSession.started += 1

    async def stop(self):
        _DeadSession.stopped += 1

    async def check_session(self):
        return False


class _Plat:
    id = "fake"
    session_path = "x:Y"
    can_run_anonymously = True

    def __init__(self, anon_cm):
        self._anon_cm = anon_cm

    def discoverer(self):
        return _Discoverer

    def session_cls(self):
        return _DeadSession

    def anonymous_context(self):
        return self._anon_cm


def _anon_context_factory(record: list):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm(proxy=None):
        record.append("entered")
        yield object()
        record.append("exited")

    return lambda: _cm


@pytest.fixture(autouse=True)
def _reset():
    _Discoverer.seen.clear()
    _DeadSession.started = _DeadSession.stopped = 0
    yield


async def _run(plat):
    # One childless parent, i.e. a plan that searches itself -- the shape
    # every client had before parent/child keyword groups existed, so this
    # test stays about the anonymous-session fallback and nothing else.
    # See backend/shared/keywords.py.
    plans = kw.build_plans({"name_keywords": ["acme"]})
    return await svc._sweep_platform(
        _Job(), _Mgr(), plat,
        plan_groups=[(kw.INDIVIDUAL, plans, 0)],
        tabs=["people"], params={},
    )


class TestFallsBackWhenAPooledSessionFailsItsCheck:
    @pytest.mark.asyncio
    async def test_the_sweep_still_runs_anonymously(self):
        used: list[str] = []
        plat = _Plat(_anon_context_factory(used)())

        with patch.object(svc.sessions_engine, "session_for_job",
                          new=AsyncMock(return_value=(plat, {"id": "s1", "identifier": "acct",
                                                            "cookies": [], "proxy": None}))), \
             patch.object(svc.sessions_engine, "mark_session_failed",
                          new=AsyncMock()) as failed, \
             patch("backend.platforms.registry.session_state",
                   new=AsyncMock(return_value="ready")):
            await _run(plat)

        assert used == ["entered", "exited"], "anonymous context was never used"
        assert _Discoverer.seen == [True], (
            f"expected exactly one anonymous discoverer, got {_Discoverer.seen}")
        # the bad session is still quarantined -- falling back must not
        # hide that the credential is broken
        failed.assert_awaited_once()
        # and its browser is closed BEFORE the anonymous profile opens
        assert _DeadSession.started == 1 and _DeadSession.stopped == 1

    @pytest.mark.asyncio
    async def test_it_does_not_raise_the_invalid_session_error(self):
        used: list[str] = []
        plat = _Plat(_anon_context_factory(used)())

        with patch.object(svc.sessions_engine, "session_for_job",
                          new=AsyncMock(return_value=(plat, {"id": "s1", "identifier": "acct",
                                                            "cookies": [], "proxy": None}))), \
             patch.object(svc.sessions_engine, "mark_session_failed", new=AsyncMock()), \
             patch("backend.platforms.registry.session_state",
                   new=AsyncMock(return_value="ready")):
            await _run(plat)  # must not raise "invalid or checkpointed"

    @pytest.mark.asyncio
    async def test_a_platform_that_needs_credentials_still_raises(self):
        """The fallback must be scoped to platforms that earned it: for
        everything else a dead session is still a hard failure."""
        used: list[str] = []
        plat = _Plat(_anon_context_factory(used)())
        plat.can_run_anonymously = False

        with patch.object(svc.sessions_engine, "session_for_job",
                          new=AsyncMock(return_value=(plat, {"id": "s1", "identifier": "acct",
                                                            "cookies": [], "proxy": None}))), \
             patch.object(svc.sessions_engine, "mark_session_failed", new=AsyncMock()), \
             patch("backend.platforms.registry.session_state",
                   new=AsyncMock(return_value="ready")):
            with pytest.raises(RuntimeError, match="invalid or checkpointed"):
                await _run(plat)
        assert used == [], "must not have opened an anonymous context"


class TestNoSessionAtAll:
    @pytest.mark.asyncio
    async def test_goes_straight_to_the_anonymous_path(self):
        """Entry point 1: the pool never offers a session, so no browser
        should be started for one."""
        used: list[str] = []
        plat = _Plat(_anon_context_factory(used)())

        with patch.object(svc.sessions_engine, "session_for_job",
                          new=AsyncMock(side_effect=AssertionError(
                              "must not ask the pool for a session"))), \
             patch("backend.platforms.registry.session_state",
                   new=AsyncMock(return_value="exhausted")):
            await _run(plat)

        assert used == ["entered", "exited"]
        assert _Discoverer.seen == [True]
        assert _DeadSession.started == 0
