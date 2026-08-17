"""Startup self-checks for the misconfigurations that change results
without ever raising.

These are the faults that are in place before the first job runs, produce
no exception, and are invisible in the output. Each must produce a finding
that names the fix, and -- just as importantly -- must produce NOTHING on
a correctly configured host, or the alert becomes noise nobody reads.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import preflight_service


class TestFuzzyMatchingCheck:
    def test_a_missing_library_is_reported_with_the_reason_it_matters(self):
        with patch("backend.shared.text.HAVE_RF", False):
            finding = preflight_service._check_fuzzy_matching()
        assert finding is not None
        assert finding.key == "rapidfuzz-missing"
        # must say WHERE and WHAT TO RUN, not just "dependency missing"
        assert "backend/shared/text.py" in finding.message
        assert "pip install" in finding.message
        # and must say why a non-engineer should care
        assert "hide real impersonators" in finding.message

    def test_a_correctly_installed_host_reports_nothing(self):
        with patch("backend.shared.text.HAVE_RF", True):
            assert preflight_service._check_fuzzy_matching() is None


class TestBrowserFingerprintCheck:
    def test_a_stale_hardcoded_version_is_reported(self):
        with patch("backend.stealth.fingerprint.CHROME_VERSION_DETECTED", False):
            finding = preflight_service._check_browser_fingerprint()
        assert finding is not None
        assert finding.key == "chrome-version-stale"
        assert "backend/stealth/fingerprint.py" in finding.message
        assert "CHROME_PATHS" in finding.message

    def test_a_real_detected_browser_reports_nothing(self):
        with patch("backend.stealth.fingerprint.CHROME_VERSION_DETECTED", True):
            assert preflight_service._check_browser_fingerprint() is None


class TestAlertingConfiguredCheck:
    def test_no_recipient_and_no_server_are_both_named(self):
        with patch("backend.config.settings.settings.alert_emails", []), \
             patch("backend.config.settings.settings.smtp_host", ""):
            finding = preflight_service._check_alerting_configured()
        assert finding is not None
        assert "ALERT_EMAILS" in finding.message
        assert "SMTP_HOST" in finding.message

    def test_a_configured_host_reports_nothing(self):
        with patch("backend.config.settings.settings.alert_emails", ["ops@example.com"]), \
             patch("backend.config.settings.settings.smtp_host", "smtp.example.com"):
            assert preflight_service._check_alerting_configured() is None


class TestRunIsSafeAtStartup:
    @pytest.mark.asyncio
    async def test_findings_are_recorded_as_incidents(self):
        with patch("backend.shared.text.HAVE_RF", False), \
             patch("backend.stealth.fingerprint.CHROME_VERSION_DETECTED", True), \
             patch("backend.config.settings.settings.alert_emails", ["ops@example.com"]), \
             patch("backend.config.settings.settings.smtp_host", "smtp.example.com"), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock) as rec:
            findings = await preflight_service.run()
        assert [f.key for f in findings] == ["rapidfuzz-missing"]
        rec.assert_called_once()
        assert rec.call_args[0][4] == "ConfigDrift"

    @pytest.mark.asyncio
    async def test_a_healthy_host_records_nothing(self):
        with patch("backend.shared.text.HAVE_RF", True), \
             patch("backend.stealth.fingerprint.CHROME_VERSION_DETECTED", True), \
             patch("backend.config.settings.settings.alert_emails", ["ops@example.com"]), \
             patch("backend.config.settings.settings.smtp_host", "smtp.example.com"), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock) as rec:
            assert await preflight_service.run() == []
            rec.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_check_that_throws_cannot_stop_the_service_starting(self):
        boom = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch.object(preflight_service, "_CHECKS", (boom,)), \
             patch("backend.services.incident_service.record", new_callable=AsyncMock):
            assert await preflight_service.run() == []

    @pytest.mark.asyncio
    async def test_an_unreachable_incident_store_cannot_stop_startup(self):
        with patch("backend.shared.text.HAVE_RF", False), \
             patch("backend.services.incident_service.record",
                   new_callable=AsyncMock, side_effect=RuntimeError("mongo down")):
            findings = await preflight_service.run()
        assert findings  # still reported to the caller and the log
