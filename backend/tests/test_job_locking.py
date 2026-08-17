"""Job locks name the RESOURCE being contended -- a platform's session
pool -- not the shape of the job.

The old scheme keyed on (platform, kind), which meant a discovery sweep, an
approve-triggered analysis, and the catch-up analysis all held DIFFERENT
locks and could therefore drive the same cookie session through three
Playwright contexts at once. Beyond the ban risk, the two analysis jobs each
read their URL list at their own start, so they would visit an identical
list concurrently.
"""

from __future__ import annotations

from backend.services.job_service import ANALYSIS, DISCOVERY, Job, JobManager


def _job(kind: str, platform=None, client_id: str = "c1") -> Job:
    return Job(id=f"{kind}-{platform}", kind=kind, client_id=client_id, platform=platform, params={})


def _keys(job: Job) -> set:
    return set(JobManager()._lock_keys(job))


def test_full_sweep_conflicts_with_a_scoped_analysis_on_the_same_platform():
    """THE bug this replaces: these two used to be able to run at once
    against one Facebook session."""
    sweep = _job(DISCOVERY, None)
    scoped = _job(ANALYSIS, "facebook")
    assert _keys(sweep) & _keys(scoped)


def test_catchup_conflicts_with_a_scoped_analysis():
    """Both read approved-and-unanalysed at their own start, so running
    concurrently means scraping the same profiles twice."""
    catchup = _job(ANALYSIS, None)
    scoped = _job(ANALYSIS, "facebook")
    assert _keys(catchup) & _keys(scoped)


def test_two_full_sweeps_conflict():
    assert _keys(_job(DISCOVERY, None, "c1")) & _keys(_job(DISCOVERY, None, "c2"))


def test_different_cookie_platforms_do_not_conflict():
    """Serialising more than necessary would be its own bug -- Facebook and
    Instagram have separate pools and must run side by side."""
    assert not (_keys(_job(ANALYSIS, "facebook")) & _keys(_job(ANALYSIS, "instagram")))


def test_key_authed_platform_still_splits_by_kind():
    """YouTube authenticates with an API key -- there is no browser session
    to collide over, so discovery and analysis there stay parallel."""
    assert not (_keys(_job(DISCOVERY, "youtube")) & _keys(_job(ANALYSIS, "youtube")))


def test_cookie_platform_does_not_split_by_kind():
    """Facebook has exactly one session pool; kind is irrelevant to it."""
    assert _keys(_job(DISCOVERY, "facebook")) & _keys(_job(ANALYSIS, "facebook"))


def test_multi_platform_keys_are_sorted():
    """Sorted acquisition order is what makes deadlock between two
    multi-platform jobs impossible -- both always take the same lock first."""
    keys = JobManager()._lock_keys(_job(DISCOVERY, None))
    assert keys == sorted(keys, key=repr)
    assert len(keys) > 1


def test_a_full_sweep_covers_every_enabled_platform():
    from backend.platforms import registry

    keys = _keys(_job(DISCOVERY, None))
    for platform_id, plat in registry.PLATFORMS.items():
        if not plat.enabled:
            continue
        scoped = _keys(_job(DISCOVERY, platform_id))
        assert scoped & keys, f"{platform_id} not covered by a full sweep's locks"
