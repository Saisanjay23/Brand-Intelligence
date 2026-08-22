"""Every analysed profile was scoring name_score=0, silently.

Confirmed live 2026-08-18 against real data: 152 of 154 analysed profiles,
across every platform, had `name_score == 0` and `target == None` in
Mongo. Root cause was `_analyse_platform`'s `target`, read once per JOB
from `params.get("target", "")`. No caller anywhere in the codebase
(profile_service.py's auto-trigger, scheduler_service.py's catch-up, or
this controller's manual run) ever set `params["target"]`, so every
profile in every run was scored via `name_score(row.profile_name, "")` --
and `name_score()` returns 0 whenever either side is empty. `name_score`
feeds `has_name_match`, which feeds the scoring rubric's base case
(`compute_score`: no name match -> BASE), so `risk_score` was quietly
depressed on every analysed row too.

The fix reads each URL's own `keywords` (the client keyword it was
actually discovered under, `$addToSet`'d onto the profile doc by
discovery) via `profile_repository.urls_for(with_keywords=True)`, and
scores each profile against ITS OWN keyword instead of a job-wide "".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import analysis_service as svc
from backend.services.job_service import Job


class _FakeRow:
    """Stands in for shared.models.row.Row: only what _analyse_platform
    and _row_to_fields touch."""

    def __init__(self, url: str, target: str, original_feed: str):
        self.url = url
        self.target = target
        self.original_feed = original_feed
        self.status = "OK"
        self.profile_id = ""
        self.profile_name = "Someone"
        self.entity_type = "profile"
        self.followers = None
        self.followers_exact = ""
        self.friends = None
        self.location = ""
        self.profile_pic_url = ""
        self.has_custom_pic = None
        self.verified = None
        self.posts_seen = ""
        self.last_post_iso = ""
        # Kept in step with shared/models/row.py. _row_to_fields reads both,
        # and the save path swallows an AttributeError, so a fake that
        # drifts from the real Row makes this test quietly stop exercising
        # the save at all instead of failing.
        self.bio = ""
        self.created_iso = ""
        self.risk = 0
        self.priority = ""
        self.notes = ""
        self.status_ = "OK"
        self.screenshot = ""
        self.src = {}

    def note(self, m):
        self.notes = m


class _FakeScraper:
    """Records the (url, target, feed) triple every .one() call receives."""

    calls: list[tuple[str, str, str]] = []

    def __init__(self, *a, **k):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    async def check_session(self):
        return True

    async def one(self, url, target, feed):
        _FakeScraper.calls.append((url, target, feed))
        return _FakeRow(url, target, feed)


class _FakePlatform:
    id = "facebook"

    def scraper(self):
        return _FakeScraper


@pytest.fixture(autouse=True)
def _reset():
    _FakeScraper.calls.clear()
    yield


def _job() -> Job:
    return Job(id="j1", kind="analysis", client_id="c1", platform="facebook", params={})


@pytest.mark.asyncio
async def test_each_url_is_scored_against_its_own_keyword():
    urls = [
        ("https://facebook.com/a", ["Gautam Adani"]),
        ("https://facebook.com/b", ["Pranav Adani"]),
    ]
    with patch("backend.services.analysis_service.sessions_engine.session_for_job",
               new=AsyncMock(return_value=(_FakePlatform(), {"id": "s1", "identifier": "acct",
                                                             "cookies": [], "proxy": None}))), \
         patch("backend.services.analysis_service.sessions_engine.mark_session_ok",
               new=AsyncMock()), \
         patch("backend.services.analysis_service.profiles_db.save_many",
               new=AsyncMock(return_value=(1, 1))), \
         patch("backend.services.analysis_service.health_engine.record"):
        await svc._analyse_platform(_job(), _NullMgr(), "facebook", urls, {})

    got = {url: target for url, target, _ in _FakeScraper.calls}
    assert got["https://facebook.com/a"] == "Gautam Adani"
    assert got["https://facebook.com/b"] == "Pranav Adani"
    # THE regression: neither call may go through empty, which is what
    # every real analysis run did before this fix
    assert "" not in got.values()


@pytest.mark.asyncio
async def test_a_url_with_no_recorded_keyword_is_not_worse_than_before():
    """A manually-added URL (profile_service.py's add_manual_urls) genuinely
    has no discovery keyword. That must fall back to "", not raise --
    exactly the previous (if universally wrong) baseline for that one
    legitimate case."""
    urls = [("https://facebook.com/manual", [])]
    with patch("backend.services.analysis_service.sessions_engine.session_for_job",
               new=AsyncMock(return_value=(_FakePlatform(), {"id": "s1", "identifier": "acct",
                                                             "cookies": [], "proxy": None}))), \
         patch("backend.services.analysis_service.sessions_engine.mark_session_ok",
               new=AsyncMock()), \
         patch("backend.services.analysis_service.profiles_db.save_many",
               new=AsyncMock(return_value=(1, 1))), \
         patch("backend.services.analysis_service.health_engine.record"):
        await svc._analyse_platform(_job(), _NullMgr(), "facebook", urls, {})

    assert _FakeScraper.calls[0][1] == ""


@pytest.mark.asyncio
async def test_a_profile_matched_under_two_keywords_uses_the_first():
    urls = [("https://facebook.com/multi", ["Gautam Adani", "Pranav Adani"])]
    with patch("backend.services.analysis_service.sessions_engine.session_for_job",
               new=AsyncMock(return_value=(_FakePlatform(), {"id": "s1", "identifier": "acct",
                                                             "cookies": [], "proxy": None}))), \
         patch("backend.services.analysis_service.sessions_engine.mark_session_ok",
               new=AsyncMock()), \
         patch("backend.services.analysis_service.profiles_db.save_many",
               new=AsyncMock(return_value=(1, 1))), \
         patch("backend.services.analysis_service.health_engine.record"):
        await svc._analyse_platform(_job(), _NullMgr(), "facebook", urls, {})

    assert _FakeScraper.calls[0][1] == "Gautam Adani"


class _NullMgr:
    async def emit(self, *a, **k):
        pass


class TestUrlsForWithKeywords:
    @pytest.mark.asyncio
    async def test_returns_url_keyword_pairs(self):
        from unittest.mock import MagicMock

        from backend.database.repositories import profile_repository as pdb

        class _Cursor:
            def __init__(self, docs):
                self._docs = docs

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for d in self._docs:
                    yield d

        docs = [
            {"url": "u1", "keywords": ["Gautam Adani"]},
            {"url": "u2", "keywords": []},
            {"url": "u3"},  # no keywords field at all -- must not KeyError
        ]

        fake_coll = MagicMock()
        fake_coll.find.return_value = _Cursor(docs)

        with patch.object(pdb, "db", return_value={pdb.PROFILES: fake_coll}):
            out = await pdb.urls_for("c1", "facebook", "approved", with_keywords=True)

        assert out == [
            ("u1", ["Gautam Adani"]),
            ("u2", []),
            ("u3", []),
        ]

    @pytest.mark.asyncio
    async def test_default_shape_is_unchanged(self):
        """The existing bare-url callers (scheduler_service's catch-up
        check) must see exactly the same shape as before."""
        from unittest.mock import MagicMock

        from backend.database.repositories import profile_repository as pdb

        class _Cursor:
            def __init__(self, docs):
                self._docs = docs

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for d in self._docs:
                    yield d

        fake_coll = MagicMock()
        fake_coll.find.return_value = _Cursor([{"url": "u1"}, {"url": "u2"}])

        with patch.object(pdb, "db", return_value={pdb.PROFILES: fake_coll}):
            out = await pdb.urls_for("c1", "facebook", "approved")

        assert out == ["u1", "u2"]
