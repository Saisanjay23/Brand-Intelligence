"""The round-robin engine sweeps DISCOVERY, at most twice a day per client.

Two product rules, both easy to regress silently:

  1. It never starts an analysis job. Analysis is the analyst's decision --
     `profile_service` queues it when a profile is approved. The engine
     used to run an analysis catch-up after every sweep, which meant
     spending session budget on profiles nobody had triaged, on the
     engine's schedule rather than the analyst's.
  2. A client is swept at most MAX_RUNS_PER_DAY times a day. The engine
     used to cycle continuously, so a handful of clients got re-swept
     every few minutes -- far more traffic, and ban risk, than
     impersonation discovery needs. The cap is enforced as a minimum gap
     between runs, which also keeps them evenly spaced rather than
     bursting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import round_robin_service as rr


@pytest.fixture(autouse=True)
def _clean():
    rr._priority.clear()
    rr._rotation.clear()
    rr._cursor = 0
    yield
    rr._priority.clear()
    rr._rotation.clear()
    rr._cursor = 0


def _client(cid="c1", **over):
    c = {"client_id": cid, "name_keywords": ["acme"], "scheduler_enabled": True}
    c.update(over)
    return c


def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


class TestNeverRunsAnalysis:
    @pytest.mark.asyncio
    async def test_a_client_turn_creates_only_a_discovery_job(self):
        from backend.services.job_service import ANALYSIS, DISCOVERY

        created: list[str] = []

        class _Job:
            id = "j"
            status = "done"
            task = None

        def fake_create(kind, client_id, params, **kw):
            created.append(kind)
            return _Job()

        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value=_client())), \
             patch("backend.database.repositories.client_repository.record_run_result",
                   new=AsyncMock()), \
             patch("backend.platforms.registry.ready_platforms",
                   new=AsyncMock(return_value=(["facebook"], {}))), \
             patch.object(rr, "notify_unavailable_platforms", new=AsyncMock()), \
             patch("backend.services.job_service.job_manager.create", side_effect=fake_create):
            await rr._process_client(0, "c1")

        assert created == [DISCOVERY], f"expected discovery only, got {created}"
        assert ANALYSIS not in created

    @pytest.mark.asyncio
    async def test_a_client_with_an_approved_backlog_still_gets_no_analysis(self):
        """The old engine checked for approved-and-unanalysed profiles and
        queued analysis when it found any. That check is gone entirely --
        a backlog is the analyst's to clear, via approval."""
        from backend.services.job_service import DISCOVERY

        created: list[str] = []

        class _Job:
            id = "j"
            status = "done"
            task = None

        urls = AsyncMock(return_value=["https://example.com/@someone"])

        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value=_client())), \
             patch("backend.database.repositories.client_repository.record_run_result",
                   new=AsyncMock()), \
             patch("backend.platforms.registry.ready_platforms",
                   new=AsyncMock(return_value=(["facebook"], {}))), \
             patch.object(rr, "notify_unavailable_platforms", new=AsyncMock()), \
             patch("backend.database.repositories.profile_repository.urls_for", new=urls), \
             patch("backend.services.job_service.job_manager.create",
                   side_effect=lambda kind, *a, **k: (created.append(kind), _Job())[1]):
            await rr._process_client(0, "c1")

        assert created == [DISCOVERY]
        urls.assert_not_awaited()  # it must not even look for a backlog


class TestRunCap:
    def test_the_cap_is_twice_a_day(self):
        assert rr.MAX_RUNS_PER_DAY == 2

    def test_the_gap_divides_the_day_by_the_cap(self):
        """Derived, not hard-coded: changing the cap must move the gap, or
        the two would silently drift apart."""
        assert rr.DISCOVERY_INTERVAL_HOURS == 24 / rr.MAX_RUNS_PER_DAY

    def test_a_client_that_never_ran_is_due(self):
        assert rr._due_for_discovery(_client()) is True
        assert rr._due_for_discovery(_client(last_run_at=None)) is True

    def test_a_client_swept_an_hour_ago_is_not_due(self):
        assert rr._due_for_discovery(_client(last_run_at=_ago(hours=1))) is False

    def test_a_client_swept_just_inside_the_window_is_not_due(self):
        recent = _ago(hours=rr.DISCOVERY_INTERVAL_HOURS - 1)
        assert rr._due_for_discovery(_client(last_run_at=recent)) is False

    def test_a_client_swept_just_past_the_window_is_due(self):
        old = _ago(hours=rr.DISCOVERY_INTERVAL_HOURS + 1)
        assert rr._due_for_discovery(_client(last_run_at=old)) is True

    def test_a_naive_mongo_datetime_is_read_as_utc(self):
        """Mongo hands back naive-but-UTC datetimes; treating one as local
        time would shift the window by the machine's offset."""
        naive = _ago(hours=rr.DISCOVERY_INTERVAL_HOURS + 2).replace(tzinfo=None)
        assert rr._due_for_discovery(_client(last_run_at=naive)) is True
        naive_recent = _ago(hours=1).replace(tzinfo=None)
        assert rr._due_for_discovery(_client(last_run_at=naive_recent)) is False

    def test_an_iso_string_is_accepted(self):
        iso = _ago(hours=rr.DISCOVERY_INTERVAL_HOURS + 1).isoformat()
        assert rr._due_for_discovery(_client(last_run_at=iso)) is True

    def test_an_unreadable_value_fails_towards_sweeping(self):
        """The guard's own failure mode must be 'sweeps too often', never
        'silently stops sweeping this client forever'."""
        assert rr._due_for_discovery(_client(last_run_at="not-a-date")) is True
        assert rr._due_for_discovery(_client(last_run_at=12345)) is True

    def test_no_more_than_the_cap_fits_in_a_day(self):
        """Walk a full day minute by minute, starting from a client that
        has never run, applying the same gap rule the rotation applies.
        It should come up for a sweep exactly MAX_RUNS_PER_DAY times."""
        start = datetime.now(timezone.utc) - timedelta(days=1)
        gap = timedelta(hours=rr.DISCOVERY_INTERVAL_HOURS)
        last = None
        runs = 0
        for minute in range(24 * 60):          # [0, 1440) -- one whole day
            at = start + timedelta(minutes=minute)
            if last is None or (at - last) >= gap:
                runs += 1
                last = at
        assert runs == rr.MAX_RUNS_PER_DAY, runs

    def test_the_gap_is_what_stops_a_third_run(self):
        """Sanity-check the mechanism itself, not just the count: with the
        cap already spent, the next sweep is refused until the gap."""
        just_swept = _client(last_run_at=_ago(hours=rr.DISCOVERY_INTERVAL_HOURS - 0.5))
        assert rr._due_for_discovery(just_swept) is False

    @pytest.mark.asyncio
    async def test_the_rotation_only_contains_due_clients(self):
        clients = [
            _client("due-never"),
            _client("due-old", last_run_at=_ago(hours=rr.DISCOVERY_INTERVAL_HOURS + 3)),
            _client("fresh", last_run_at=_ago(hours=2)),
        ]
        with patch("backend.database.repositories.client_repository.list_all",
                   new=AsyncMock(return_value=clients)):
            await rr._next_client_id()
        assert sorted(rr._rotation) == ["due-never", "due-old"]
        assert "fresh" not in rr._rotation

    @pytest.mark.asyncio
    async def test_nothing_due_means_nothing_to_pick_up(self):
        clients = [_client("fresh", last_run_at=_ago(minutes=5))]
        with patch("backend.database.repositories.client_repository.list_all",
                   new=AsyncMock(return_value=clients)):
            assert await rr._next_client_id() is None

    @pytest.mark.asyncio
    async def test_an_admin_queue_jump_ignores_the_daily_guard(self):
        """'Run next' is a direct instruction, not part of the rotation --
        it must work even for a client swept minutes ago."""
        rr.run_next("fresh")
        fresh = _client("fresh", last_run_at=_ago(minutes=5))
        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value=fresh)), \
             patch("backend.database.repositories.client_repository.list_all",
                   new=AsyncMock(return_value=[fresh])):
            assert await rr._next_client_id() == "fresh"
