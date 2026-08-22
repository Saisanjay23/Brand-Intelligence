"""The /with_replies tier: an account whose posts are all replies.

X's `UserOriginalsTimeline` serves ORIGINAL posts only, while
`tweet_counts.tweets` counts replies too. An account that only ever replies
therefore reports a non-zero post count and hands back an empty timeline --
which, from the profile tab alone, is indistinguishable from a scraping
failure.

Live-confirmed 2026-08-22 on real stored rows that had come away blank:
@MBS_4_U reports 7 posts, its profile tab's timeline response carries no
tweet objects at all (only who-to-follow entries and cursors) and renders
zero tweet cells, and its /with_replies tab answers 2020-01-17 from
`UserRepliesTimeline`. 18 of 179 stored Twitter rows were in this state.
"""

from __future__ import annotations

from backend.platforms.twitter.discovery_engine import (
    TWEETS_QUERIES, latest_post)

# Trimmed from a real UserOriginalsTimeline response for such an account:
# entries exist, but not one of them is a tweet.
ORIGINALS_EMPTY = {
    "data": {"user": {"result": {"timeline": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [
            {"entryId": "who-to-follow-2091169117124952064"},
            {"entryId": "cursor-top-2091169117124952065"},
            {"entryId": "cursor-bottom-2091169117124952063"},
        ]}
    ]}}}}
}

# Trimmed from the real UserRepliesTimeline response for the same account.
REPLIES = {
    "data": {"user": {"result": {"timeline": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [
            {"entryId": "tweet-1218000000000000000", "content": {"itemContent": {
                "tweet_results": {"result": {
                    "rest_id": "1218000000000000000",
                    "core": {"user_results": {"result": {
                        "core": {"screen_name": "MBS_4_U"}}}},
                    "legacy": {
                        "id_str": "1218000000000000000",
                        "user_id_str": "999",
                        "created_at": "Fri Jan 17 09:00:00 +0000 2020",
                        "full_text": "a reply",
                        "conversation_id_str": "1217000000000000000",
                    },
                }}
            }}},
        ]}
    ]}}}}
}


class TestQueryCoverage:
    def test_replies_timeline_is_intercepted(self):
        """The /with_replies tab's query. Without this fragment the tier's
        payload is never handed to the parser and the tab load is wasted."""
        url = "https://x.com/i/api/graphql/abc/UserRepliesTimeline?variables=%7B%7D"
        assert any(q in url for q in TWEETS_QUERIES)

    def test_the_original_two_are_still_matched(self):
        for name in ("UserTweets", "UserOriginalsTimeline"):
            assert any(q in f"https://x.com/i/api/graphql/x/{name}" for q in TWEETS_QUERIES)


class TestParsingEachTimeline:
    def test_an_originals_timeline_with_no_tweets_yields_nothing(self):
        """Not an error and not a guess -- this really is what X returns for
        an account that has never posted an original."""
        assert latest_post(ORIGINALS_EMPTY, "MBS_4_U") == ""

    def test_the_replies_timeline_yields_the_date(self):
        assert latest_post(REPLIES, "MBS_4_U") == "2020-01-17"

    def test_a_reply_counts_as_the_account_s_own_activity(self):
        """A reply is content the account itself published, so it is honest
        activity. `latest_post` still drops retweets and pinned tweets,
        which are the two things that would flatter a dormant account."""
        assert latest_post(REPLIES, "MBS_4_U", "999") == "2020-01-17"

    def test_another_handle_is_not_credited_with_it(self):
        assert latest_post(REPLIES, "someone_else") == ""
