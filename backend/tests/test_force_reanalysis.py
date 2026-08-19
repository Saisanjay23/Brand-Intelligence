"""`POST /analysis {force: true}` -- the analyst-facing "run analysis again"
button.

Without this, clicking the manual analysis trigger after the auto-trigger-
on-approve (or the 20-minute catch-up sweep) had already cleared the normal
backlog to zero did nothing at all: `run_analysis` would find zero
approved-and-unanalysed URLs and immediately report "nothing to analyse,
already up to date". The button looked broken. `force=True` instead
re-analyses every currently-approved profile regardless of whether an
earlier run already scored it.

Two things this has to get right:
  1. `run_analysis` must actually query WITHOUT excluding already-analysed
     profiles when force is set.
  2. The job-coalescing introduced for the per-card approve flood (see
     JobManager.existing_queued) must never fold a forced request into an
     already-queued PLAIN job -- that would silently turn "run everything
     again" into a no-op the moment the plain job finds nothing new.
"""

from __future__ import annotations

import pytest

from backend.services.job_service import ANALYSIS, QUEUED, Job, JobManager


def _fresh_manager() -> JobManager:
    mgr = JobManager()
    mgr._init()
    return mgr


def _queued_job(id_: str, client_id: str = "c1", platform=None, force: bool = False) -> Job:
    return Job(id=id_, kind=ANALYSIS, client_id=client_id, platform=platform,
               params={"force": force}, status=QUEUED)


# Coalescing

def test_a_forced_request_is_never_coalesced_into_a_plain_queued_job():
    mgr = _fresh_manager()
    mgr.jobs["plain"] = _queued_job("plain", force=False)
    assert mgr.existing_queued(ANALYSIS, "c1", None, force=True) is None


def test_a_forced_request_is_never_coalesced_even_into_another_forced_job():
    """Each force click gets its own job -- still correct (a queued force
    job already covers everything), but the important guarantee is that
    force never silently merges into something narrower. Merging into an
    equally-forced job is not incorrect, just not required either way; the
    method's contract is "force never returns a match", full stop, which
    is what makes it impossible to ever accidentally downgrade a forced
    request."""
    mgr = _fresh_manager()
    mgr.jobs["forced"] = _queued_job("forced", force=True)
    assert mgr.existing_queued(ANALYSIS, "c1", None, force=True) is None


def test_a_plain_request_still_coalesces_into_another_plain_queued_job():
    """The original bug this coalescing fixed (200 Validate clicks -> 200
    jobs) must stay fixed for the common, non-forced path."""
    mgr = _fresh_manager()
    mgr.jobs["plain"] = _queued_job("plain", force=False)
    assert mgr.existing_queued(ANALYSIS, "c1", None, force=False) is mgr.jobs["plain"]


def test_a_plain_request_may_coalesce_into_an_already_queued_forced_job():
    """A full re-analysis is a strict superset of "whatever's still owed" --
    reusing it for a plain request does at least as much work as required,
    never less."""
    mgr = _fresh_manager()
    mgr.jobs["forced"] = _queued_job("forced", force=True)
    assert mgr.existing_queued(ANALYSIS, "c1", None, force=False) is mgr.jobs["forced"]


@pytest.mark.asyncio
async def test_create_never_hands_a_forced_caller_someone_elses_queued_job(monkeypatch):
    """End-to-end through create(): a forced call must get a NEW job id,
    never an existing queued one -- regardless of what create() has decided
    to do with the task/process machinery."""
    import asyncio

    mgr = _fresh_manager()
    mgr.jobs["plain"] = _queued_job("plain", force=False)

    # create() spawns a real asyncio task via self._guard -- stub it out to
    # something that returns instantly, so this stays a pure unit test of
    # the coalescing decision rather than exercising process spawning
    async def _noop_guard(job):
        return None

    monkeypatch.setattr(mgr, "_guard", _noop_guard)

    job = mgr.create(ANALYSIS, "c1", {"force": True}, platform=None)
    try:
        assert job.id != "plain"
        assert job.params["force"] is True
    finally:
        job.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job.task


# DTO default

def test_analysis_dto_defaults_force_to_false():
    from backend.dto.analysis_dto import AnalysisIn

    assert AnalysisIn(client_id="c1").force is False
    assert AnalysisIn(client_id="c1", force=True).force is True


# Run_analysis actually widens the query

@pytest.mark.asyncio
async def test_run_analysis_excludes_analysed_when_not_forced(monkeypatch):
    from backend.services import analysis_service as svc
    from backend.services.job_service import Job

    captured = {}

    async def fake_urls_for(client_id, platform, status, *, exclude_analysed=False, with_keywords=False):
        captured["exclude_analysed"] = exclude_analysed
        return []  # short-circuits to the "nothing to analyse" branch

    monkeypatch.setattr(svc.profiles_db, "urls_for", fake_urls_for)

    job = Job(id="j1", kind="analysis", client_id="c1", platform="facebook", params={})
    await svc.run_analysis(job)
    assert captured["exclude_analysed"] is True
    assert "already up to date" in job.message


@pytest.mark.asyncio
async def test_run_analysis_includes_already_analysed_when_forced(monkeypatch):
    from backend.services import analysis_service as svc
    from backend.services.job_service import Job

    captured = {}

    async def fake_urls_for(client_id, platform, status, *, exclude_analysed=False, with_keywords=False):
        captured["exclude_analysed"] = exclude_analysed
        return []

    monkeypatch.setattr(svc.profiles_db, "urls_for", fake_urls_for)

    job = Job(id="j2", kind="analysis", client_id="c1", platform="facebook", params={"force": True})
    await svc.run_analysis(job)
    assert captured["exclude_analysed"] is False
    # the message must not claim the client is "already up to date" when a
    # forced run simply found no approved profiles at all -- those are two
    # different facts
    assert "already up to date" not in job.message
    assert "no approved profiles" in job.message
