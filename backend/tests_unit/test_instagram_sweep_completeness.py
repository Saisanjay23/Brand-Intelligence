"""Instagram discovery's completeness reporting (backend/platforms/
instagram/discovery_engine.py::Discovery.sweep).

`Sweep.complete` is not cosmetic: services/discovery_service.py::
_sweep_platform builds its "N sweep(s) INCOMPLETE" analyst warning from
exactly this flag. An engine that truncates its own pagination and then
reports complete=True tells the operator it reached the end of Instagram's
results when it had simply run out of page budget -- the one silent
overstatement the rest of that accounting exists to prevent.

This engine used to do that unconditionally: a hardcoded ten-page loop
followed by `stopped="limit-reached"; complete=True`, with no way for a
caller asking for more depth to get it. These cover both halves -- the
flag, and the budget now coming from DiscoveryOptions like every other
platform's does.
"""

from __future__ import annotations

import json

import pytest

from backend.platforms.instagram import discovery_engine as ig
from backend.platforms.scan_options import DiscoveryOptions


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self._payload = payload

    async def text(self) -> str:
        return json.dumps(self._payload)


class FakeRequest:
    """Stands in for Playwright's `ctx.request`, recording every call so a
    test can assert how many pages were actually fetched."""

    def __init__(self, pages: list[dict]):
        self._pages = pages
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        # the last page repeats forever, so a test that never exhausts the
        # API is driven purely by the engine's own budget
        idx = min(len(self.calls) - 1, len(self._pages) - 1)
        return FakeResponse(self._pages[idx])


class FakeCtx:
    def __init__(self, pages: list[dict]):
        self.request = FakeRequest(pages)


def _user(username: str) -> dict:
    return {"pk": f"id-{username}", "username": username, "full_name": username.title()}


def _page(usernames: list[str], *, has_more: bool) -> dict:
    """One mobile-search response. `has_more` false AND no page_token is
    what the engine treats as Instagram genuinely running out."""
    blob: dict = {"users": [_user(u) for u in usernames], "has_more": has_more}
    if has_more:
        blob["page_token"] = "next-page-token"
    return blob


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """The engine paces itself 2.5s between pages; a ten-page test would
    otherwise spend 25 real seconds asleep."""
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(ig.asyncio, "sleep", _instant)


async def _sweep(pages: list[dict], **opts) -> tuple:
    ctx = FakeCtx(pages)
    discoverer = ig.Discovery(DiscoveryOptions(**opts), ctx)
    return await discoverer.sweep("acme"), ctx


class TestTruncatedSweepIsReportedIncomplete:
    @pytest.mark.asyncio
    async def test_exhausting_the_page_budget_is_not_complete(self):
        # every response still advertises another page: the engine stops on
        # its own budget, having never reached the end of the results
        sweep, _ = await _sweep([_page(["a", "b"], has_more=True)])
        assert sweep.complete is False
        assert sweep.stopped == "cap:pages"

    @pytest.mark.asyncio
    async def test_default_budget_is_ten_pages(self):
        _, ctx = await _sweep([_page(["a"], has_more=True)])
        assert len(ctx.request.calls) == ig.DEFAULT_MAX_PAGES

    @pytest.mark.asyncio
    async def test_stopped_code_is_not_mistaken_for_a_session_failure(self):
        # discovery_service.py runs classify_failure over `stopped` to decide
        # whether to quarantine the account; a page cap must never do that
        from backend.shared.resilience import classify_failure

        sweep, _ = await _sweep([_page(["a"], has_more=True)])
        assert classify_failure(sweep.stopped) is None


class TestGenuinelyExhaustedSweepStaysComplete:
    @pytest.mark.asyncio
    async def test_api_saying_it_has_no_more_is_complete(self):
        sweep, _ = await _sweep([_page(["a", "b"], has_more=False)])
        assert sweep.complete is True
        assert sweep.stopped == "exhausted"

    @pytest.mark.asyncio
    async def test_it_stops_asking_once_exhausted(self):
        _, ctx = await _sweep([_page(["a"], has_more=False)])
        assert len(ctx.request.calls) == 1


class TestCallerConfiguredPageBudget:
    @pytest.mark.asyncio
    async def test_max_pages_from_options_is_honoured(self):
        _, ctx = await _sweep([_page(["a"], has_more=True)], max_pages=3)
        assert len(ctx.request.calls) == 3

    @pytest.mark.asyncio
    async def test_a_deeper_budget_than_the_old_hardcoded_ten_is_reachable(self):
        # the whole point of reading this from options: a client configured
        # for more depth used to be silently held at ten pages
        _, ctx = await _sweep([_page(["a"], has_more=True)], max_pages=25)
        assert len(ctx.request.calls) == 25

    @pytest.mark.asyncio
    async def test_zero_means_use_the_default_not_zero_pages(self):
        # 0 is "unset" throughout DiscoveryOptions, never a real cap
        _, ctx = await _sweep([_page(["a"], has_more=True)], max_pages=0)
        assert len(ctx.request.calls) == ig.DEFAULT_MAX_PAGES
