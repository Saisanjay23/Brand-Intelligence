"""alerting_service._build_email -- a SessionInvalid incident (discovery/
analysis conclusively hit a login/checkpoint page) must produce an
unambiguous "login page detected, update the session" email naming the
exact platform, distinct from the generic pipeline-incident wording every
other error type gets."""

from __future__ import annotations

from backend.services.alerting_service import _build_email


def _incident(**overrides) -> dict:
    base = {
        "platform": "facebook", "kind": "discovery", "scope": "client-1",
        "job_id": "job-1", "url": "", "error_type": "SessionInvalid",
        "message": "session invalid or checkpointed",
        "cause": "The saved session cookies were rejected, or the platform challenged the account.",
        "fix": "Re-export fresh cookies for this platform and re-upload them under Sessions, then retry.",
        "source_file": "", "where": "",
    }
    base.update(overrides)
    return base


class TestSessionInvalidEmail:
    def test_subject_names_the_platform_and_says_login_page_detected(self):
        msg = _build_email(_incident(platform="facebook"))
        assert "Login page detected" in msg["Subject"]
        assert "Facebook" in msg["Subject"]
        assert "please update the session" in msg["Subject"].lower()

    def test_subject_reflects_the_actual_platform_not_a_hardcoded_one(self):
        msg = _build_email(_incident(platform="telegram"))
        assert "Telegram" in msg["Subject"]
        assert "Facebook" not in msg["Subject"]

    def test_body_tells_the_analyst_what_to_do(self):
        msg = _build_email(_incident(platform="youtube"))
        # The alert is multipart/alternative (plain text + an HTML part),
        # so the plain-text body has to be selected explicitly --
        # get_content() raises on the multipart container itself. The
        # wording asserted below is unchanged; only the retrieval is.
        body = msg.get_body(preferencelist=("plain",)).get_content()
        assert "update the session for Youtube" in body or "Youtube" in body
        assert "logged out or challenged" in body

    def test_other_incident_types_keep_the_generic_subject(self):
        msg = _build_email(_incident(error_type="ParserDrift", cause="x", fix="y"))
        assert "Login page detected" not in msg["Subject"]
        assert "Pipeline Incident" in msg["Subject"]
