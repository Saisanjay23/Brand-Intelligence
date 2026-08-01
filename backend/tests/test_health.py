"""Unit tests for the platform health scoring -- pure, in-memory, no I/O."""

from __future__ import annotations

import pytest

from backend.engine import health as health_engine


@pytest.fixture(autouse=True)
def _reset_health():
    health_engine._by_platform.clear()
    yield
    health_engine._by_platform.clear()


def test_unknown_before_any_outcome_recorded():
    assert health_engine.one("facebook")["state"] == "unknown"


def test_all_ok_is_healthy():
    for _ in range(10):
        health_engine.record("facebook", "OK")
    d = health_engine.one("facebook")
    assert d["state"] == "healthy"
    assert d["score"] == 1.0


def test_all_bad_is_critical():
    for _ in range(10):
        health_engine.record("facebook", "ERROR")
    d = health_engine.one("facebook")
    assert d["state"] == "critical"
    assert d["score"] == 0.0


def test_partial_counts_as_half_credit():
    health_engine.record("facebook", "PARTIAL")
    d = health_engine.one("facebook")
    assert d["score"] == 0.5


def test_platforms_are_tracked_independently():
    health_engine.record("facebook", "ERROR")
    health_engine.record("twitter", "OK")
    assert health_engine.one("facebook")["state"] == "critical"
    assert health_engine.one("twitter")["state"] == "healthy"
