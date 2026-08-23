"""A half-swept client must say WHICH platform still owes work, and get it.

THE GAP THIS CLOSES
    A client turn produced one aggregate word -- "success" or "failed" --
    and that word cannot express the most common partial outcome there is:
    Instagram and X finished, Facebook lost its session halfway. Calling
    that a success hides a real coverage gap; calling it a failure implies
    the whole turn was wasted. Either way the only fact worth keeping --
    which platform still owes this client work -- was thrown away, and the
    client then waited out the full DISCOVERY_INTERVAL_HOURS before anyone
    looked at it again.

WHY "interrupted" IS NOT "failed"
    An interrupted platform lost its SESSION. Nothing about the keywords,
    the parsers or the client is wrong, and re-running it against a healthy
    session simply finishes it. A failed platform broke for a reason a
    fresh session will not change, so re-running it early would spend
    session budget reproducing the same error. Only the first is resumed.
"""

from __future__ import annotations

import pytest

from backend.services import round_robin_service as rr


def _client(**kw):
    base = {"client_id": "c1", "name_keywords": ["acme"], "scheduler_enabled": True}
    base.update(kw)
    return base


class TestReadingTheBreakdown:
    def test_interrupted_platforms_are_unfinished(self):
        c = _client(last_run_platforms={"facebook": "interrupted", "twitter": "done"})
        assert rr._unfinished_platforms(c) == {"facebook"}

    def test_a_platform_skipped_for_a_dead_session_is_unfinished_too(self):
        """It never started, so the client is just as half-swept."""
        c = _client(last_run_platforms={"facebook": "skipped", "twitter": "done"})
        assert rr._unfinished_platforms(c) == {"facebook"}

    def test_a_failed_platform_is_NOT_resumed(self):
        """It will fail the same way; retrying early only burns budget."""
        c = _client(last_run_platforms={"facebook": "failed", "twitter": "done"})
        assert rr._unfinished_platforms(c) == set()

    @pytest.mark.parametrize("state", ["done", "partial"])
    def test_completed_platforms_are_not_unfinished(self, state):
        assert rr._unfinished_platforms(_client(last_run_platforms={"x": state})) == set()

    def test_a_client_that_never_ran_claims_nothing(self):
        """Prioritisation must rest on positive evidence, never on absent
        data -- otherwise every new client jumps the queue forever."""
        assert rr._unfinished_platforms(_client()) == set()

    def test_a_malformed_record_is_ignored_rather_than_raising(self):
        assert rr._unfinished_platforms(_client(last_run_platforms="nonsense")) == set()


class TestOnlyResumeWhatCanActuallyRun:
    """The loop this guards: a client whose Facebook session is dead and
    stays dead would otherwise be prioritised, skip Facebook, record
    "skipped", and be prioritised again -- starving every other client
    while accomplishing nothing."""

    C = _client(last_run_platforms={"facebook": "interrupted", "twitter": "done"})

    def test_it_resumes_when_the_platform_is_usable_again(self):
        assert rr._resumable_platforms(self.C, ["facebook", "twitter"]) == ["facebook"]

    def test_it_does_not_resume_while_the_platform_is_still_down(self):
        assert rr._resumable_platforms(self.C, ["twitter"]) == []

    def test_nothing_ready_means_nothing_to_resume(self):
        assert rr._resumable_platforms(self.C, []) == []

    def test_only_the_unfinished_platform_is_returned(self):
        """The resume run must not re-sweep platforms that already
        finished -- that is a second full session budget spent to
        rediscover profiles already stored."""
        assert "twitter" not in rr._resumable_platforms(self.C, ["facebook", "twitter"])


class TestUnfinishedWorkBeatsTheInterval:
    def test_a_half_swept_client_is_due_immediately(self):
        """The interval exists to stop re-sweeping work that is DONE. A
        known gap is exactly what it should not delay."""
        from datetime import datetime, timezone

        c = _client(last_run_at=datetime.now(timezone.utc),
                    last_run_platforms={"facebook": "interrupted"})
        assert rr._due_for_discovery(c) is True

    def test_a_fully_swept_client_still_waits_its_turn(self):
        from datetime import datetime, timezone

        c = _client(last_run_at=datetime.now(timezone.utc),
                    last_run_platforms={"facebook": "done", "twitter": "done"})
        assert rr._due_for_discovery(c) is False

    def test_a_client_that_never_ran_is_still_due(self):
        assert rr._due_for_discovery(_client()) is True


class TestTheStatesAreTheOnesTheEngineEmits:
    def test_unfinished_states_are_exactly_interrupted_and_skipped(self):
        assert set(rr.UNFINISHED_PLATFORM_STATES) == {"interrupted", "skipped"}

    def test_discovery_emits_interrupted_for_a_session_failure(self):
        """Guards the coupling: discovery_service classifies a session-shaped
        exception as `interrupted`, and this module keys off that exact
        word. If either side is renamed, resumes silently stop happening."""
        import inspect

        from backend.services import discovery_service

        src = inspect.getsource(discovery_service._sweep_platform.__module__ and
                                discovery_service)
        assert 'platform_status=status' in src
        assert '"interrupted" if reason else "failed"' in src
