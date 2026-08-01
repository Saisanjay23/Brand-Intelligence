"""Unit tests for the incident diagnosis rulebook -- pure regex matching,
no I/O. Pins that each known failure shape gets ITS OWN diagnosis, not the
generic fallback, and that an unrecognized error correctly falls through to
the generic one rather than matching something misleading.
"""

from __future__ import annotations

from backend.services.incident_service import _GENERIC, diagnose


def test_session_checkpoint_gets_a_specific_diagnosis():
    d = diagnose("RuntimeError", "session invalid or checkpointed")
    assert d is not _GENERIC
    assert "checkpoint" in d.cause.lower() or "cookies" in d.cause.lower()


def test_credentials_not_ready_gets_a_specific_diagnosis():
    d = diagnose("RuntimeError", "youtube credentials invalid or rejected")
    assert d is not _GENERIC
    assert "credentials" in d.cause.lower()


def test_quota_exceeded_gets_a_specific_diagnosis():
    d = diagnose("QuotaExceeded", "quotaExceeded")
    assert "quota" in d.cause.lower()


def test_flood_wait_gets_a_specific_diagnosis():
    d = diagnose("FloodWaitError", "A wait of 3600 seconds is required")
    assert "telegram" in d.cause.lower() or "rate limit" in d.cause.lower()


def test_unrecognized_error_falls_through_to_generic():
    d = diagnose("ValueError", "something completely unprecedented happened")
    assert d is _GENERIC


def test_rule_matching_is_case_insensitive():
    d = diagnose("Error", "SESSION INVALID OR CHECKPOINTED")
    assert d is not _GENERIC
