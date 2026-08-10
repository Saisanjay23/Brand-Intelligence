"""K_POST_TIME must not include "created_time" -- confirmed live to be a
COMMENT field, not a post field. A comment from a stranger on an old post
must never be able to make a dormant page's last-post date look like today.

Also covers the deeper fix: even a key match on a POST-scoped name
(creation_time) isn't proof of a real post on its own -- _post_stamps()
additionally requires the live-confirmed `post_id` sibling.
"""

from __future__ import annotations

from backend.platforms.facebook.analysis_engine import (Harvest, K_POST_TIME,
                                                         _post_stamps,
                                                         read_last_post)
from backend.shared.models.row import Row
from backend.shared.text import find_ints


class TestKPostTimeExcludesCommentField:
    def test_created_time_is_not_in_the_key_set(self):
        assert "created_time" not in K_POST_TIME

    def test_creation_time_is_still_in_the_key_set(self):
        # confirmed live: appears alongside the post's own post_id, a
        # genuine post-scoped signal -- must not be removed alongside the
        # comment field just because the names look similar
        assert "creation_time" in K_POST_TIME


class TestRegexScanNoLongerLeaksCommentTimestamps:
    def test_a_comment_newer_than_the_real_post_is_not_picked_up(self):
        # shape matches the live-captured payload: a genuine post at
        # creation_time=1786178490 (2026-08-08), and a stranger's comment
        # on it three days later at created_time=1786353620 (2026-08-10)
        payload = (
            '{"post_id":"1522040459954509","creation_time":1786178490,'
            '"cix_screen":null}'
            '...'
            '{"__typename":"XFBCommentTimestampBadge","comment":'
            '{"created_time":1786353620,"url":"https://facebook.com/x"}}'
        )
        stamps = find_ints(payload, K_POST_TIME)
        assert 1786178490 in stamps  # the real post
        assert 1786353620 not in stamps  # the comment -- must not appear

    def test_without_the_fix_the_comment_would_have_won_as_max(self):
        # sanity: the comment timestamp really is the larger number, so
        # excluding it is doing real work, not a no-op
        post_ts = 1786178490
        comment_ts = 1786353620
        assert comment_ts > post_ts


class TestPostStampsRequiresThePostIdSibling:
    """Even a POST-scoped key name (creation_time) isn't proof on its own --
    _post_stamps() additionally requires the live-confirmed post_id sibling,
    which is what actually distinguishes a post from any other nested
    object Facebook's payload happens to carry a matching key on."""

    def test_a_dict_without_post_id_is_ignored_even_with_a_valid_key(self):
        roots = [{"creation_time": 1700000000, "cache_id": "x"}]  # no post_id
        assert _post_stamps(roots) == []

    def test_a_dict_with_post_id_is_accepted(self):
        roots = [{"post_id": "123", "creation_time": 1700000000}]
        assert _post_stamps(roots) == [1700000000]

    def test_finds_it_nested_arbitrarily_deep(self):
        roots = [{"a": {"b": [{"c": {"post_id": "1", "creation_time": 1700000000}}]}}]
        assert _post_stamps(roots) == [1700000000]

    def test_a_sibling_comment_object_is_not_picked_up(self):
        # the live-captured shape: a real post dict, and elsewhere in the
        # same subtree a comment dict that has no post_id of its own
        roots = [{
            "post_id": "123", "creation_time": 1700000000,
            "comments": [{"created_time": 1700099999}],  # comment: no post_id
        }]
        stamps = _post_stamps(roots)
        assert 1700000000 in stamps
        assert 1700099999 not in stamps


class TestReadLastPostEndToEnd:
    """read_last_post() against a realistic Harvest, exercising the full
    scoped -> unscoped fallback chain, not just the helper in isolation."""

    def test_scoped_ents_wins_over_unscoped_when_both_present(self):
        row = Row(url="u", target="Adani")
        h = Harvest()
        h.ents = [{"post_id": "1", "creation_time": 1700000000}]
        h.gql = [{"post_id": "2", "creation_time": 1650000000}]  # older, unscoped
        read_last_post(row, h)
        assert row.last_post_iso == _iso(1700000000)
        assert row.src.get("last_post") == "graphql"

    def test_falls_back_to_unscoped_gql_when_ents_empty(self):
        row = Row(url="u", target="Adani")
        h = Harvest()
        h.ents = []
        h.gql = [{"post_id": "2", "creation_time": 1650000000}]
        read_last_post(row, h)
        assert row.last_post_iso == _iso(1650000000)
        assert row.src.get("last_post") == "graphql-unscoped"

    def test_a_comment_never_wins_even_via_the_unscoped_fallback(self):
        row = Row(url="u", target="Adani")
        h = Harvest()
        h.ents = []
        h.gql = [
            {"post_id": "1", "creation_time": 1650000000},
            {"comments": [{"created_time": 1700000000}]},  # newer, but a comment
        ]
        read_last_post(row, h)
        assert row.last_post_iso == _iso(1650000000)

    def test_no_post_id_falls_through_to_the_ungated_regex_tier(self):
        # tier 1/2 (post_id-gated) find nothing here, but tier 3 (the
        # original text-regex scan, kept as a safety net -- see
        # read_last_post's docstring for why it was NOT removed) still
        # recovers a date rather than reporting nothing
        row = Row(url="u", target="Adani")
        h = Harvest()
        h.ents = []
        h.gql = [{"creation_time": 1700000000}]  # no post_id
        h.html = {}
        read_last_post(row, h)
        assert row.last_post_iso == _iso(1700000000)
        assert row.src.get("last_post") == "payload-regex-ungated"

    def test_created_time_is_excluded_even_in_the_ungated_tier(self):
        # the specific leak this whole rewrite closed must stay closed even
        # in the loosest fallback tier
        row = Row(url="u", target="Adani")
        h = Harvest()
        h.ents = []
        h.gql = [{"created_time": 1700000000}]  # comment field, no post_id
        h.html = {}
        read_last_post(row, h)
        assert row.last_post_iso == ""


def _iso(epoch: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()
