"""Unit tests for the session pool rotation/quarantine policy -- pure, no
Mongo. This is the algorithm that keeps a 100-session pool from being
hammered through session #1 until it's banned; see sessions/manager.py.
"""

from __future__ import annotations

from backend.sessions.manager import (_is_available,
                                         _pick_least_recently_used,
                                         pool_summary_of)

NOW = 1_000_000.0


def _item(id_, status="ready", rate_limited_until=0.0, last_used=0.0) -> dict:
    return {"platform": "facebook", "id": id_, "identifier": id_, "status": status,
            "rate_limited_until": rate_limited_until, "last_used": last_used}


def test_dead_states_are_never_available():
    for status in ("expired", "checkpointed", "unreadable"):
        assert _is_available(_item("a", status=status), NOW) is False


def test_rate_limited_in_the_future_is_unavailable():
    item = _item("a", rate_limited_until=NOW + 100)
    assert _is_available(item, NOW) is False


def test_rate_limit_cooldown_already_served_is_available_again():
    item = _item("a", rate_limited_until=NOW - 1)
    assert _is_available(item, NOW) is True


def test_picks_least_recently_used_not_first_ready():
    """Rotation, not failover: always returning the first ready session
    would drive every request through it until banned."""
    items = [_item("a", last_used=NOW - 10), _item("b", last_used=NOW - 500), _item("c", last_used=0)]
    chosen = _pick_least_recently_used(items)
    assert chosen["id"] == "c"  # never used (0) sorts first


def test_dead_and_rate_limited_are_skipped_when_picking():
    items = [_item("dead", status="expired", last_used=0), _item("busy", rate_limited_until=NOW + 10, last_used=0), _item("ok", last_used=NOW - 1)]
    available = [s for s in items if _is_available(s, NOW)]
    chosen = _pick_least_recently_used(available)
    assert chosen["id"] == "ok"


def test_empty_pool_has_no_pick():
    assert _pick_least_recently_used([]) is None


def test_pool_summary_counts():
    items = [_item("a", status="ready", last_used=0), _item("b", status="expired"), _item("c", rate_limited_until=NOW + 10)]
    summary = pool_summary_of(items, NOW)
    assert summary == {"total": 3, "available": 1, "dead": 1}
