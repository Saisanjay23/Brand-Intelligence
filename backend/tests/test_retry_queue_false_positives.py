"""A field a platform CANNOT produce is not a field we missed.

WHAT THIS GUARDS
    `missing_fields` decides whether a row is re-queued for analysis. A
    field required on a platform that is structurally incapable of
    producing it can never be satisfied, so the row is re-queued on every
    sweep forever, burns its retry budget, and shows up to the analyst as
    data loss that no amount of re-scraping will fix.

    Measured live on 2026-08-23 -- 86 rows in the retry queue, every one
    of them `analysis_status: OK`, i.e. read successfully:

      telegram 10  ONLY unmet field `screenshot`. Telegram speaks MTProto;
                   there is no browser and telegram/analysis_engine.py has
                   no screenshot method at all.
      youtube   1  same shape (followers 148000, last post read). YouTube
                   is the official Data API -- also no browser.
      telegram  1  t.me/CA_NITIN_MURARKA6, entity_type "profile": a USER
                   account, where followers AND last-post are both
                   protocol-level absences, not misses.

    `want_screenshot` comes from the run-wide `settings.capture_evidence`,
    so switching evidence on silently made every YouTube and Telegram row
    permanently incomplete.
"""

from __future__ import annotations

import pytest

from backend.shared.completeness import (PLATFORMS_WITH_SCREENSHOT, field_report,
                                          missing_fields)
from backend.shared.models.row import Row


def _row(**kw) -> Row:
    row = Row(url="u", target="t", original_feed="")
    row.status = "OK"
    row.profile_name = kw.pop("name", "Someone")
    row.entity_type = kw.pop("entity_type", "profile")
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def _full(**kw) -> Row:
    """A row with everything a browser platform would normally carry."""
    base = dict(followers=100, last_post_iso="2026-08-20", posts_seen="yes",
                screenshot="c/p/1.png")
    base.update(kw)
    return _row(**base)


class TestPlatformsThatCannotScreenshot:
    """Established by reading the engines: only the four browser platforms
    define Scraper.screenshot."""

    @pytest.mark.parametrize("platform", ["telegram", "youtube"])
    def test_a_missing_screenshot_is_not_a_miss(self, platform):
        row = _full(screenshot="", entity_type="channel")
        assert missing_fields(platform, row, want_screenshot=True) == []

    @pytest.mark.parametrize("platform", ["telegram", "youtube"])
    def test_the_verdict_says_not_collected_rather_than_missed(self, platform):
        row = _full(screenshot="", entity_type="channel")
        report = field_report(platform, row, want_screenshot=True)
        assert report["screenshot"] == "not-collected"

    def test_the_live_telegram_shape_leaves_the_queue(self):
        """The 10 rows measured: name, followers and last post all read,
        only the impossible screenshot outstanding."""
        row = _row(entity_type="channel", followers=14233,
                   last_post_iso="2026-08-21", posts_seen="yes", screenshot="")
        assert missing_fields("telegram", row, want_screenshot=True) == []

    def test_the_live_youtube_shape_leaves_the_queue(self):
        row = _row(entity_type="channel", followers=148000,
                   last_post_iso="2026-08-17", posts_seen="yes",
                   location="IN", screenshot="")
        assert missing_fields("youtube", row, want_screenshot=True) == []


class TestBrowserPlatformsStillRequireIt:
    """The carve-out must not become a blanket excuse -- a browser platform
    that failed to capture really did lose evidence."""

    @pytest.mark.parametrize("platform", sorted(PLATFORMS_WITH_SCREENSHOT))
    def test_a_missing_screenshot_is_still_a_miss(self, platform):
        row = _full(screenshot="")
        assert "screenshot" in missing_fields(platform, row, want_screenshot=True)

    @pytest.mark.parametrize("platform", sorted(PLATFORMS_WITH_SCREENSHOT))
    def test_and_is_still_reported_as_missed(self, platform):
        row = _full(screenshot="")
        assert field_report(platform, row, want_screenshot=True)["screenshot"] == "MISSED"

    def test_capture_switched_off_is_still_configuration_not_a_gap(self):
        row = _full(screenshot="")
        assert missing_fields("facebook", row, want_screenshot=False) == []

    def test_the_roster_is_exactly_the_browser_platforms(self):
        assert PLATFORMS_WITH_SCREENSHOT == {"facebook", "instagram", "twitter", "tiktok"}


class TestTelegramUserAccounts:
    """MTProto exposes no member count for a User (only Channel/Chat carry
    participants_count) and a user's own messages are not readable -- see
    Telegram.last_post's docstring."""

    def test_no_followers_is_not_a_miss(self):
        row = _row(entity_type="profile", followers=None, friends=None,
                   last_post_iso="", screenshot="")
        assert missing_fields("telegram", row, want_screenshot=True) == []

    def test_the_exact_frozen_row_clears(self):
        """t.me/CA_NITIN_MURARKA6 -- all three fields marked MISSED, all
        three structural."""
        row = _row(entity_type="profile", followers=None, friends=None,
                   last_post_iso="", posts_seen=None, screenshot="")
        assert missing_fields("telegram", row, want_screenshot=True) == []

    def test_the_verdicts_read_not_collected(self):
        row = _row(entity_type="profile", followers=None, friends=None,
                   last_post_iso="", screenshot="")
        report = field_report("telegram", row, want_screenshot=True)
        assert report["followers"] == "not-collected"
        assert report["last_post_date"] == "not-collected"


class TestTelegramChannelsAreUnaffected:
    """Channels DO publish both, so a blank one there is a real miss."""

    def test_a_channel_missing_its_member_count_is_still_a_miss(self):
        row = _row(entity_type="channel", followers=None, friends=None,
                   last_post_iso="2026-08-21", screenshot="")
        assert "followers" in missing_fields("telegram", row, want_screenshot=True)

    def test_a_channel_missing_its_last_post_is_still_a_miss(self):
        row = _row(entity_type="channel", followers=500,
                   last_post_iso="", posts_seen=None, screenshot="")
        assert "last post date" in missing_fields("telegram", row, want_screenshot=True)

    def test_a_user_on_another_platform_is_unaffected(self):
        """The carve-out is Telegram-specific; an X profile with no
        follower count really is a miss."""
        row = _row(entity_type="profile", followers=None, friends=None,
                   last_post_iso="2026-08-20", screenshot="s.png")
        assert "followers" in missing_fields("twitter", row, want_screenshot=True)


class TestZeroIsARealReading:
    """Guards the pre-existing rule the live data depends on: an account
    with 0 followers has been READ, not missed. instagram/saudiprince638
    came back followers=0, exact=yes."""

    def test_zero_followers_is_not_missing(self):
        row = _full(followers=0)
        assert missing_fields("instagram", row, want_screenshot=True) == []

    def test_zero_followers_reads_as_read(self):
        row = _full(followers=0)
        assert field_report("instagram", row, want_screenshot=True)["followers"] == "read"


class TestTheCarveOutsThatAlreadyExisted:
    """Regression cover for the rules this change sits alongside."""

    def test_a_confirmed_postless_account_is_not_missing_a_date(self):
        row = _full(last_post_iso="", posts_seen="no")
        assert missing_fields("twitter", row, want_screenshot=True) == []

    def test_a_private_timeline_is_not_missing_a_date(self):
        row = _full(last_post_iso="", posts_seen=None)
        row.note("private account -- posts not visible")
        assert missing_fields("instagram", row, want_screenshot=True) == []

    def test_a_facebook_profile_publishing_no_audience_is_not_missing_one(self):
        row = _full(followers=None, friends=None)
        row.note("profile publishes no audience count")
        assert missing_fields("facebook", row, want_screenshot=True) == []

    def test_a_gone_profile_is_never_re_queued(self):
        row = _row(followers=None, last_post_iso="", screenshot="")
        row.status = "GONE"
        assert missing_fields("facebook", row, want_screenshot=True) == []
