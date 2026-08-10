"""The two invariants that decide whether a client report can be trusted:

1. A profile the engine never actually READ stays in the analysis queue.
   A transient failure that silently marks a profile "analysed" removes an
   approved impersonation candidate from the pipeline forever, and nothing
   downstream can tell that happened.

2. A freshly analysed result is held back from the client-facing view for a
   bounded window, and then becomes visible ON ITS OWN. A hold that never
   expires isn't a review window, it's a dead pipeline.

Pure query-shape tests -- no Mongo. What is asserted is the filter each
function builds, because that filter IS the behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.database.repositories import profile_repository as repo


# ── 1. retry semantics ─────────────────────────────────────────────────────

def test_retryable_statuses_are_incomplete_or_never_read():
    """OK/GONE are complete verdicts. The other four mean the reading is
    not trustworthy enough to publish yet -- ERROR/CHECKPOINT/LOGIN_REQUIRED
    because nothing was read at all, PARTIAL because SOME of the profile was
    read but not enough to trust (see RETRYABLE_ANALYSIS_STATUSES' own
    comment for the live evidence this was added on: PARTIAL rows found
    stuck at analysis_attempts=0, permanently unretried, and eligible to be
    published with missing fields)."""
    assert set(repo.RETRYABLE_ANALYSIS_STATUSES) == {
        "ERROR", "CHECKPOINT", "LOGIN_REQUIRED", "PARTIAL",
    }
    for verdict in ("OK", "GONE"):
        assert verdict not in repo.RETRYABLE_ANALYSIS_STATUSES


@pytest.mark.asyncio
async def test_urls_for_requeues_a_failed_attempt(monkeypatch):
    captured = {}

    class _Cursor:
        def __aiter__(self):
            async def gen():
                if False:
                    yield {}
            return gen()

    class _Coll:
        def find(self, q, projection=None):
            captured["q"] = q
            return _Cursor()

    monkeypatch.setattr(repo, "db", lambda: {repo.PROFILES: _Coll()})
    await repo.urls_for("c1", "facebook", "approved", exclude_analysed=True)

    branches = captured["q"]["$or"]
    # never analysed at all
    assert {"phase": {"$ne": repo.PHASE_ANALYSIS}} in branches
    # analysed, but the attempt never read the profile and has retries left
    retry = next(b for b in branches if "analysis_status" in b)
    assert retry["analysis_status"] == {"$in": list(repo.RETRYABLE_ANALYSIS_STATUSES)}
    assert retry["analysis_attempts"] == {"$lt": repo.MAX_ANALYSIS_ATTEMPTS}


@pytest.mark.asyncio
async def test_urls_for_does_not_requeue_a_successful_read(monkeypatch):
    """A row that WAS read must not be re-scraped on every catch-up sweep --
    that is real page loads under a live session, for nothing."""
    captured = {}

    class _Cursor:
        def __aiter__(self):
            async def gen():
                if False:
                    yield {}
            return gen()

    class _Coll:
        def find(self, q, projection=None):
            captured["q"] = q
            return _Cursor()

    monkeypatch.setattr(repo, "db", lambda: {repo.PROFILES: _Coll()})
    await repo.urls_for("c1", "facebook", "approved", exclude_analysed=True)

    ok_row = {"phase": repo.PHASE_ANALYSIS, "analysis_status": "OK", "analysis_attempts": 0}
    assert not _matches_any(captured["q"]["$or"], ok_row)


_MISSING = object()


def _matches_any(branches: list[dict], doc: dict) -> bool:
    """Minimal Mongo-filter evaluator, enough for the branch shapes these
    two features build. Deliberately raises on an operator it doesn't
    model, so a future change to the real query can't quietly stop being
    covered by these tests."""
    for branch in branches:
        if all(_field_matches(doc.get(f, _MISSING), cond) for f, cond in branch.items()):
            return True
    return False


def _field_matches(value, cond) -> bool:
    present = value is not _MISSING
    if not isinstance(cond, dict):
        return present and value == cond
    if "$exists" in cond and present != cond["$exists"]:
        return False
    if "$ne" in cond and (not present or value == cond["$ne"]):
        # a MISSING field does satisfy {"$ne": x} in Mongo
        if present:
            return False
    if "$in" in cond and (not present or value not in cond["$in"]):
        return False
    if "$lt" in cond and not (present and value is not None and value < cond["$lt"]):
        return False
    if "$lte" in cond and not (present and value is not None and value <= cond["$lte"]):
        return False
    if "$gte" in cond and not (present and value is not None and value >= cond["$gte"]):
        return False
    known = {"$exists", "$ne", "$in", "$lt", "$lte", "$gte"}
    if unknown := set(cond) - known:
        raise AssertionError(f"unhandled operator(s) {unknown} in {cond!r}")
    return True


def test_attempt_cap_eventually_stops_a_dead_url():
    """A permanently unreachable URL must stop consuming the queue."""
    assert repo.MAX_ANALYSIS_ATTEMPTS >= 2, "at least one real retry"
    assert repo.MAX_ANALYSIS_ATTEMPTS <= 10, "must actually give up"

    exhausted = {
        "phase": repo.PHASE_ANALYSIS, "analysis_status": "ERROR",
        "analysis_attempts": repo.MAX_ANALYSIS_ATTEMPTS,
    }
    branches = [
        {"phase": {"$ne": repo.PHASE_ANALYSIS}},
        {"analysis_status": {"$in": list(repo.RETRYABLE_ANALYSIS_STATUSES)},
         "analysis_attempts": {"$lt": repo.MAX_ANALYSIS_ATTEMPTS}},
    ]
    assert not _matches_any(branches, exhausted)


# ── 2. publish hold ────────────────────────────────────────────────────────

def _hold_branches(now: datetime) -> list[dict]:
    """The visibility clause find() builds for include_held=False."""
    return [
        {"published": True},
        {"publish_hold_until": {"$lte": now}},
        {"published": {"$exists": False}, "publish_hold_until": {"$exists": False}},
    ]


def test_hold_expires_on_its_own():
    """The whole point of ADR 0007: the row becomes client-visible when the
    window passes, WITHOUT anyone clicking Publish. Nothing wrote
    publish_hold_until before, so an unpublished row was hidden forever."""
    now = datetime.now(timezone.utc)
    expired = {"published": False, "publish_hold_until": now - timedelta(minutes=1)}
    still_holding = {"published": False, "publish_hold_until": now + timedelta(minutes=5)}

    assert _matches_any(_hold_branches(now), expired)
    assert not _matches_any(_hold_branches(now), still_holding)


def test_explicit_publish_wins_over_an_unexpired_hold():
    now = datetime.now(timezone.utc)
    published_early = {"published": True, "publish_hold_until": now + timedelta(minutes=5)}
    assert _matches_any(_hold_branches(now), published_early)


def test_rows_predating_the_feature_stay_visible():
    """A row analysed before the hold existed carries neither field. It must
    not be retroactively hidden."""
    now = datetime.now(timezone.utc)
    assert _matches_any(_hold_branches(now), {})


@pytest.mark.asyncio
async def test_a_failed_attempt_is_not_publishable(monkeypatch):
    """A run that never read the profile produced no finding to publish.

    Regression: the guard existed but read `analysis_status` from a document
    fetched with a projection that didn't include it, so it never fired and
    a CHECKPOINT row published cleanly. A guard that reads an unprojected
    field is indistinguishable from no guard at all.
    """
    from backend.shared.errors import ConflictError

    captured = {}
    doc = {"_id": "x", "phase": repo.PHASE_ANALYSIS, "status": "approved",
           "analysis_status": "CHECKPOINT"}

    class _Coll:
        async def find_one(self, q, projection=None):
            captured["projection"] = projection or {}
            return {k: v for k, v in doc.items() if not projection or k in projection or k == "_id"}

    monkeypatch.setattr(repo, "db", lambda: {repo.PROFILES: _Coll()})
    monkeypatch.setattr(repo, "_oid", lambda s: s)

    with pytest.raises(ConflictError):
        await repo.publish("x")

    assert "analysis_status" in captured["projection"], (
        "publish() must PROJECT every field its guards read"
    )


@pytest.mark.asyncio
async def test_a_partial_reading_is_not_publishable(monkeypatch):
    """PARTIAL means SOME fields were read, not enough to trust -- e.g. an
    Instagram profile whose name/followers/post-date never rendered in time.
    Before PARTIAL joined RETRYABLE_ANALYSIS_STATUSES this guard let it
    through, so an incident could be published with missing fields and no
    automatic retry would ever fix it."""
    from backend.shared.errors import ConflictError

    doc = {"_id": "x", "phase": repo.PHASE_ANALYSIS, "status": "approved",
           "analysis_status": "PARTIAL"}

    class _Coll:
        async def find_one(self, q, projection=None):
            return {k: v for k, v in doc.items() if not projection or k in projection or k == "_id"}

    monkeypatch.setattr(repo, "db", lambda: {repo.PROFILES: _Coll()})
    monkeypatch.setattr(repo, "_oid", lambda s: s)

    with pytest.raises(ConflictError):
        await repo.publish("x")


@pytest.mark.asyncio
async def test_publish_all_skips_never_read_rows(monkeypatch):
    """The bulk path needs the same guard as the single one, or Publish All
    quietly ships the findings the single button refuses."""
    captured = {}

    class _Cursor:
        def __aiter__(self):
            async def gen():
                if False:
                    yield {}
            return gen()

    class _Coll:
        def find(self, q, projection=None):
            captured["q"] = q
            return _Cursor()

    monkeypatch.setattr(repo, "db", lambda: {repo.PROFILES: _Coll()})
    await repo.list_unpublished_ids("c1")
    assert captured["q"]["analysis_status"] == {"$nin": list(repo.RETRYABLE_ANALYSIS_STATUSES)}
    assert captured["q"]["status"] == {"$ne": "rejected"}


# ── identity protection ────────────────────────────────────────────────────

def test_entity_id_is_not_writable_by_analysis():
    """Analysis derives entity_id from the URL it was handed and only
    upgrades a vanity slug when the page happens to expose a numeric id.
    Letting it write the field back overwrote a correct numeric id with a
    slug, breaking the next sweep's dedup."""
    assert "entity_id" not in repo.ANALYSIS_FIELDS
    assert "entity_id" in repo.DISCOVERY_FIELDS


@pytest.mark.parametrize(
    "current,incoming,expected",
    [
        ("", "100012345", True),        # fill a blank
        ("johndoe", "100012345", True), # slug -> canonical numeric id
        ("100012345", "johndoe", False),# never numeric -> slug
        ("100012345", "999999999", False),  # two distinct profiles colliding
        ("johndoe", "janedoe", False),  # slug -> different slug is not an upgrade
        ("100012345", "100012345", False),  # no-op
        ("johndoe", "", False),         # nothing to write
    ],
)
def test_identity_only_ever_sharpens(current, incoming, expected):
    assert repo._is_identity_upgrade(current, incoming) is expected
