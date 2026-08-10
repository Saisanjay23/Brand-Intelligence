"""read_last_post_date()'s grid-pinning robustness.

Confirmed live (adanifoundationschools, 2026-08-10): a profile's grid links
are NOT reliably newest-first -- the first three tiles were all dated
2025-09-01 (Instagram's "pin to grid" feature, up to 3 posts) while a
genuinely newer post (2026-08-08) sat in 4th position. No pin marker is
visible in a third party's view of the DOM, so this can't be fixed by
detecting "is this one pinned" -- it's fixed by reading several candidates
and taking the real max instead of trusting grid position, same as the
Twitter/Facebook pinned-post fixes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.platforms.instagram.analysis_engine import Scraper


def _scraper(timeout: int = 45) -> Scraper:
    s = Scraper.__new__(Scraper)  # skip __init__ -- no real session needed
    s.a = SimpleNamespace(timeout=timeout)
    return s


def _page(alt_dates: list[str], hrefs: list[str] | None = None, times: list[str | None] | None = None):
    """A page whose evaluate() answers JS_GRID_ALT_DATES first, then (only
    if tier 2 runs) JS_GRID_POST_LINKS, then JS_POST_TIME once per href."""
    page = AsyncMock()
    responses = [alt_dates]
    if hrefs is not None:
        responses.append(hrefs)
        responses.extend(times or [])
    page.evaluate = AsyncMock(side_effect=responses)
    return page


class TestParseAltDate:
    def test_photo_by_x_on_date_format(self):
        assert Scraper._parse_alt_date(
            "Photo by Adani Foundation Schools on September 01, 2025."
        ) == "2025-09-01"

    def test_with_trailing_may_be_an_image_suffix(self):
        assert Scraper._parse_alt_date(
            "Photo by Adani International School on August 09, 2026. "
            "May be an image of swimming, sport equipment, poster and text"
        ) == "2026-08-09"

    def test_tagged_shared_post_format(self):
        assert Scraper._parse_alt_date(
            "Photo shared by Guwahati Airport School on August 08, 2026 "
            "tagging @foundation.adani, and @adanifoundationschools."
        ) == "2026-08-08"

    def test_reel_caption_with_no_date_yields_nothing(self):
        # live-captured shape: reels carry only their own caption text,
        # no "Photo by X on <date>" wrapper at all
        assert Scraper._parse_alt_date(
            "Hosting Excellence | CBSE Cluster VII Boys Football Tournament"
        ) == ""

    def test_empty_or_none_yields_nothing(self):
        assert Scraper._parse_alt_date("") == ""
        assert Scraper._parse_alt_date(None) == ""

    def test_future_date_is_rejected(self):
        assert Scraper._parse_alt_date("Photo by X on January 01, 2099.") == ""

    def test_pre_instagram_date_is_rejected(self):
        assert Scraper._parse_alt_date("Photo by X on January 01, 2005.") == ""


class TestReadLastPostDateTier1AltText:
    async def test_the_live_captured_pin_scenario_picks_the_real_newest(self):
        # exact shape from adanifoundationschools: 3 pinned tiles dated
        # 2025-09-01, a genuinely newer post at 2026-08-08 further down
        alts = [
            "Photo by Adani Foundation Schools on September 01, 2025.",
            "Photo by Adani Foundation Schools on September 01, 2025.",
            "Photo by Adani Foundation Schools on September 01, 2025.",
            "Photo shared by Guwahati Airport School on August 08, 2026 "
            "tagging @adanifoundationschools.",
        ]
        s = _scraper()
        page = _page(alts)
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == "2026-08-08"

    async def test_reels_without_dates_are_ignored_photos_still_win(self):
        alts = [
            "Some reel caption with no date at all",
            "Photo by X on July 04, 2026.",
            "Another reel caption",
        ]
        s = _scraper()
        page = _page(alts)
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == "2026-07-04"


class TestReadLastPostDateTier2Fallback:
    async def test_all_reels_no_alt_dates_falls_back_to_visiting_grid_links(self):
        s = _scraper()
        page = _page(
            alt_dates=[],  # no photo tiles at all -- tier 1 empty
            hrefs=["/x/reel/aaa/", "/x/reel/bbb/", "/x/reel/ccc/"],
            times=[
                "2026-08-09T10:00:00.000Z",
                "2026-08-05T10:00:00.000Z",
                "2026-07-20T10:00:00.000Z",
            ],
        )
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == "2026-08-09"

    async def test_tier2_still_takes_the_max_not_the_first(self):
        # first grid link visited is not necessarily newest either -- must
        # still take max across all 3 candidates, not just use link 0
        s = _scraper()
        page = _page(
            alt_dates=[],
            hrefs=["/x/reel/aaa/", "/x/reel/bbb/", "/x/reel/ccc/"],
            times=[
                "2026-07-01T10:00:00.000Z",
                "2026-08-09T10:00:00.000Z",  # newest, but 2nd link visited
                "2026-07-20T10:00:00.000Z",
            ],
        )
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == "2026-08-09"

    async def test_failed_navigation_on_one_link_does_not_abort_the_others(self):
        s = _scraper()
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=[
            [],  # tier 1: no alt dates
            ["/x/reel/aaa/", "/x/reel/bbb/"],  # grid links
            "2026-08-05T10:00:00.000Z",  # time for link 2 (link 1 goto fails)
        ])
        calls = {"n": 0}

        async def flaky_goto(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("navigation failed")

        page.goto = AsyncMock(side_effect=flaky_goto)
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == "2026-08-05"

    async def test_no_grid_links_at_all_yields_nothing(self):
        s = _scraper()
        page = _page(alt_dates=[], hrefs=[])
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == ""


class TestGuards:
    async def test_private_account_returns_empty_without_evaluating_anything(self):
        s = _scraper()
        page = AsyncMock()
        result = await s.read_last_post_date(page, private=True, has_posts=True)
        assert result == ""
        page.evaluate.assert_not_called()

    async def test_no_posts_returns_empty_without_evaluating_anything(self):
        s = _scraper()
        page = AsyncMock()
        result = await s.read_last_post_date(page, private=False, has_posts=False)
        assert result == ""
        page.evaluate.assert_not_called()

    async def test_tier1_evaluate_failure_falls_through_to_tier2(self):
        s = _scraper()
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=[
            RuntimeError("boom"),  # tier 1 blows up
            ["/x/p/zzz/"],  # tier 2 grid links
            "2026-08-01T00:00:00.000Z",
        ])
        result = await s.read_last_post_date(page, private=False, has_posts=True)
        assert result == "2026-08-01"
