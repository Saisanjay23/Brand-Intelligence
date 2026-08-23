"""A blocked region must be reported as a block, not as an empty result.

WHAT THIS GUARDS
    TikTok serves the same government notice for every URL in a region
    where it is banned, and redirects to `/<cc>/about`. Confirmed live on
    2026-08-23 from an Indian IP with no proxy: `tiktok.com/search?q=...`
    landed on `https://www.tiktok.com/in/about` carrying the June 2020
    India ban notice, with zero `/api/search/` responses, an empty
    hydration payload, and no profile links in the DOM.

    Before this was detected, the sweep ran its whole time budget against
    that notice page and finished `stopped=stalled` with "search results
    never rendered" -- a message indistinguishable from a parser break or
    a slow render, which points whoever investigates at the extraction
    code when the real fix is to route the platform through a proxy.
    Measured: 140.6s to reach a misleading answer, versus 1.1s to reach
    the right one.

    Analysis had the matching failure: the row came back PARTIAL "profile
    payload not seen", a RETRYABLE status, so every re-analysis kept
    spending attempts on something no retry can fix.
"""

from __future__ import annotations

import pytest

from backend.platforms.tiktok.discovery_engine import RE_GEOBLOCK, geoblocked


BAN_NOTICE = (
    "Dear Users, On June 29, 2020 the Govt. of India decided to block 59 apps, "
    "including TikTok. We are in the process of complying with the Government "
    "of India's directive."
)


class TestTheRedirectIsThePrimarySignal:
    """The `/<cc>/about` landing is something only the block produces, so it
    stands on its own without needing the notice text to match."""

    @pytest.mark.parametrize("url", [
        "https://www.tiktok.com/in/about",
        "https://www.tiktok.com/IN/about",
        "https://www.tiktok.com/de/about?lang=en",
    ])
    def test_a_region_about_redirect_is_a_block(self, url):
        assert geoblocked(url, "") is True

    def test_it_does_not_need_the_body_at_all(self):
        assert geoblocked("https://www.tiktok.com/in/about", "") is True


class TestTheNoticeTextCorroborates:
    """Kept for a future variant that serves the notice without redirecting."""

    def test_the_live_india_notice_matches(self):
        assert geoblocked("https://www.tiktok.com/search?q=x", BAN_NOTICE) is True

    @pytest.mark.parametrize("body", [
        "This service is not available in your country.",
        "TikTok is not available in your region right now.",
    ])
    def test_other_region_wordings_match(self, body):
        assert geoblocked("https://www.tiktok.com/search?q=x", body) is True

    def test_the_regex_is_what_does_it(self):
        assert RE_GEOBLOCK.search(BAN_NOTICE)


class TestItNeverFiresOnAWorkingPage:
    """A false positive here would take a healthy platform offline and tell
    the operator to go configure a proxy they do not need."""

    @pytest.mark.parametrize("url,body", [
        ("https://www.tiktok.com/search?q=gautam+adani", "Top Users Videos Sounds"),
        ("https://www.tiktok.com/@someone", "someone 1.2M Followers"),
        ("https://www.tiktok.com/search?q=x", ""),
    ])
    def test_ordinary_pages_are_not_blocks(self, url, body):
        assert geoblocked(url, body) is False

    def test_the_plain_about_page_is_not_a_block(self):
        """`/about` is TikTok's own ordinary about page. Only a TWO-LETTER
        region segment in front of it means a block, so the pattern must be
        anchored rather than a bare "/about" substring."""
        assert geoblocked("https://www.tiktok.com/about", "About TikTok") is False

    def test_a_word_ending_in_about_is_not_a_block(self):
        assert geoblocked("https://www.tiktok.com/@x/roundabout", "") is False

    def test_a_profile_merely_discussing_india_is_not_a_block(self):
        """The regex has to be specific enough that an account whose bio
        mentions the country does not read as a ban notice."""
        assert geoblocked(
            "https://www.tiktok.com/@indiannews",
            "News from India. Follow for daily updates about India.",
        ) is False


class TestDegradedInputs:
    def test_empty_everything_is_not_a_block(self):
        assert geoblocked("", "") is False

    def test_none_is_tolerated(self):
        assert geoblocked(None, None) is False
