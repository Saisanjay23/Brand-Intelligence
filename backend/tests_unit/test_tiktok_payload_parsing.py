"""TikTok payload-parsing helpers (backend/platforms/tiktok/
discovery_engine.py): user_from_node, iter_users, profile_from, and the
video-id-encodes-a-timestamp decoding these engines depend on for
last-post-date. Pure functions of a parsed payload, no browser needed.
"""

from __future__ import annotations

from backend.platforms.tiktok.discovery_engine import (
    _epoch_to_iso, _video_id_to_iso, iter_users, newest_post_iso, profile_from, user_from_node,
)


class TestUserFromNode:
    def test_nested_stats_dict(self):
        u = user_from_node(
            {"uniqueId": "brandofficial", "nickname": "Brand Official",
             "avatarLarger": "https://p.tiktok.com/a.jpg", "signature": "hi",
             "verified": True},
            {"followerCount": 5000, "followingCount": 12, "heartCount": 99000, "videoCount": 40},
        )
        assert u is not None
        assert u.username == "brandofficial"
        assert u.nickname == "Brand Official"
        assert u.follower_count == 5000
        assert u.following_count == 12
        assert u.heart_count == 99000
        assert u.video_count == 40
        assert u.verified is True
        assert u.url == "https://www.tiktok.com/@brandofficial"

    def test_stats_merged_into_user_dict(self):
        u = user_from_node({"uniqueId": "someone", "followerCount": "1234"})
        assert u is not None
        # string counts must coerce to int, not stay a string or drop
        assert u.follower_count == 1234

    def test_no_username_returns_none(self):
        assert user_from_node({"nickname": "no handle"}) is None

    def test_non_dict_node_returns_none_not_raises(self):
        assert user_from_node("not a dict") is None

    def test_private_and_default_flags(self):
        u = user_from_node({"uniqueId": "x", "privateAccount": True})
        assert u is not None
        assert u.private is True

    def test_malformed_count_is_ignored_not_raised(self):
        u = user_from_node({"uniqueId": "x", "followerCount": "not-a-number"})
        assert u is not None
        assert u.follower_count is None

    def test_has_custom_pic_true_whenever_any_avatar_present(self):
        u = user_from_node({"uniqueId": "x", "avatarLarger": "https://p.tiktok.com/a.jpg"})
        assert u is not None
        assert u.has_custom_pic is True

    def test_has_custom_pic_false_with_no_avatar(self):
        u = user_from_node({"uniqueId": "x"})
        assert u is not None
        assert u.has_custom_pic is False


class TestIterUsers:
    def test_nested_user_stats_shape(self):
        blob = {"userInfo": {"user": {"uniqueId": "a"}, "stats": {"followerCount": 10}}}
        users = list(iter_users(blob))
        assert len(users) == 1
        assert users[0].username == "a"
        assert users[0].follower_count == 10

    def test_flat_shape_older_sigi_state(self):
        blob = {"UserModule": {"users": {"a": {"uniqueId": "a", "followerCount": 5}}}}
        users = list(iter_users(blob))
        assert len(users) == 1
        assert users[0].username == "a"

    def test_dedupes_by_username_case_insensitively(self):
        blob = [
            {"user": {"uniqueId": "Same"}, "stats": {}},
            {"user": {"uniqueId": "same"}, "stats": {}},
        ]
        assert len(list(iter_users(blob))) == 1

    def test_no_users_in_payload_yields_nothing(self):
        assert list(iter_users({"unrelated": {"a": 1}})) == []

    def test_search_result_list_shape(self):
        blob = {"user_list": [
            {"user_info": {"uniqueId": "u1"}},
            {"user": {"uniqueId": "u2"}, "stats": {"followerCount": 99}},
        ]}
        usernames = {u.username for u in iter_users(blob)}
        assert "u2" in usernames


class TestProfileFrom:
    def test_matches_requested_username_case_insensitively(self):
        blob = {"user": {"uniqueId": "BrandOfficial"}, "stats": {}}
        u = profile_from(blob, "brandofficial")
        assert u is not None
        assert u.username == "BrandOfficial"

    def test_returns_none_when_username_not_present(self):
        blob = {"user": {"uniqueId": "someone-else"}, "stats": {}}
        assert profile_from(blob, "brandofficial") is None

    def test_no_username_filter_returns_first_user_found(self):
        blob = {"user": {"uniqueId": "first"}, "stats": {}}
        u = profile_from(blob)
        assert u is not None
        assert u.username == "first"


class TestVideoIdToIso:
    def test_decodes_a_plausible_snowflake_id(self):
        # 7300000000000000000 was a real-shaped TikTok video id as of 2023;
        # just needs to decode to a plausible, non-empty date in range
        iso = _video_id_to_iso(7300000000000000000)
        assert iso != ""
        assert iso.startswith(("202",))

    def test_non_numeric_id_returns_empty_string_not_raises(self):
        assert _video_id_to_iso("not-a-number") == ""
        assert _video_id_to_iso(None) == ""

    def test_implausible_decoded_year_returns_empty_string(self):
        # a small id decodes to a pre-2016 (pre-TikTok) timestamp
        assert _video_id_to_iso(12345) == ""


class TestNewestPostIso:
    def test_picks_the_newest_of_several_video_nodes(self):
        blob = {"itemList": [
            {"id": 7100000000000000000, "desc": "old", "author": {"uniqueId": "brand"}},
            {"id": 7300000000000000000, "desc": "new", "author": {"uniqueId": "brand"}},
        ]}
        iso = newest_post_iso(blob, "brand")
        assert iso != ""
        assert iso == _video_id_to_iso(7300000000000000000)

    def test_scoped_to_requested_username_only(self):
        blob = {"itemList": [
            {"id": 7300000000000000000, "desc": "not this one", "author": {"uniqueId": "someone-else"}},
        ]}
        assert newest_post_iso(blob, "brand") == ""

    def test_ignores_non_video_nodes_that_merely_have_an_id_key(self):
        blob = {"id": 7300000000000000000, "unrelated": True}
        assert newest_post_iso(blob) == ""

    def test_empty_blob_returns_empty_string(self):
        assert newest_post_iso({}) == ""


class TestEpochToIso:
    def test_decodes_a_plausible_epoch_seconds_value(self):
        # 1681774947 -> a real createTime captured live, 2023-04-17 UTC
        assert _epoch_to_iso(1681774947) == "2023-04-17"

    def test_string_epoch_coerces_like_the_int_form(self):
        assert _epoch_to_iso("1681774947") == _epoch_to_iso(1681774947)

    def test_non_numeric_value_returns_empty_string_not_raises(self):
        assert _epoch_to_iso("not-a-number") == ""
        assert _epoch_to_iso(None) == ""

    def test_zero_or_falsy_returns_empty_string(self):
        assert _epoch_to_iso(0) == ""

    def test_implausible_pre_launch_epoch_returns_empty_string(self):
        assert _epoch_to_iso(1) == ""  # 1970, long before TikTok existed
