"""A vanity Facebook URL must still resolve to its numeric id.

THE DEFECT THIS GUARDS
    `visit()` polls JS_READY before reading anything, and is given an
    EMPTY needle for a vanity URL -- there is no numeric id to wait for
    yet. JS_READY starts with `id = !needle`, so an empty needle marks the
    id half already satisfied and the poll returns as soon as the
    social-context PAYLOAD lands. That happens well before the page's
    photo-album anchors render, and those anchors are exactly what
    `owner_id()` resolves the numeric id from.

    So `read_dom` saw an empty `pbIds` list, `resolve_id` fell through to
    "id unresolved -- fields not scope-verified", and the whole visit
    degraded.

    Confirmed live 2026-08-23 on facebook.com/AdaniOnline:

        before  profile_id = "AdaniOnline", note "id unresolved"
        after   profile_id = "100064457091354", note gone

    The same page given ~9s exposed six anchors, unanimously
    100064457091354, corroborated by `owning_profile_id` in the payloads.

    Unresolved is not cosmetic. Every field then comes from the UNSCOPED
    gql_* readers rather than this profile's own entity subtree -- on a
    page carrying suggested pages, sponsored blocks and commenters, each
    with their own name and follower count -- and the row files under the
    vanity slug instead of the numeric id, so one profile reached by its
    two URL shapes becomes two rows.
"""

from __future__ import annotations

import pytest

from backend.platforms.facebook.analysis_engine import Harvest, Scraper


class _Page:
    """Minimal Playwright-ish page. Records whether the album anchors were
    waited for, and only reveals them once they have been."""

    def __init__(self, *, anchors_appear: bool = True, ids=("42", "42", "7")):
        self.waited_for = None
        self._anchors_appear = anchors_appear
        self._ids = list(ids)
        self.scrolled_to_top = False

    async def wait_for_selector(self, selector, timeout=0):
        self.waited_for = selector
        if not self._anchors_appear:
            raise TimeoutError("no anchors on this profile")
        return object()

    async def evaluate(self, script, *args):
        if "scrollTo" in str(script):
            self.scrolled_to_top = True
            return None
        # JS_HEADER: anchors are only visible if they were waited for
        visible = self._ids if (self.waited_for and self._anchors_appear) else []
        return {"name": "Adani Group", "pbIds": visible, "verified": True}

    async def wait_for_timeout(self, ms):
        return None


class TestTheWaitHappensOnlyForAVanityUrl:
    @pytest.mark.asyncio
    async def test_a_vanity_url_waits_for_the_album_anchors(self):
        page, h = _Page(), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h, await_ids=True)
        assert page.waited_for == 'a[href*="set=pb."]'

    @pytest.mark.asyncio
    async def test_a_numeric_url_does_not_wait(self):
        """The id is already known there, so the wait would be pure latency
        added to every ordinary profile visit."""
        page, h = _Page(), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h, await_ids=False)
        assert page.waited_for is None

    @pytest.mark.asyncio
    async def test_the_default_is_not_to_wait(self):
        page, h = _Page(), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h)
        assert page.waited_for is None


class TestTheIdThenResolves:
    @pytest.mark.asyncio
    async def test_waiting_is_what_makes_owner_id_work(self):
        page, h = _Page(), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h, await_ids=True)
        assert Scraper.owner_id(h) == "42"

    @pytest.mark.asyncio
    async def test_without_the_wait_there_is_nothing_to_resolve_from(self):
        """The regression itself, expressed directly."""
        page, h = _Page(), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h, await_ids=False)
        assert Scraper.owner_id(h) == ""

    def test_owner_id_takes_the_most_frequent_id(self):
        """A page carries other entities' album links too, so the winner
        has to be the majority id, not merely the first one seen."""
        h = Harvest()
        h.dom = {"pbIds": ["7", "42", "42", "42", "9"]}
        assert Scraper.owner_id(h) == "42"


class TestAProfileWithNoAlbumAnchors:
    @pytest.mark.asyncio
    async def test_a_timeout_does_not_fail_the_visit(self):
        """A brand-new or photo-less account genuinely has none. The visit
        must continue and report honestly, not raise."""
        page, h = _Page(anchors_appear=False), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h, await_ids=True)
        assert h.dom.get("name") == "Adani Group"
        assert Scraper.owner_id(h) == ""

    @pytest.mark.asyncio
    async def test_the_rest_of_the_header_still_arrives(self):
        page, h = _Page(anchors_appear=False), Harvest()
        await Scraper.read_dom(Scraper.__new__(Scraper), page, h, await_ids=True)
        assert h.dom.get("verified") is True


class TestResolveIdReportsHonestly:
    def test_an_unresolvable_vanity_url_is_flagged(self):
        from backend.shared.models.row import Row

        row = Row(url="https://www.facebook.com/SomePage", target="X", original_feed="")
        row.profile_id = "SomePage"
        h = Harvest()
        pid = Scraper.resolve_id(Scraper.__new__(Scraper), row, h,
                                 "https://www.facebook.com/SomePage")
        assert pid == ""
        assert "not scope-verified" in row.notes

    def test_a_resolved_vanity_url_adopts_the_numeric_id(self):
        from backend.shared.models.row import Row

        row = Row(url="https://www.facebook.com/SomePage", target="X", original_feed="")
        row.profile_id = "SomePage"
        h = Harvest()
        h.dom = {"pbIds": ["42", "42"]}
        pid = Scraper.resolve_id(Scraper.__new__(Scraper), row, h,
                                 "https://www.facebook.com/SomePage")
        assert pid == "42"
        assert row.profile_id == "42"
        assert "not scope-verified" not in row.notes

    def test_a_numeric_url_short_circuits(self):
        from backend.shared.models.row import Row

        row = Row(url="https://www.facebook.com/profile.php?id=99",
                  target="X", original_feed="")
        row.profile_id = "99"
        assert Scraper.resolve_id(Scraper.__new__(Scraper), row, Harvest(), row.url) == "99"
