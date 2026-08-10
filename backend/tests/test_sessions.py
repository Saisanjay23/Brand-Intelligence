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


# ── graduated quarantine backoff ───────────────────────────────────────────
# One rate-limit used to cost a flat 24h, so a single bad afternoon could
# quarantine an entire pool until the next day.

def test_backoff_ladder_escalates_then_saturates():
    from backend.config.settings import settings

    ladder = settings.session_backoff_minutes
    assert ladder == [15, 60, 360, 1440], "default ladder changed -- update this test deliberately"

    def cooldown_for(consecutive_failures: int) -> int:
        return ladder[min(consecutive_failures, len(ladder)) - 1]

    assert cooldown_for(1) == 15      # first blip costs minutes, not a day
    assert cooldown_for(2) == 60
    assert cooldown_for(3) == 360
    assert cooldown_for(4) == 1440
    assert cooldown_for(99) == 1440   # saturates, never indexes past the end


def test_a_first_failure_is_far_cheaper_than_the_old_flat_day():
    from backend.config.settings import settings

    assert settings.session_backoff_minutes[0] < 24 * 60


def test_public_view_of_non_cookie_items():
    from backend.sessions.manager import _public
    yt_item = {"platform": "youtube", "id": "api_key", "identifier": "YouTube API Key",
               "status": "ready", "rate_limited_until": 0.0, "last_used": 0.0, "api_key": "secret"}
    assert _public(yt_item) == {
        "id": "api_key", "identifier": "YouTube API Key", "status": "ready",
        "rate_limited_until": 0.0, "last_used": 0.0, "use_count": 0, "in_use": False,
        "cookie_count": 0, "proxy_host": "",
        "is_api_key": True, "consecutive_failures": 0, "available": True,
    }


def test_public_view_never_leaks_the_credential():
    """The single most important property of _public: a session cookie /
    API key IS the credential, and this shape is what the API returns."""
    from backend.sessions.manager import _public
    item = _item("s1", status="ready")
    item["api_key"] = "SECRET-KEY"
    item["cookies"] = [{"name": "c_user", "value": "SECRET-COOKIE"}]
    rendered = repr(_public(item))
    assert "SECRET-KEY" not in rendered
    assert "SECRET-COOKIE" not in rendered


def test_public_view_reports_unavailability():
    """A quarantined session must not read as usable in the Sessions panel.

    `_public` resolves availability against the real clock, so the cooldown
    has to be genuinely in the future rather than relative to this module's
    synthetic NOW.
    """
    import time as _time

    from backend.sessions.manager import _public
    cooling = _item("s1", status="rate_limited", rate_limited_until=_time.time() + 3600)
    cooling["consecutive_failures"] = 3
    out = _public(cooling)
    assert out["available"] is False
    assert out["consecutive_failures"] == 3

