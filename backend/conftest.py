"""Test-suite isolation from live infrastructure.

THE INCIDENT THIS EXISTS TO PREVENT
    Running `pytest` sent real alert emails to the configured recipients and
    wrote real documents into the live `incidents` collection.

    `backend/tests/test_analysis_target_scoring.py` drives the genuine
    `analysis_service._analyse_platform` with a fake scraper that returns
    rows carrying no follower count and no posts. That is a perfectly good
    test of keyword targeting -- but the real run loop it exercises also
    runs the extraction-drift detectors, and those detectors were the only
    collaborator the test did not stub. So every run raised a genuine
    `FieldExtractionDrift` and `LastPostExtractionDrift` for facebook,
    persisted it, and emailed it.

    The operator saw "last post date is missing for facebook" alerts naming
    `https://facebook.com/a` and `https://facebook.com/b` -- fixture URLs
    that do not exist -- and had no way to tell those apart from a real
    platform outage. 24 of the 25 facebook drift incidents in the database
    came from `job=j1 scope=c1`, i.e. from the test suite.

    An alert an operator learns to ignore is worse than no alert: the next
    genuine drift is indistinguishable from the noise. Hermetic tests are
    what keep the alerting channel trustworthy.

WHY THESE TWO SEAMS
    `alerting_service._smtp_send` is the single function that opens an SMTP
    connection, so blocking it is a guarantee rather than a hope: no future
    test, on any code path, can email anyone.

    `incident_service.record` is the single writer of the `incidents`
    collection and the trigger for `notify_incident`.

Tests that deliberately assert an incident WAS raised (e.g.
tests/test_analysis_last_post_health_check.py) patch `record` themselves
inside the test body; that inner patch simply nests over this one and is
restored normally, so their assertions are unaffected.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_live_alerts():
    """Applied to every test in this repository."""
    smtp = patch(
        "backend.services.alerting_service._smtp_send",
        new=MagicMock(side_effect=AssertionError(
            "a test tried to send a real email -- see backend/conftest.py"
        )),
    )
    record = patch("backend.services.incident_service.record", new=AsyncMock())
    with smtp, record:
        yield
