"""What an alert email actually says.

The requirement these lock down: a recipient must be able to act on the
mail alone -- it has to name what broke, which field, why, what to change,
and exactly which file and line -- without opening the dashboard or the
codebase first. Both the plain-text and HTML parts must carry it, because
which one a client renders is not ours to choose.
"""

from __future__ import annotations

from backend.services.alerting_service import _build_email


def _incident(**overrides) -> dict:
    base = {
        "platform": "instagram", "kind": "analysis", "scope": "adani-group",
        "job_id": "job-42", "url": "https://instagram.com/fake_adani",
        "error_type": "FieldExtractionDrift",
        "message": "instagram: the 'follower count' field came back empty for 9 of 12 profiles.",
        "cause": "The platform changed the part of its data feed that carries this field.",
        "fix": "Update the extraction code.",
        "source_file": "backend.platforms.instagram.analysis_engine:Scraper",
        "where": "  backend/platforms/instagram/analysis_engine.py:458 in Scraper.fill()",
        "severity": "critical",
    }
    base.update(overrides)
    return base


def _parts(msg) -> tuple[str, str]:
    return (
        msg.get_body(preferencelist=("plain",)).get_content(),
        msg.get_body(preferencelist=("html",)).get_content(),
    )


class TestSubjectIsScannable:
    def test_subject_says_what_broke_not_just_that_something_did(self):
        subject = _build_email(_incident())["Subject"]
        assert "Instagram" in subject
        assert "stopped extracting" in subject

    def test_a_dead_session_keeps_its_own_unambiguous_subject(self):
        subject = _build_email(_incident(error_type="SessionInvalid"))["Subject"]
        assert "Login page detected" in subject


class TestBothPartsCarryTheActionableDetail:
    def test_the_exact_file_and_line_appear_in_both_parts(self):
        text, html = _parts(_build_email(_incident()))
        for part in (text, html):
            assert "analysis_engine.py:458" in part
            assert "Scraper.fill()" in part

    def test_the_folder_to_work_in_appears_in_both_parts(self):
        text, html = _parts(_build_email(_incident()))
        for part in (text, html):
            assert "backend/platforms/instagram/" in part

    def test_numbered_fix_steps_appear_in_both_parts(self):
        text, html = _parts(_build_email(_incident()))
        assert "1. Open the file named under" in text
        assert "<ol" in html and "Open the file named under" in html

    def test_the_cause_and_observation_appear_in_both_parts(self):
        text, html = _parts(_build_email(_incident()))
        for part in (text, html):
            assert "changed the part of its data feed" in part
            assert "9 of 12 profiles" in part


class TestRepeatsAreDisclosedNotHidden:
    def test_collapsed_repeats_are_stated_in_both_parts(self):
        text, html = _parts(_build_email(_incident(suppressed_since_last=57)))
        for part in (text, html):
            assert "57" in part
        assert "collapsed into this" in text

    def test_a_first_occurrence_says_nothing_about_repeats(self):
        text, _ = _parts(_build_email(_incident()))
        assert "collapsed into this" not in text


class TestSessionFailureReadsAsAnOpsTaskNotACodeTask:
    def test_it_tells_the_operator_to_replace_cookies_not_read_code(self):
        text, _ = _parts(_build_email(_incident(error_type="SessionInvalid")))
        assert "logged out or challenged" in text
        assert "Sessions" in text

    def test_it_does_not_send_them_into_a_platform_engine_folder(self):
        text, _ = _parts(_build_email(_incident(error_type="SessionInvalid", where="", source_file="")))
        assert "backend/platforms/instagram/" not in text


class TestRenderingIsSafe:
    def test_html_from_a_platform_message_cannot_break_out_of_the_markup(self):
        # scraped text reaches the message field; it must never be able to
        # inject markup into an email we send to ourselves
        evil = "<script>alert(1)</script> & \"quoted\""
        _, html = _parts(_build_email(_incident(message=evil)))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_an_incident_with_no_blame_trail_still_renders(self):
        text, html = _parts(_build_email(_incident(where="", source_file="")))
        assert text.strip() and "<html" in html
