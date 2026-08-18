"""The admin-controlled front of the round-robin queue.

`_rotation` is rebuilt from Mongo once per lap, so anything written into it
is erased at the next lap boundary. `_priority` is a separate list only an
admin adds to or removes from, drained ahead of the rotation -- that is
what makes "run this client next" mean something more than a suggestion
that quietly disappears a minute later.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import round_robin_service as rr


@pytest.fixture(autouse=True)
def _clean_queue():
    rr._priority.clear()
    rr._rotation.clear()
    rr._cursor = 0
    yield
    rr._priority.clear()
    rr._rotation.clear()
    rr._cursor = 0


def _schedulable(**overrides):
    client = {"client_id": "x", "name_keywords": ["acme"], "scheduler_enabled": True}
    client.update(overrides)
    return AsyncMock(return_value=client)


class TestQueueOrdering:
    def test_run_next_goes_to_the_front(self):
        rr.enqueue("a")
        rr.enqueue("b")
        assert rr.run_next("b") == ["b", "a"]

    def test_run_next_never_duplicates_a_client(self):
        rr.enqueue("a")
        rr.run_next("a")
        rr.run_next("a")
        assert rr.priority_queue() == ["a"]

    def test_enqueue_appends_behind_what_is_already_waiting(self):
        rr.enqueue("a")
        rr.enqueue("b")
        assert rr.priority_queue() == ["a", "b"]

    def test_move_swaps_neighbours(self):
        for c in "abc":
            rr.enqueue(c)
        assert rr.move("c", "up") == ["a", "c", "b"]
        assert rr.move("a", "down") == ["c", "a", "b"]

    def test_move_at_either_end_is_a_no_op_not_an_error(self):
        # the UI shows the arrows on every row; clicking the one at the top
        # should do nothing rather than fail
        rr.enqueue("a")
        rr.enqueue("b")
        assert rr.move("a", "up") == ["a", "b"]
        assert rr.move("b", "down") == ["a", "b"]

    def test_move_of_an_unqueued_client_raises(self):
        with pytest.raises(ValueError):
            rr.move("ghost", "up")

    def test_dequeue_removes_and_is_forgiving(self):
        rr.enqueue("a")
        assert rr.dequeue("a") == []
        assert rr.dequeue("a") == []  # already gone, still fine


class TestQueueIsServedFirst:
    @pytest.mark.asyncio
    async def test_priority_beats_the_rotation(self):
        rr._rotation[:] = ["rot-1", "rot-2"]
        rr.enqueue("urgent")
        with patch("backend.database.repositories.client_repository.try_get", new=_schedulable()):
            assert await rr._next_client_id() == "urgent"
        # and it is consumed, not served forever
        assert rr.priority_queue() == []

    @pytest.mark.asyncio
    async def test_a_queued_client_that_became_unschedulable_is_skipped(self):
        """Checked when it is pulled off the queue, not when it went on:
        a client can be deleted, emptied or paused in between."""
        rr._rotation[:] = ["rot-1"]
        rr.enqueue("gone")
        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value=None)):
            assert await rr._next_client_id() == "rot-1"

    @pytest.mark.asyncio
    async def test_a_paused_client_is_skipped_even_if_queued(self):
        rr._rotation[:] = ["rot-1"]
        rr.enqueue("parked")
        with patch("backend.database.repositories.client_repository.try_get",
                   new=_schedulable(scheduler_enabled=False)):
            assert await rr._next_client_id() == "rot-1"


class TestRotationRespectsTheSkipFlag:
    @pytest.mark.asyncio
    async def test_disabled_clients_never_enter_the_rotation(self):
        clients = [
            {"client_id": "on", "name_keywords": ["a"], "scheduler_enabled": True},
            {"client_id": "off", "name_keywords": ["b"], "scheduler_enabled": False},
            # saved before the flag existed -- must keep running
            {"client_id": "legacy", "name_keywords": ["c"]},
        ]
        with patch("backend.database.repositories.client_repository.list_all",
                   new=AsyncMock(return_value=clients)):
            first = await rr._next_client_id()
        assert "off" not in rr._rotation
        assert sorted(rr._rotation) == ["legacy", "on"]
        assert first in ("on", "legacy")

    @pytest.mark.asyncio
    async def test_a_client_with_no_keywords_is_still_excluded(self):
        clients = [{"client_id": "empty", "scheduler_enabled": True}]
        with patch("backend.database.repositories.client_repository.list_all",
                   new=AsyncMock(return_value=clients)):
            assert await rr._next_client_id() is None


class TestUpcoming:
    def test_queue_entries_come_first_and_are_labelled(self):
        rr._rotation[:] = ["r1", "r2"]
        rr.enqueue("q1")
        out = rr.upcoming(limit=5)
        assert [e["client_id"] for e in out] == ["q1", "r1", "r2"]
        assert out[0]["source"] == "queue"
        assert out[1]["source"] == "rotation"

    def test_a_queued_client_is_not_listed_twice(self):
        rr._rotation[:] = ["dup", "other"]
        rr.enqueue("dup")
        out = rr.upcoming(limit=5)
        assert [e["client_id"] for e in out] == ["dup", "other"]

    def test_respects_the_limit(self):
        rr._rotation[:] = [f"c{i}" for i in range(50)]
        assert len(rr.upcoming(limit=7)) == 7

    def test_starts_from_the_cursor_not_the_top_of_the_lap(self):
        rr._rotation[:] = ["a", "b", "c", "d"]
        rr._cursor = 2
        assert [e["client_id"] for e in rr.upcoming(limit=4)] == ["c", "d", "a", "b"]


class TestQueueControllerGuards:
    @pytest.mark.asyncio
    async def test_queueing_a_keywordless_client_is_refused(self):
        from backend.controllers import scheduler_controller
        from backend.shared.errors import ConflictError

        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value={"client_id": "x"})):
            with pytest.raises(ConflictError):
                await scheduler_controller.enqueue("x", front=True)
        assert rr.priority_queue() == []

    @pytest.mark.asyncio
    async def test_queueing_a_paused_client_is_refused(self):
        from backend.controllers import scheduler_controller
        from backend.shared.errors import ConflictError

        client = {"client_id": "x", "name_keywords": ["a"], "scheduler_enabled": False}
        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value=client)):
            with pytest.raises(ConflictError):
                await scheduler_controller.enqueue("x", front=True)

    @pytest.mark.asyncio
    async def test_pausing_a_client_drops_its_queue_entry(self):
        """Otherwise the panel shows a client sitting in the queue that
        _next_client_id then skips over -- which reads as a broken queue."""
        from backend.controllers import scheduler_controller

        rr.enqueue("x")
        with patch("backend.database.repositories.client_repository.try_get",
                   new=_schedulable()), \
             patch("backend.database.repositories.client_repository.set_scheduler_enabled",
                   new=AsyncMock(return_value=False)):
            await scheduler_controller.set_client_enabled("x", False)
        assert rr.priority_queue() == []

    @pytest.mark.asyncio
    async def test_unknown_client_is_a_404(self):
        from backend.controllers import scheduler_controller
        from backend.shared.errors import NotFoundError

        with patch("backend.database.repositories.client_repository.try_get",
                   new=AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await scheduler_controller.enqueue("ghost", front=True)
