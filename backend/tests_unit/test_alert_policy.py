"""alert_policy: which incidents earn an interrupt, and how repeats are
collapsed.

The two failure modes this replaced are both regressions worth locking
down: a burst of ExtractionDegraded flooding the inbox (57 in one live
morning), and a per-platform debounce silently swallowing a SECOND,
unrelated failure on the same platform.
"""

from __future__ import annotations

from backend.services import alert_policy
from backend.services.alert_policy import CRITICAL, INFO, WARNING, AlertRouter


def _incident(**overrides) -> dict:
    base = {
        "platform": "facebook", "kind": "discovery", "scope": "client-1",
        "job_id": "job-1", "url": "", "error_type": "ParserDrift",
        "message": "every sweep returned 0 results",
        "cause": "The platform changed its payload shape.",
        "fix": "Update the parser.", "source_file": "", "where": "",
    }
    base.update(overrides)
    return base


class TestSeverity:
    def test_dead_session_is_critical(self):
        assert alert_policy.severity_of(_incident(error_type="SessionInvalid")) == CRITICAL

    def test_broken_parser_is_critical(self):
        assert alert_policy.severity_of(_incident(error_type="ParserDrift")) == CRITICAL

    def test_missing_field_is_critical(self):
        assert alert_policy.severity_of(_incident(error_type="FieldExtractionDrift")) == CRITICAL

    def test_working_fallback_is_only_a_warning(self):
        # the 57-in-one-morning case: real, worth knowing, not an interrupt
        assert alert_policy.severity_of(_incident(error_type="ExtractionDegraded")) == WARNING

    def test_transient_page_timeout_is_never_paged(self):
        i = _incident(error_type="Unknown", cause="The page failed to load in time -- slow connection.")
        assert alert_policy.severity_of(i) == INFO

    def test_empty_analysis_queue_is_never_paged(self):
        i = _incident(error_type="Unknown", cause="Analysis was asked to run but no profile matched.")
        assert alert_policy.severity_of(i) == INFO

    def test_an_unknown_failure_type_is_treated_as_critical(self):
        # a brand-new failure mode nobody has triaged must not be silent --
        # that is the exact silent-drift class this subsystem exists for
        assert alert_policy.severity_of(_incident(error_type="SomethingBrandNew")) == CRITICAL

    def test_error_type_beats_cause_text(self):
        # a dead session whose message happens to mention a timeout is
        # still a dead session
        i = _incident(error_type="SessionInvalid", cause="The page failed to load in time")
        assert alert_policy.severity_of(i) == CRITICAL


class TestFingerprint:
    def test_same_break_fingerprints_identically_despite_differing_messages(self):
        a = _incident(message="3 of 6 profiles blank", job_id="job-1", where="  file.py:10 in fill()")
        b = _incident(message="9 of 12 profiles blank", job_id="job-9", where="  file.py:10 in fill()")
        assert alert_policy.fingerprint(a) == alert_policy.fingerprint(b)

    def test_different_error_types_on_one_platform_are_different_breaks(self):
        a = _incident(error_type="SessionInvalid")
        b = _incident(error_type="ParserDrift")
        assert alert_policy.fingerprint(a) != alert_policy.fingerprint(b)

    def test_same_error_on_different_platforms_are_different_breaks(self):
        assert alert_policy.fingerprint(_incident(platform="facebook")) != \
            alert_policy.fingerprint(_incident(platform="twitter"))

    def test_different_code_locations_are_different_breaks(self):
        a = _incident(where="  backend/x.py:10 in fill()")
        b = _incident(where="  backend/x.py:99 in read_counts()")
        assert alert_policy.fingerprint(a) != alert_policy.fingerprint(b)


class TestRoutingSendsRealBreaks:
    def test_a_critical_break_emails_immediately(self):
        assert AlertRouter().decide(_incident()).send is True

    def test_a_warning_never_interrupts(self):
        d = AlertRouter().decide(_incident(error_type="ExtractionDegraded"))
        assert d.send is False
        assert d.severity == WARNING


class TestRoutingSuppressesRepeatsOnly:
    def test_the_same_break_twice_only_emails_once(self):
        router = AlertRouter()
        assert router.decide(_incident(), now=1000).send is True
        assert router.decide(_incident(), now=1060).send is False

    def test_a_different_break_on_the_same_platform_still_emails(self):
        """The regression the old per-platform debounce caused: a
        SessionInvalid on Facebook must not silence a ParserDrift on
        Facebook for the next hour."""
        router = AlertRouter()
        assert router.decide(_incident(error_type="SessionInvalid"), now=1000).send is True
        assert router.decide(_incident(error_type="ParserDrift"), now=1060).send is True

    def test_the_same_break_emails_again_after_the_quiet_period(self):
        router = AlertRouter(realert_seconds=3600)
        assert router.decide(_incident(), now=1000).send is True
        assert router.decide(_incident(), now=1000 + 3601).send is True

    def test_suppressed_repeats_are_counted_and_reported_in_the_next_email(self):
        router = AlertRouter(realert_seconds=100)
        router.decide(_incident(), now=1000)
        for t in range(1010, 1060, 10):
            router.decide(_incident(), now=t)
        later = router.decide(_incident(), now=1200)
        assert later.send is True
        assert later.suppressed_since_last == 5


class TestFloodCeiling:
    def test_a_whole_estate_outage_cannot_send_unbounded_email(self):
        router = AlertRouter(max_per_hour=3)
        sent = sum(
            router.decide(_incident(platform=f"p{i}"), now=1000 + i).send
            for i in range(10)
        )
        assert sent == 3

    def test_the_ceiling_lifts_once_the_hour_rolls_over(self):
        router = AlertRouter(max_per_hour=2)
        router.decide(_incident(platform="a"), now=1000)
        router.decide(_incident(platform="b"), now=1001)
        assert router.decide(_incident(platform="c"), now=1002).send is False
        assert router.decide(_incident(platform="c"), now=1002 + 3601).send is True
