"""Telling "the platform does not publish this" apart from "we failed to read it".

Both look identical in the database -- a blank cell -- and they demand
opposite responses: leave it alone, or go get it. Getting this wrong is not
cosmetic. A blank that is wrongly classed as a MISS is re-queued on every
sweep forever, burning the retry budget on something no visit can ever
produce, and is reported to the analyst as real data loss.

Each case below was measured against live accounts on 2026-08-22 before
being encoded here.
"""

from __future__ import annotations

from backend.platforms.facebook.analysis_engine import NO_AUDIENCE_NOTE
from backend.shared.completeness import field_report, missing_fields
from backend.shared.models.row import Row


def _row(**kw) -> Row:
    row = Row(url=kw.pop("url", "https://www.facebook.com/profile.php?id=1"),
              target="Acme")
    row.profile_name = kw.pop("profile_name", "Someone")
    row.status = kw.pop("status", "OK")
    for k, v in kw.items():
        setattr(row, k, v)
    return row


class TestFacebookProfileWithNoPublishedAudience:
    """Live-confirmed: a brand-new locked-down personal profile renders its
    name, "Add friend" and its tab bar, and no count anywhere -- not on the
    timeline, not on /friends, not on /about, not on
    /about_profile_transparency, and no rendered chip in the GraphQL payload
    either. 28 stored rows were in this state, all counted as misses."""

    def test_blank_audience_with_the_note_is_not_a_miss(self):
        row = _row(followers=None, friends=None, posts_seen="no",
                   notes=NO_AUDIENCE_NOTE)
        assert "followers" not in missing_fields(
            "facebook", row, want_screenshot=False)

    def test_it_reports_as_absent_not_missed(self):
        row = _row(followers=None, friends=None, posts_seen="no",
                   notes=NO_AUDIENCE_NOTE)
        verdict = field_report("facebook", row, want_screenshot=False)["followers"]
        assert verdict == "none-exist"

    def test_without_the_note_a_blank_audience_is_still_a_miss(self):
        """The note is the whole signal. A profile that simply came away
        empty -- no evidence either way -- must stay retryable, or this
        change would paper over real losses."""
        row = _row(followers=None, friends=None, posts_seen="no")
        assert "followers" in missing_fields("facebook", row, want_screenshot=False)
        assert field_report("facebook", row, want_screenshot=False)["followers"] == "MISSED"

    def test_a_real_count_is_unaffected(self):
        row = _row(followers=146, friends=146, posts_seen="no")
        assert "followers" not in missing_fields("facebook", row, want_screenshot=False)
        assert field_report("facebook", row, want_screenshot=False)["followers"] == "read"


class TestPostsSeenDrivesTheLastPostVerdict:
    def test_a_posting_account_with_no_date_is_a_real_miss(self):
        """All 18 stored Twitter rows in this state carried posts_seen=None,
        which made a genuine miss indistinguishable from an empty account.
        The engines now record "yes" whenever the payload states a non-zero
        post count."""
        row = _row(followers=10, posts_seen="yes", last_post_iso="")
        assert "last post date" in missing_fields("twitter", row, want_screenshot=False)
        assert field_report("twitter", row, want_screenshot=False)["last_post_date"] == "MISSED"

    def test_an_account_with_no_posts_is_not_a_miss(self):
        row = _row(followers=10, posts_seen="no", last_post_iso="")
        assert "last post date" not in missing_fields("twitter", row, want_screenshot=False)
        assert field_report("twitter", row, want_screenshot=False)["last_post_date"] == "none-exist"
