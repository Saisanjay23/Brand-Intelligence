"""JobManager._guard closes its IPC queue when a job ends -- an unclosed
multiprocessing.Queue leaves its background feeder thread (and the OS
pipe/handle backing it) running for the life of the process, one leaked per
job. On Windows enough of those is what turned into the spawn-failure
storms round_robin_service.py's own backoff exists to survive.

No real subprocess is spawned here -- multiprocessing.Queue/Process are
mocked so this test is fast and doesn't depend on the platform's actual
spawn mechanism; only the cleanup contract on the mock queue is asserted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.job_service import Job, JobManager


def _fake_queue(messages: list[dict]) -> MagicMock:
    """A queue.get() that hands back each message in turn, matching what
    _guard's _get_next() polling loop expects."""
    q = MagicMock()
    q.get.side_effect = list(messages)
    return q


@pytest.mark.asyncio
async def test_ipc_queue_is_closed_and_joined_after_a_job_completes():
    job = Job(id="j1", kind="discovery", client_id="c1", platform="facebook", params={})
    ipc_queue = _fake_queue([{"type": "__done__", "message": "ok"}])

    fake_proc = MagicMock()
    fake_proc.is_alive.return_value = False

    with patch("backend.services.job_service.multiprocessing.Queue", return_value=ipc_queue), \
         patch("backend.services.job_service.multiprocessing.Process", return_value=fake_proc):
        await JobManager()._guard(job)

    assert job.status == "done"
    ipc_queue.close.assert_called_once()
    ipc_queue.join_thread.assert_called_once()


@pytest.mark.asyncio
async def test_ipc_queue_is_still_closed_when_the_job_fails():
    job = Job(id="j2", kind="analysis", client_id="c1", platform="facebook", params={})
    ipc_queue = _fake_queue([{"type": "__failed__", "error": "boom", "error_type": "RuntimeError"}])

    fake_proc = MagicMock()
    fake_proc.is_alive.return_value = False

    with patch("backend.services.job_service.multiprocessing.Queue", return_value=ipc_queue), \
         patch("backend.services.job_service.multiprocessing.Process", return_value=fake_proc), \
         patch("backend.services.incident_service.record", new_callable=AsyncMock):
        await JobManager()._guard(job)

    assert job.status == "failed"
    ipc_queue.close.assert_called_once()
    ipc_queue.join_thread.assert_called_once()


@pytest.mark.asyncio
async def test_a_broken_close_never_masks_the_jobs_own_outcome():
    """close()/join_thread() are best-effort cleanup, not part of the job's
    own success/failure contract -- a queue that errors on close() must not
    turn an otherwise-successful job into a crash the caller has to handle."""
    job = Job(id="j3", kind="discovery", client_id="c1", platform="facebook", params={})
    ipc_queue = _fake_queue([{"type": "__done__", "message": "ok"}])
    ipc_queue.close.side_effect = OSError("already closed")

    fake_proc = MagicMock()
    fake_proc.is_alive.return_value = False

    with patch("backend.services.job_service.multiprocessing.Queue", return_value=ipc_queue), \
         patch("backend.services.job_service.multiprocessing.Process", return_value=fake_proc):
        await JobManager()._guard(job)  # must not raise

    assert job.status == "done"
