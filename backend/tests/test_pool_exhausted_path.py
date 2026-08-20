"""The session-pool-exhausted path in analysis_service._analyse_platform.

WHY THIS EXISTS
    The handler that runs when `session_for_job` can no longer hand out a
    healthy session interpolated `{attempted}` -- a name bound nowhere in
    that function. So the moment the pool ran dry it raised NameError from
    inside the except block, which meant:

      * the PoolExhausted incident was never recorded, so the one alert
        that explains WHY a run stopped never reached anyone,
      * the `break` after it never executed, and
      * a deliberate, graceful early stop became an unhandled crash.

    It survived because nothing exercised this branch: it only runs when
    the pool is already exhausted, which no other test set up. Static
    analysis (ruff F821) found it; this test keeps it found.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import analysis_service as svc
from backend.services.job_service import Job


class _NullMgr:
    async def emit(self, *a, **k):
        return None


class _FakePlatform:
    can_run_anonymously = False

    def scraper(self):  # pragma: no cover - never reached in this path
        raise AssertionError("no session was available; nothing should be scraped")


def _job() -> Job:
    return Job(id="jP", kind="analysis", client_id="cP", platform="facebook", params={})


@pytest.mark.asyncio
async def test_pool_exhaustion_stops_cleanly_and_reports_why():
    urls = [(f"https://facebook.com/p{i}", ["Acme"]) for i in range(3)]

    # `registry` is imported inside the function body, so it has to be
    # patched where it lives rather than on the analysis_service module.
    with patch("backend.services.analysis_service.sessions_engine.session_for_job",
               new=AsyncMock(side_effect=RuntimeError("no healthy session in pool"))), \
         patch("backend.platforms.registry.get", return_value=_FakePlatform()), \
         patch("backend.platforms.registry.session_state",
               new=AsyncMock(return_value="missing")), \
         patch("backend.services.analysis_service.incidents_engine.record",
               new_callable=AsyncMock) as rec:
        saved, new, attempted, stop_reason = await svc._analyse_platform(
            _job(), _NullMgr(), "facebook", urls, {})

    # It returns rather than raising -- the regression was a NameError here.
    assert stop_reason == "no healthy session remaining"
    assert attempted == 0
    assert (saved, new) == (0, 0)

    # ...and the operator is actually told why.
    rec.assert_awaited_once()
    assert rec.await_args.args[4] == "PoolExhausted"
    assert "0/3 profiles" in rec.await_args.args[5]
