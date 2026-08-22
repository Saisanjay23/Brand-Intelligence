"""`analysis_attempts` bounds SWEEPS, not the re-reads inside one sweep.

THE DEFECT THIS GUARDS
    Analysis re-visits an incomplete profile up to `_COMPLETENESS_PASSES`
    more times within a single job, and every one of those visits saves.
    Each save counted against `analysis_attempts`, so one job spent THREE
    of a budget of four (MAX_ANALYSIS_ATTEMPTS).

    Measured live on 2026-08-23, before this file existed:

        facebook   18 incomplete rows, 18 frozen, every one at exactly 6
        instagram  27 incomplete rows, 27 frozen
        twitter    19 incomplete rows, 19 frozen
        telegram   11 incomplete rows, 11 frozen, every one at exactly 6
        youtube     1 incomplete row,   1 frozen

    76 of 76 incomplete rows across every platform were over the cap, so
    `urls_for` excluded all of them and no later sweep could ever pick one
    up again. The recurring 6 is the signature: 3 passes by 2 jobs.

    The in-job passes are bounded by `_COMPLETENESS_PASSES` already. This
    counter exists to stop a permanently-dead URL being retried by sweep
    after sweep forever, so only a job's FINAL word on a URL may spend it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.database.repositories import profile_repository as profiles_db


class _Coll:
    """Captures the update document `save` builds, without a database."""

    def __init__(self, existing):
        self._existing = existing
        self.updates: list[dict] = []

    async def find_one(self, *_a, **_k):
        return self._existing

    async def update_one(self, _filter, update, upsert=False):
        self.updates.append(update)
        return SimpleNamespace(upserted_id=None)


def _fake_db(coll: _Coll):
    return lambda: {profiles_db.PROFILES: coll}


async def _save(fields: dict, *, retry_pending: bool) -> dict:
    """Run a real `save` against the fake and return the update document."""
    coll = _Coll({"_id": "row1", "entity_id": "", "status": "pending"})
    with patch.object(profiles_db, "db", _fake_db(coll)):
        await profiles_db.save(
            "c1", "facebook", profiles_db.PHASE_ANALYSIS, fields,
            url="https://facebook.com/someone", retry_pending=retry_pending,
        )
    assert len(coll.updates) == 1
    return coll.updates[0]


INCOMPLETE = {"analysis_status": "OK", "analysis_complete": False, "profile_name": "Someone"}
COMPLETE = {"analysis_status": "OK", "analysis_complete": True, "profile_name": "Someone"}
ERRORED = {"analysis_status": "ERROR", "analysis_complete": False}


def _attempt_delta(update: dict):
    """+1 when the update spends an attempt, 0 when it resets, None when it
    leaves the stored counter alone."""
    if "analysis_attempts" in update.get("$set", {}):
        return 0
    return update.get("$inc", {}).get("analysis_attempts")


class TestAnInJobRePassIsFree:
    @pytest.mark.asyncio
    async def test_an_incomplete_row_queued_for_another_pass_spends_nothing(self):
        assert _attempt_delta(await _save(INCOMPLETE, retry_pending=True)) is None

    @pytest.mark.asyncio
    async def test_an_errored_row_queued_for_another_pass_spends_nothing(self):
        """ERROR rows are re-queued by the same completeness check (an error
        row is missing every field), so they were double-spending too."""
        assert _attempt_delta(await _save(ERRORED, retry_pending=True)) is None

    @pytest.mark.asyncio
    async def test_a_provisional_save_never_resets_the_counter_either(self):
        """Skipping the increment must not tip over into clearing what
        earlier sweeps legitimately spent."""
        update = await _save(INCOMPLETE, retry_pending=True)
        assert "analysis_attempts" not in update.get("$set", {})


class TestTheJobsFinalWordStillSpends:
    @pytest.mark.asyncio
    async def test_an_incomplete_row_with_no_pass_left_spends_one(self):
        assert _attempt_delta(await _save(INCOMPLETE, retry_pending=False)) == 1

    @pytest.mark.asyncio
    async def test_an_errored_row_with_no_pass_left_spends_one(self):
        assert _attempt_delta(await _save(ERRORED, retry_pending=False)) == 1

    @pytest.mark.asyncio
    async def test_one_job_can_never_spend_more_than_one(self):
        """The whole point, expressed as the arithmetic that was wrong: a
        job makes 1 + _COMPLETENESS_PASSES visits, and only the last one is
        final, so the durable cost of a job is exactly 1."""
        from backend.services.analysis_service import _COMPLETENESS_PASSES

        visits = [True] * _COMPLETENESS_PASSES + [False]
        spent = 0
        for retry_pending in visits:
            spent += _attempt_delta(await _save(INCOMPLETE, retry_pending=retry_pending)) or 0
        assert len(visits) == _COMPLETENESS_PASSES + 1
        assert spent == 1

    @pytest.mark.asyncio
    async def test_the_budget_survives_long_enough_to_matter(self):
        """A job costing 1 means the cap really is a number of sweeps."""
        assert profiles_db.MAX_ANALYSIS_ATTEMPTS >= 2


class TestASuccessfulReadStillResets:
    @pytest.mark.asyncio
    async def test_a_complete_reading_clears_the_counter(self):
        assert _attempt_delta(await _save(COMPLETE, retry_pending=False)) == 0

    @pytest.mark.asyncio
    async def test_a_complete_reading_clears_it_even_mid_job(self):
        """`retry_pending` is only ever True for a row that came back
        short, but a complete row must reset regardless -- the flag
        suppresses spending, never the recovery."""
        assert _attempt_delta(await _save(COMPLETE, retry_pending=True)) == 0


class TestTheDefaultIsUnchangedBehaviour:
    @pytest.mark.asyncio
    async def test_callers_that_do_not_pass_the_flag_still_spend(self):
        """Every pre-existing caller (the standalone engine sink, manual
        re-analysis) omits it and must behave exactly as before."""
        coll = _Coll({"_id": "row1", "entity_id": "", "status": "pending"})
        with patch.object(profiles_db, "db", _fake_db(coll)):
            await profiles_db.save("c1", "facebook", profiles_db.PHASE_ANALYSIS,
                                   INCOMPLETE, url="https://facebook.com/x")
        assert _attempt_delta(coll.updates[0]) == 1


class TestSaveManyRoutesTheFlag:
    @pytest.mark.asyncio
    async def test_the_control_key_reaches_save_and_not_the_document(self):
        """It rides on the item like url/entity_id/keyword, so it must be
        popped -- a `retry_pending` field landing in Mongo would be a
        silent schema leak."""
        seen = {}

        async def fake_save(_c, _p, _ph, fields, **kw):
            seen["fields"] = fields
            seen["retry_pending"] = kw.get("retry_pending")
            return False

        with patch.object(profiles_db, "save", fake_save):
            await profiles_db.save_many("c1", "facebook", "analysis", [
                {**INCOMPLETE, "url": "https://facebook.com/x", "retry_pending": True},
            ])
        assert seen["retry_pending"] is True
        assert "retry_pending" not in seen["fields"]

    @pytest.mark.asyncio
    async def test_an_item_without_the_key_defaults_to_spending(self):
        seen = {}

        async def fake_save(_c, _p, _ph, _fields, **kw):
            seen["retry_pending"] = kw.get("retry_pending")
            return False

        with patch.object(profiles_db, "save", fake_save):
            await profiles_db.save_many("c1", "facebook", "analysis", [
                {**INCOMPLETE, "url": "https://facebook.com/x"},
            ])
        assert seen["retry_pending"] is False
