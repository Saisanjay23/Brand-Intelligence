"""latest_post() must exclude a pinned tweet, not just a retweet.

Shape matches a real captured UserTweets response: the pin is not a marker
on the tweet object itself, it's a distinct top-level instruction
(TimelinePinEntry) wrapping the tweet, sitting next to the normal
TimelineAddEntries instruction. Reproduced here as a regression test for
the live-confirmed bug where a pinned tweet's date (2026-08-10) was
reported instead of the account's real last post (2026-08-07).
"""

from __future__ import annotations

from backend.platforms.twitter.discovery_engine import latest_post, pinned_tweet_ids


def _tweet(rest_id: str, created_at: str, user_id="999", handle="cyfimar", is_retweet=False):
    legacy = {
        "created_at": created_at,
        "full_text": "hello",
        "id_str": rest_id,
        "user_id_str": user_id,
    }
    if is_retweet:
        legacy["retweeted_status_result"] = {"result": {}}
    return {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "legacy": legacy,
        "core": {"user_results": {"result": {"legacy": {"screen_name": handle}}}},
    }


def _timeline_blob(entries: list[dict], pin_entry: dict | None = None):
    instructions = [{"type": "TimelineClearCache"}]
    if pin_entry is not None:
        instructions.append({"type": "TimelinePinEntry", "entry": {
            "entryId": f"tweet-{pin_entry['rest_id']}",
            "content": {"itemContent": {"tweet_results": {"result": pin_entry}}},
        }})
    instructions.append({"type": "TimelineAddEntries", "entries": [
        {"entryId": f"tweet-{e['rest_id']}",
         "content": {"itemContent": {"tweet_results": {"result": e}}}}
        for e in entries
    ]})
    return {"data": {"user": {"result": {"timeline": {"timeline": {"instructions": instructions}}}}}}


class TestPinnedTweetIds:
    def test_finds_the_pinned_tweet_id(self):
        pinned = _tweet("111", "Mon Aug 10 07:23:29 +0000 2026")
        blob = _timeline_blob([_tweet("222", "Fri Aug 07 06:33:54 +0000 2026")], pin_entry=pinned)
        assert pinned_tweet_ids(blob) == {"111"}

    def test_no_pin_instruction_means_no_pinned_ids(self):
        blob = _timeline_blob([_tweet("222", "Fri Aug 07 06:33:54 +0000 2026")])
        assert pinned_tweet_ids(blob) == set()


class TestLatestPostExcludesThePin:
    def test_the_live_captured_scenario(self):
        # exact reproduction: pin is newer than the real last post
        pinned = _tweet("111", "Mon Aug 10 07:23:29 +0000 2026")
        blob = _timeline_blob(
            [_tweet("222", "Fri Aug 07 06:33:54 +0000 2026"),
             _tweet("333", "Thu Aug 06 07:22:42 +0000 2026")],
            pin_entry=pinned,
        )
        assert latest_post(blob, handle="cyfimar") == "2026-08-07"

    def test_without_the_fix_this_would_have_regressed_to_the_pin_date(self):
        # sanity: the pin really is the newest raw timestamp present, so a
        # correct exclusion is doing real work here, not a no-op
        pinned = _tweet("111", "Mon Aug 10 07:23:29 +0000 2026")
        blob = _timeline_blob([_tweet("222", "Fri Aug 07 06:33:54 +0000 2026")], pin_entry=pinned)
        result = latest_post(blob, handle="cyfimar")
        assert result == "2026-08-07"
        assert result != "2026-08-10"

    def test_retweets_are_still_excluded_alongside_pins(self):
        pinned = _tweet("111", "Mon Aug 10 07:23:29 +0000 2026")
        rt = _tweet("444", "Sun Aug 09 00:00:00 +0000 2026", is_retweet=True)
        organic = _tweet("222", "Fri Aug 07 06:33:54 +0000 2026")
        blob = _timeline_blob([rt, organic], pin_entry=pinned)
        assert latest_post(blob, handle="cyfimar") == "2026-08-07"

    def test_only_a_pinned_tweet_present_yields_no_date(self):
        pinned = _tweet("111", "Mon Aug 10 07:23:29 +0000 2026")
        blob = _timeline_blob([], pin_entry=pinned)
        assert latest_post(blob, handle="cyfimar") == ""
