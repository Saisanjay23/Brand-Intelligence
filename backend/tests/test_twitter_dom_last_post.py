"""dom_last_post's core filtering: exclude reposts/pinned tweets so a DOM
fallback can't misreport someone else's repost as this account's activity.

Verified live: on a real account, the single newest tweet CELL on screen
was a repost, one day newer than that account's actual last original post
-- naively taking the newest <time> would have been silently wrong.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.platforms.twitter.analysis_engine import dom_last_post


def _page(cells):
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=cells)
    return page


class TestExcludesReposts:
    @pytest.mark.asyncio
    async def test_a_repost_newer_than_the_real_post_is_not_reported(self):
        # the exact live-captured shape: cell 0 is a repost one day newer
        # than the account's own last organic post
        cells = [
            {"dt": "2026-08-10T08:01:39.000Z", "repostOrPinned": True},
            {"dt": "2026-08-09T04:52:37.000Z", "repostOrPinned": False},
        ]
        assert await dom_last_post(_page(cells)) == "2026-08-09"

    @pytest.mark.asyncio
    async def test_all_reposts_yields_nothing_rather_than_a_repost_date(self):
        cells = [{"dt": "2026-08-10T08:01:39.000Z", "repostOrPinned": True}]
        assert await dom_last_post(_page(cells)) == ""


class TestPicksTheNewestOrganicPost:
    @pytest.mark.asyncio
    async def test_newest_of_several_organic_posts_wins(self):
        cells = [
            {"dt": "2026-06-01T15:41:51.000Z", "repostOrPinned": False},
            {"dt": "2026-08-08T14:48:55.000Z", "repostOrPinned": False},
            {"dt": "2026-07-18T09:03:45.000Z", "repostOrPinned": False},
        ]
        assert await dom_last_post(_page(cells)) == "2026-08-08"


class TestGuards:
    @pytest.mark.asyncio
    async def test_empty_timeline_returns_empty_not_a_guess(self):
        assert await dom_last_post(_page([])) == ""

    @pytest.mark.asyncio
    async def test_cell_with_no_time_element_is_skipped(self):
        cells = [{"dt": None, "repostOrPinned": False}]
        assert await dom_last_post(_page(cells)) == ""

    @pytest.mark.asyncio
    async def test_page_evaluate_failure_is_swallowed_not_raised(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("navigated away"))
        assert await dom_last_post(page) == ""
