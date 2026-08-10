"""Instagram payload-parsing helpers (backend/platforms/instagram/
discovery_engine.py): _count, _latest_post, user_from_node, profile_from,
and the two search-result iterators. None had direct unit coverage --
last-post-date PINNING logic is already well tested
(test_instagram_pinned_last_post.py) but the underlying payload field
extraction these helpers do is not the same code and was untested.
"""

from __future__ import annotations

from backend.platforms.instagram.discovery_engine import (
    _count, _latest_post, iter_mobile_search_users, iter_search_users,
    profile_from, user_from_node)


class TestCount:
    def test_dict_with_count_key_form(self):
        assert _count({"edge_followed_by": {"count": 5000}}, "edge_followed_by") == 5000

    def test_bare_integer_form(self):
        assert _count({"follower_count": 5000}, "follower_count") == 5000

    def test_first_matching_key_wins(self):
        node = {"edge_followed_by": {"count": 100}, "follower_count": 200}
        assert _count(node, "edge_followed_by", "follower_count") == 100

    def test_falls_through_to_the_second_key_when_first_is_absent(self):
        node = {"follower_count": 200}
        assert _count(node, "edge_followed_by", "follower_count") == 200

    def test_no_matching_key_returns_none(self):
        assert _count({}, "edge_followed_by", "follower_count") is None

    def test_non_dict_node_returns_none_not_raises(self):
        assert _count("not a dict", "follower_count") is None

    def test_malformed_count_dict_without_int_count_is_ignored(self):
        assert _count({"follower_count": {"count": "not-an-int"}}, "follower_count") is None


class TestLatestPost:
    def test_picks_the_max_timestamp_among_several(self):
        node = {"edges": [
            {"node": {"taken_at_timestamp": 1700000000}},
            {"node": {"taken_at_timestamp": 1750000000}},
            {"node": {"taken_at_timestamp": 1720000000}},
        ]}
        assert _latest_post(node) == "2025-06-15"

    def test_taken_at_key_is_also_recognised(self):
        node = {"media": {"taken_at": 1700000000}}
        assert _latest_post(node) == "2023-11-14"

    def test_out_of_range_timestamps_are_ignored(self):
        # below 1_000_000_000 (before Instagram-plausible) or above
        # 4_000_000_000 (implausibly far future) must not win
        node = {"a": {"taken_at_timestamp": 500}, "b": {"taken_at_timestamp": 5_000_000_000}}
        assert _latest_post(node) == ""

    def test_no_timestamp_anywhere_returns_empty_string(self):
        assert _latest_post({"username": "adanigroup"}) == ""

    def test_non_integer_timestamp_is_ignored(self):
        node = {"media": {"taken_at_timestamp": "1700000000"}}  # string, not int
        assert _latest_post(node) == ""


class TestUserFromNode:
    def _node(self, **overrides) -> dict:
        base = {
            "id": "123456",
            "username": "adanigroup",
            "full_name": "Adani Group",
            "edge_followed_by": {"count": 50000},
            "edge_follow": {"count": 10},
            "edge_owner_to_timeline_media": {"count": 200},
            "profile_pic_url_hd": "https://instagram.com/hd.jpg",
            "profile_pic_url": "https://instagram.com/thumb.jpg",
            "biography": "Official account",
            "is_verified": True,
            "is_private": False,
        }
        base.update(overrides)
        return base

    def test_non_dict_input_returns_none(self):
        assert user_from_node("not a dict") is None
        assert user_from_node(None) is None

    def test_missing_username_returns_none(self):
        node = self._node()
        del node["username"]
        assert user_from_node(node) is None

    def test_blank_username_returns_none(self):
        assert user_from_node(self._node(username="")) is None

    def test_every_field_is_extracted(self):
        u = user_from_node(self._node())
        assert u.entity_id == "123456"
        assert u.username == "adanigroup"
        assert u.full_name == "Adani Group"
        assert u.followers == 50000
        assert u.following == 10
        assert u.posts == 200
        assert u.biography == "Official account"
        assert u.verified is True
        assert u.private is False

    def test_hd_avatar_preferred_over_thumbnail(self):
        u = user_from_node(self._node())
        assert u.avatar == "https://instagram.com/hd.jpg"

    def test_falls_back_to_thumbnail_when_hd_missing(self):
        node = self._node()
        del node["profile_pic_url_hd"]
        u = user_from_node(node)
        assert u.avatar == "https://instagram.com/thumb.jpg"

    def test_id_field_fallback_chain_pk(self):
        node = self._node()
        del node["id"]
        node["pk"] = "999"
        assert user_from_node(node).entity_id == "999"

    def test_id_field_fallback_chain_pk_id(self):
        node = self._node()
        del node["id"]
        node["pk_id"] = "888"
        assert user_from_node(node).entity_id == "888"

    def test_no_id_at_all_yields_empty_string_entity_id_not_none(self):
        node = self._node()
        del node["id"]
        assert user_from_node(node).entity_id == ""


class TestProfileFrom:
    def _profile_node(self, username: str, followers: int = 5000) -> dict:
        return {
            "username": username,
            "edge_followed_by": {"count": followers},
            "edge_owner_to_timeline_media": {"count": 10},
            "biography": "bio",
        }

    def test_finds_the_named_profile_among_other_mentions(self):
        blob = {
            "someone_else": {"username": "randomperson"},  # bare mention, no counts -- must be skipped
            "the_profile": self._profile_node("adanigroup"),
        }
        u = profile_from(blob, "adanigroup")
        assert u is not None
        assert u.username == "adanigroup"

    def test_bare_username_mention_with_no_counts_is_not_mistaken_for_the_profile(self):
        blob = {"tagged_user": {"username": "someone"}}  # no follower/media/bio keys at all
        assert profile_from(blob, "someone") is None

    def test_case_insensitive_username_match(self):
        blob = {"p": self._profile_node("AdaniGroup")}
        u = profile_from(blob, "adanigroup")
        assert u is not None and u.username == "AdaniGroup"

    def test_no_username_filter_returns_the_first_real_profile_node_found(self):
        blob = {"p": self._profile_node("adanigroup")}
        u = profile_from(blob)
        assert u is not None and u.username == "adanigroup"

    def test_prefers_a_candidate_with_known_followers_over_one_without(self):
        # both match the same username, but Instagram's OWN profile payload
        # is far more likely to carry real counts than an incidental mention
        blob = {
            "mention": {"username": "adanigroup", "biography": "seen elsewhere"},
            "real_profile": self._profile_node("adanigroup", followers=99999),
        }
        u = profile_from(blob, "adanigroup")
        assert u is not None and u.followers == 99999

    def test_no_matching_username_returns_none(self):
        blob = {"p": self._profile_node("adanigroup")}
        assert profile_from(blob, "someoneelse") is None


class TestSearchIterators:
    def test_iter_search_users_dedupes_by_username_case_insensitively(self):
        blob = {"users": [
            {"user": {"username": "adanigroup", "id": "1"}},
            {"user": {"username": "AdaniGroup", "id": "1"}},  # same person, different case
            {"user": {"username": "other", "id": "2"}},
        ]}
        users = list(iter_search_users(blob))
        assert [u.username for u in users] == ["adanigroup", "other"]

    def test_iter_search_users_skips_entries_with_no_user_key(self):
        blob = {"items": [{"not_a_user": {}}, {"user": {"username": "adanigroup", "id": "1"}}]}
        users = list(iter_search_users(blob))
        assert len(users) == 1

    def test_iter_mobile_search_users_reads_the_flat_users_array(self):
        blob = {"users": [
            {"username": "adanigroup", "id": "1"},
            {"username": "other", "id": "2"},
        ]}
        users = list(iter_mobile_search_users(blob))
        assert [u.username for u in users] == ["adanigroup", "other"]

    def test_iter_mobile_search_users_on_non_dict_blob_yields_nothing(self):
        assert list(iter_mobile_search_users("not a dict")) == []

    def test_iter_mobile_search_users_dedupes(self):
        blob = {"users": [
            {"username": "adanigroup", "id": "1"},
            {"username": "adanigroup", "id": "1"},
        ]}
        assert len(list(iter_mobile_search_users(blob))) == 1
