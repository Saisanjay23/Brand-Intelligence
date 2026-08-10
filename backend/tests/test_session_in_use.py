"""_session_in_use() and the plumbing that feeds it: the Sessions panel's
"currently running" highlight and use-count. Deliberately derived from the
SAME Job objects job_manager.jobs already tracks (status/session_id/
session_platform) rather than a separately-managed "checked out" flag --
see _session_in_use's own docstring for why: it self-heals the instant a
job's status leaves "running" (done, failed, cancelled, or the
round-robin-slot crash recovery added earlier this session), with no
explicit "release" call needed anywhere, and no risk of a permanently-stuck
"running" badge if a job dies uncleanly.
"""

from __future__ import annotations

import pytest

from backend.services.job_service import Job, job_manager
from backend.sessions.manager import _public, _session_in_use


def _job(job_id: str, status: str, session_id: str = "", session_platform: str = "") -> Job:
    j = Job(id=job_id, kind="analysis", client_id="c1", platform=session_platform or None, params={})
    j.status = status
    j.session_id = session_id
    j.session_platform = session_platform
    return j


@pytest.fixture(autouse=True)
def _clean_jobs():
    saved = dict(job_manager.jobs)
    job_manager.jobs.clear()
    yield
    job_manager.jobs.clear()
    job_manager.jobs.update(saved)


class TestSessionInUse:
    def test_true_when_a_running_job_holds_this_exact_session(self):
        job_manager.jobs["j1"] = _job("j1", "running", session_id="abc123", session_platform="facebook")
        assert _session_in_use("facebook", "abc123") is True

    def test_false_when_no_job_references_this_session(self):
        job_manager.jobs["j1"] = _job("j1", "running", session_id="other", session_platform="facebook")
        assert _session_in_use("facebook", "abc123") is False

    def test_false_once_the_job_is_no_longer_running(self):
        # this is the self-healing property: no explicit "release" needed
        for status in ("done", "failed", "cancelled", "queued"):
            job_manager.jobs.clear()
            job_manager.jobs["j1"] = _job("j1", status, session_id="abc123", session_platform="facebook")
            assert _session_in_use("facebook", "abc123") is False, f"status={status}"

    def test_platform_must_match_not_just_the_session_id(self):
        # session ids are independently generated per platform (uuid4
        # hex[:8]) -- a low-probability but real cross-platform collision
        # must not falsely show "in use"
        job_manager.jobs["j1"] = _job("j1", "running", session_id="abc123", session_platform="twitter")
        assert _session_in_use("facebook", "abc123") is False

    def test_multiple_concurrent_jobs_each_match_their_own_session(self):
        job_manager.jobs["j1"] = _job("j1", "running", session_id="s1", session_platform="facebook")
        job_manager.jobs["j2"] = _job("j2", "running", session_id="s2", session_platform="instagram")
        assert _session_in_use("facebook", "s1") is True
        assert _session_in_use("instagram", "s2") is True
        assert _session_in_use("facebook", "s2") is False
        assert _session_in_use("instagram", "s1") is False


class TestPublicViewIncludesInUseAndUseCount:
    def test_in_use_true_surfaces_through_public(self):
        job_manager.jobs["j1"] = _job("j1", "running", session_id="s1", session_platform="facebook")
        item = {"platform": "facebook", "id": "s1", "identifier": "acct1", "status": "ready",
                "rate_limited_until": 0.0, "last_used": 0.0, "use_count": 7}
        out = _public(item)
        assert out["in_use"] is True
        assert out["use_count"] == 7

    def test_in_use_false_and_use_count_defaults_to_zero(self):
        item = {"platform": "facebook", "id": "s1", "identifier": "acct1", "status": "ready",
                "rate_limited_until": 0.0, "last_used": 0.0}
        out = _public(item)
        assert out["in_use"] is False
        assert out["use_count"] == 0


class TestEmitThreadsSessionIdOntoTheRealJob:
    """emit()'s direct-mutation branch (no _ipc_queue -- the parent process
    replaying an IPC item onto the real Job job_manager.jobs holds) is what
    _session_in_use actually reads. Confirms session_id/session_platform
    land on the job the same way new_profiles already does."""

    @pytest.mark.asyncio
    async def test_session_id_and_platform_are_set_on_the_job(self):
        job = Job(id="j1", kind="analysis", client_id="c1", platform=None, params={})
        await job_manager.emit(job, "progress", platform="facebook", session_id="abc123")
        assert job.session_id == "abc123"
        assert job.session_platform == "facebook"

    @pytest.mark.asyncio
    async def test_omitting_session_id_leaves_the_previous_value_untouched(self):
        job = Job(id="j1", kind="analysis", client_id="c1", platform=None, params={})
        job.session_id, job.session_platform = "abc123", "facebook"
        await job_manager.emit(job, "progress", message="unrelated update")
        assert job.session_id == "abc123"
        assert job.session_platform == "facebook"
