"""X renamed the timeline GraphQL query, and the rename was silent.

Confirmed live 2026-08-18: visiting a profile fires `UserOriginalsTimeline`
(199KB, 37 tweet nodes) and never `UserTweets`, which is the only name the
listener matched. Every last-post read therefore fell through to the DOM
tier -- and that tier was racing the render (profile query answers ~4.1s,
first tweet cells exist ~5.7s), so it caught some profiles and missed
others. The result was a critical "last-post extraction is broken"
incident across 17 of 33 profiles, when the extraction logic itself was
fine.

Both fixes are pinned here: match either query name, and wait for the
timeline to paint rather than sleeping a flat 1.2s.
"""

from __future__ import annotations

import inspect

from backend.platforms.twitter import analysis_engine as ta
from backend.platforms.twitter.discovery_engine import TWEETS_QUERIES


class TestTimelineQueryNames:
    def test_the_current_name_is_matched(self):
        assert "UserOriginalsTimeline" in TWEETS_QUERIES

    def test_the_previous_name_is_still_matched(self):
        """Kept deliberately -- it still appears on some account types and
        costs nothing. A rename should ADD to this tuple, not replace."""
        assert "UserTweets" in TWEETS_QUERIES

    def test_a_real_response_url_matches(self):
        url = ("https://x.com/i/api/graphql/AbC123/UserOriginalsTimeline"
               "?variables=%7B%22userId%22%3A%22123%22%7D")
        assert any(q in url for q in TWEETS_QUERIES)

    def test_the_listener_matches_every_known_name(self):
        """The listener must consult the whole tuple, not one entry."""
        src = inspect.getsource(ta.Scraper.process)
        assert "TWEETS_QUERIES" in src
        assert "TWEETS_QUERY in resp.url" not in src, (
            "matching a single query name is what caused the silent break")

    def test_an_unrelated_query_does_not_match(self):
        url = "https://x.com/i/api/graphql/xyz/SidebarUserRecommendations"
        assert not any(q in url for q in TWEETS_QUERIES)


class TestDomFallbackWaitsForTheTimeline:
    def test_it_waits_for_tweet_cells_rather_than_sleeping(self):
        src = inspect.getsource(ta.Scraper.process)
        assert 'wait_for_selector' in src
        assert 'data-testid="tweet"' in src
        assert "wait_for_timeout(1200)" not in src, (
            "a flat sleep races the render -- measured 4.1s vs 5.7s")

    def test_the_wait_is_bounded(self):
        assert isinstance(ta._DOM_TIMELINE_WAIT_MS, int)
        assert 2000 <= ta._DOM_TIMELINE_WAIT_MS <= 30000
