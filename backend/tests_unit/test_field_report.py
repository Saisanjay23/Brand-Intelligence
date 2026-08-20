"""shared/completeness.py::field_report -- the verdict that makes a blank
cell explainable.

THE DISTINCTION THIS EXISTS TO ENFORCE
    "No last post date" meant two opposite things and looked identical in
    the database: the account has never posted (nothing to fetch, ever), or
    its timeline failed to load (go and get it). Every engine already
    computed `posts_seen` to tell them apart and the value was discarded
    before it reached storage, so downstream nothing could.

    The cases below are the real ones observed live while verifying this:
    a Facebook profile with posts_seen="no" and no date (genuinely never
    posted -- not a loss), and an Instagram profile with posts_seen="yes"
    and no date (the timeline was there and we still came away empty --
    a real loss).
"""

from __future__ import annotations

from backend.shared.completeness import field_report, missing_fields
from backend.shared.models.row import Row


def _row(**kw) -> Row:
    row = Row(url=kw.pop("url", "https://x.test/a"), target=kw.pop("target", "Acme"))
    row.status = kw.pop("status", "OK")
    for k, v in kw.items():
        setattr(row, k, v)
    return row


class TestLastPostVerdict:
    def test_confirmed_no_posts_is_not_a_loss(self):
        """Live: facebook profile.php?id=100094076126892 -- 12 friends, no
        posts at all. A blank date here is the correct, final answer."""
        row = _row(profile_name="Gautam Adani", friends=12, posts_seen="no")
        rep = field_report("facebook", row, want_screenshot=False)
        assert rep["last_post_date"] == "none-exist"
        assert "last post date" not in missing_fields("facebook", row, want_screenshot=False)

    def test_posts_present_but_undated_is_a_real_loss(self):
        """Live: instagram/gautamadanifans/ -- 32 followers, posts_seen=yes,
        no date extracted. This is the one shape worth re-visiting."""
        row = _row(profile_name="Gautamadani", followers=32, posts_seen="yes")
        rep = field_report("instagram", row, want_screenshot=False)
        assert rep["last_post_date"] == "MISSED"

    def test_a_read_date_is_read(self):
        row = _row(profile_name="Gautam Adani", followers=1400000,
                   posts_seen="yes", last_post_iso="2026-08-19")
        assert field_report("instagram", row, want_screenshot=False)["last_post_date"] == "read"

    def test_private_timeline_is_not_a_loss(self):
        """The platform is withholding the posts, not the scraper losing
        them -- the carve-out completeness.py already honours, now named."""
        row = _row(profile_name="Someone", followers=5,
                   notes="protected account -- posts not visible")
        assert field_report("twitter", row, want_screenshot=False)["last_post_date"] == "none-exist"


class TestLocationVerdict:
    def test_platform_that_never_reads_location_reports_not_collected(self):
        """Instagram/TikTok/Telegram engines have no location reader at all
        (verified by reading them), so 309/309 blank instagram locations are
        not 309 misses -- nothing ever attempted to fill them."""
        row = _row(profile_name="x", followers=1, posts_seen="no")
        for platform in ("instagram", "tiktok", "telegram"):
            assert field_report(platform, row, want_screenshot=False)["location"] == "not-collected"

    def test_supported_platform_reads_it_when_present(self):
        row = _row(profile_name="x", followers=1, posts_seen="no", location="Ahmedabad, India")
        assert field_report("twitter", row, want_screenshot=False)["location"] == "read"

    def test_supported_platform_blank_is_absent_never_missed(self):
        """A user who simply never set a location is indistinguishable from
        a failed read, so it is reported honestly as absent rather than
        being handed to a retry that could never resolve it."""
        row = _row(profile_name="x", followers=1, posts_seen="no")
        assert field_report("facebook", row, want_screenshot=False)["location"] == "none-exist"


class TestOtherVerdicts:
    def test_gone_profile_reports_unknown_not_missed(self):
        """A removed profile's blanks are the honest answer; calling them
        misses would put it in a retry queue forever."""
        row = _row(status="GONE")
        rep = field_report("facebook", row, want_screenshot=False)
        assert set(rep.values()) == {"unknown"}

    def test_screenshot_not_collected_when_capture_is_off(self):
        row = _row(profile_name="x", followers=1, posts_seen="no")
        assert field_report("facebook", row, want_screenshot=False)["screenshot"] == "not-collected"
        assert field_report("facebook", row, want_screenshot=True)["screenshot"] == "MISSED"

    def test_group_is_exempt_from_the_audience_requirement(self):
        """Facebook groups publish a member count under neither followers
        nor friends."""
        row = _row(profile_name="A Group", entity_type="group", posts_seen="no")
        assert field_report("facebook", row, want_screenshot=False)["followers"] == "not-collected"

    def test_missing_name_is_a_real_loss(self):
        row = _row(profile_name="", followers=10, posts_seen="no")
        assert field_report("facebook", row, want_screenshot=False)["display_name"] == "MISSED"

    def test_zero_followers_counts_as_read(self):
        """0 is a reading, not an absence -- a new account really can have
        none. Mirrors completeness._blank."""
        row = _row(profile_name="x", followers=0, posts_seen="no")
        assert field_report("instagram", row, want_screenshot=False)["followers"] == "read"
