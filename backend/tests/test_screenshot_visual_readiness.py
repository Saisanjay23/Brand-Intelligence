"""Session.wait_for_visible_content (backend/stealth/browser.py) -- the
remaining defense-in-depth every platform's screenshot() waits on before
capturing, now that the actual root cause (build_extra_headers() forcing
Upgrade-Insecure-Requests onto cross-origin CDN requests and breaking their
CORS preflight, so Facebook's client-side app never got past its own
loading splash) is fixed in headers.py. Uses Playwright's own inner_text(),
not a raw page.evaluate() of document.body.innerText -- confirmed live that
the latter reads 0 in this headless Chromium configuration even once real
content is on screen.
"""

from __future__ import annotations

import pytest

from backend.stealth.browser import Session


class _FakePage:
    """Simulates inner_text("body") growing from a "splash" length to a
    "real page" length after some number of polls -- exactly the race this
    gate exists to close."""

    def __init__(self, lengths: list[int]):
        self._lengths = list(lengths)
        self.waited_ms = 0

    async def inner_text(self, _selector: str) -> str:
        length = self._lengths.pop(0) if len(self._lengths) > 1 else self._lengths[0]
        return "x" * length

    async def wait_for_timeout(self, ms: int) -> None:
        self.waited_ms += ms


def _session() -> Session:
    # Session.__init__ needs real-looking options/cookies but never touches
    # a browser until .start() -- constructing one for this pure method call
    # is safe and matches how the rest of the suite exercises helpers that
    # live on heavier classes.
    return Session(options=object(), cookies=[])


@pytest.mark.asyncio
async def test_returns_immediately_when_content_is_already_there():
    page = _FakePage([500])
    session = _session()
    await session.wait_for_visible_content(page, min_chars=200, timeout_ms=4000, poll_ms=250)
    assert page.waited_ms == 0


@pytest.mark.asyncio
async def test_polls_until_the_splash_gives_way_to_real_content():
    # 3 splash reads (30 chars, matching Facebook's actual "from Meta"
    # splash) then real content -- must poll exactly 3 times, not give up.
    page = _FakePage([30, 30, 30, 450])
    session = _session()
    await session.wait_for_visible_content(page, min_chars=200, timeout_ms=4000, poll_ms=250)
    assert page.waited_ms == 750


@pytest.mark.asyncio
async def test_gives_up_after_timeout_without_raising():
    # Never clears the threshold -- must return (not hang/raise) once
    # timeout_ms is exhausted, so a genuinely stuck page still gets SOME
    # screenshot rather than blocking the analysis run indefinitely.
    page = _FakePage([10])
    session = _session()
    await session.wait_for_visible_content(page, min_chars=200, timeout_ms=1000, poll_ms=250)
    assert page.waited_ms == 1000


@pytest.mark.asyncio
async def test_a_closed_or_navigated_away_page_does_not_raise():
    class _DeadPage:
        async def inner_text(self, _selector: str):
            raise Exception("Target closed")

    session = _session()
    await session.wait_for_visible_content(_DeadPage(), min_chars=200, timeout_ms=4000, poll_ms=250)
