"""Progress must stay honest while the re-attempt passes run.

THE SYMPTOM THIS FIXES
    A run reported "10/10" with status "running" and then sat there. It was
    not hung: `_analyse_platform` had visited every URL once, found some
    rows short of a field, and moved on to the completeness re-attempt
    passes -- which are deliberately the SLOWEST part of the run (one tab
    at a time, with a pause between each, up to `_COMPLETENESS_PASSES`
    times). So the phase that takes longest was also the phase that looked
    already finished.

    The cause was that progress counted UNIQUE URLs (`i = len(done)`, and
    `done` is a set), so re-visiting a URL could not move the number. The
    counter saturated and stopped meaning anything.

    Progress now counts VISITS against a total that grows when re-reads are
    queued, so it stays monotonic, never saturates early, and lands exactly
    on the total when the work actually ends.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import analysis_service as svc
from backend.services.job_service import Job


class _IncompleteRow:
    """A row that reads OK but comes away short of a field the platform
    publishes -- which is exactly what queues a re-attempt pass."""

    def __init__(self, url, target, feed):
        self.url, self.target, self.original_feed = url, target, feed
        self.status = "OK"
        self.profile_id = ""
        self.profile_name = "Someone"
        self.entity_type = "profile"
        self.followers = None          # missing -> incomplete
        self.followers_exact = ""
        self.friends = None
        self.location = ""
        self.bio = ""
        self.created_iso = ""
        self.profile_pic_url = ""
        self.has_custom_pic = None
        self.verified = None
        self.posts_seen = ""
        self.last_post_iso = ""        # missing -> incomplete
        self.risk = 0
        self.priority = ""
        # the derived properties _row_to_fields reads
        self.logo_yes = ""
        self.active_yes = "No"
        self.name_yes = ""
        self.name_score = 0
        self.name_exact_run = False
        self.notes = ""
        self.screenshot = ""
        self.src = {}

    def note(self, m):
        self.notes = m


class _Scraper:
    visits = 0

    def __init__(self, *a, **k):
        pass

    async def start(self): pass
    async def stop(self): pass
    async def pause(self, *a, **k): pass
    async def check_session(self): return True

    async def one(self, url, target, feed):
        _Scraper.visits += 1
        return _IncompleteRow(url, target, feed)


class _Platform:
    id = "facebook"

    def scraper(self):
        return _Scraper


class _RecordingMgr:
    """Captures the (processed, total) pairs the UI would render."""

    def __init__(self):
        self.samples: list[tuple[int, int]] = []
        self.statuses: list[str] = []
        self.processed = 0
        self.total = 0

    async def emit(self, job, type_, message="", found=None, total=None, **kw):
        if kw.get("platform_processed") is not None:
            self.processed = kw["platform_processed"]
        if kw.get("platform_total") is not None:
            self.total = kw["platform_total"]
        if kw.get("platform_status") is not None:
            self.statuses.append(kw["platform_status"])
        if kw.get("platform") and (self.processed or self.total):
            self.samples.append((self.processed, self.total))


def _job() -> Job:
    return Job(id="j1", kind="analysis", client_id="c1", platform="facebook", params={})


@pytest.fixture(autouse=True)
def _reset():
    _Scraper.visits = 0
    yield


async def _run(urls) -> _RecordingMgr:
    mgr = _RecordingMgr()
    with patch("backend.services.analysis_service.sessions_engine.session_for_job",
               new=AsyncMock(return_value=(_Platform(), {"id": "s1", "identifier": "a",
                                                         "cookies": [], "proxy": None}))), \
         patch("backend.services.analysis_service.sessions_engine.mark_session_ok", new=AsyncMock()), \
         patch("backend.services.analysis_service.profiles_db.save_many",
               new=AsyncMock(return_value=(1, 1))), \
         patch("backend.services.analysis_service.health_engine.record"):
        await svc._analyse_platform(_job(), mgr, "facebook", urls, {})
    return mgr


@pytest.mark.asyncio
async def test_the_counter_never_saturates_before_the_work_ends():
    """The regression: every sample after the first pass used to read
    2/2 while two more full passes were still to run."""
    urls = [("https://facebook.com/a", ["k"]), ("https://facebook.com/b", ["k"])]
    mgr = await _run(urls)
    # re-attempts really did happen, otherwise this test proves nothing
    assert _Scraper.visits > len(urls)

    # The precise property: once the counter reads processed == total, no
    # further visit may happen. Before the fix it read 2/2 with four more
    # visits still to come.
    first_full = next(
        (idx for idx, (p, t) in enumerate(mgr.samples) if p == t and t), None)
    assert first_full is not None, mgr.samples
    after = mgr.samples[first_full:]
    assert all(p == after[0][0] for p, _ in after), (
        f"work continued after the counter already read complete: {mgr.samples}")


@pytest.mark.asyncio
async def test_the_total_grows_to_cover_the_re_reads():
    urls = [("https://facebook.com/a", ["k"]), ("https://facebook.com/b", ["k"])]
    mgr = await _run(urls)
    assert mgr.total > len(urls)
    assert mgr.total == _Scraper.visits


@pytest.mark.asyncio
async def test_it_lands_exactly_on_the_total():
    urls = [("https://facebook.com/a", ["k"]), ("https://facebook.com/b", ["k"])]
    mgr = await _run(urls)
    assert mgr.processed == mgr.total, mgr.samples


@pytest.mark.asyncio
async def test_progress_is_monotonic():
    urls = [("https://facebook.com/a", ["k"]), ("https://facebook.com/b", ["k"])]
    mgr = await _run(urls)
    seen = [p for p, _ in mgr.samples]
    assert seen == sorted(seen), seen
