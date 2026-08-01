"""Unit tests for JobManager's bounded in-memory job history.

Jobs are constructed directly and inserted into `manager.jobs` rather than
via `create()`, which would spawn a real child process -- eviction itself
is pure dict/list logic, no I/O, so it doesn't need one.
"""

from __future__ import annotations

from backend.services import job_service as job_manager_module
from backend.services.job_service import DONE, QUEUED, RUNNING, Job, JobManager


def _fresh_manager() -> JobManager:
    mgr = JobManager()
    mgr._init()  # reset the process-wide singleton to a clean slate for this test
    return mgr


def _job(id_: str, status: str, finished: str = "") -> Job:
    return Job(
        id=id_, kind="discovery", client_id="c1", platform="facebook",
        params={}, status=status, finished=finished,
    )


def test_no_eviction_under_the_cap():
    mgr = _fresh_manager()
    for i in range(5):
        mgr.jobs[str(i)] = _job(str(i), DONE, finished=f"2026-01-{i+1:02d}")
    mgr._evict_old_jobs()
    assert len(mgr.jobs) == 5


def test_evicts_oldest_terminal_jobs_over_the_cap():
    mgr = _fresh_manager()
    job_manager_module.MAX_JOBS_IN_MEMORY = 3
    try:
        for i in range(5):
            mgr.jobs[str(i)] = _job(str(i), DONE, finished=f"2026-01-{i+1:02d}")
        mgr._evict_old_jobs()
        assert len(mgr.jobs) == 3
        # the three most recently finished survive
        assert set(mgr.jobs) == {"2", "3", "4"}
    finally:
        job_manager_module.MAX_JOBS_IN_MEMORY = 2000


def test_never_evicts_running_or_queued_jobs():
    mgr = _fresh_manager()
    job_manager_module.MAX_JOBS_IN_MEMORY = 3
    try:
        mgr.jobs["running"] = _job("running", RUNNING)
        mgr.jobs["queued"] = _job("queued", QUEUED)
        mgr.jobs["done-old"] = _job("done-old", DONE, finished="2026-01-01")
        mgr.jobs["done-new"] = _job("done-new", DONE, finished="2026-01-02")
        mgr._evict_old_jobs()  # 4 jobs, cap 3 -> 1 must go; only 2 are eligible
        # both non-terminal jobs must survive
        assert "running" in mgr.jobs
        assert "queued" in mgr.jobs
        # the oldest terminal job is the one evicted
        assert "done-old" not in mgr.jobs
        assert "done-new" in mgr.jobs
    finally:
        job_manager_module.MAX_JOBS_IN_MEMORY = 2000


def test_running_and_queued_never_evicted_even_when_they_alone_exceed_cap():
    """If every terminal job is already gone, further overflow just has to
    be tolerated -- eviction must never touch a job that's actually doing
    something."""
    mgr = _fresh_manager()
    job_manager_module.MAX_JOBS_IN_MEMORY = 1
    try:
        mgr.jobs["running"] = _job("running", RUNNING)
        mgr.jobs["queued"] = _job("queued", QUEUED)
        mgr._evict_old_jobs()
        assert set(mgr.jobs) == {"running", "queued"}
    finally:
        job_manager_module.MAX_JOBS_IN_MEMORY = 2000
