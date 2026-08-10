"""Facebook's per-field readers (backend/platforms/facebook/analysis_engine.py):
read_name, take_chip, read_counts, read_created. Each is a pure function of
(Row, Harvest) per the module's own comment ("no browser, no network --
that is what makes them testable against a saved payload"), yet none had a
test. These are the multi-tier graphql -> dom -> text-regex fallback chains
that decide what actually lands in the Name/Followers/Created-Date columns.
"""

from __future__ import annotations

from backend.platforms.facebook.analysis_engine import (
    Harvest, read_counts, read_created, read_name, take_chip)
from backend.shared.models.row import Row


def _row(target: str = "Adani") -> Row:
    return Row(url="https://www.facebook.com/x", target=target)


def _harvest(ents=None, dom=None, html=None, text=None, gql=None) -> Harvest:
    h = Harvest()
    h.ents = ents or []
    h.dom = dom or {}
    h.html = html or {}
    h.text = text or {}
    h.gql = gql or []
    return h


class TestReadName:
    def test_graphql_entity_name_wins_when_present(self):
        row, h = _row(), _harvest(ents=[{"id": "1", "name": "Adani Group"}])
        read_name(row, h)
        assert row.profile_name == "Adani Group"
        assert row.src.get("name") == "graphql"

    def test_falls_back_to_dom_header_name_when_graphql_absent(self):
        row, h = _row(), _harvest(dom={"name": "Adani Group"})
        read_name(row, h)
        assert row.profile_name == "Adani Group"
        assert row.src.get("name") == "dom-header"

    def test_falls_back_to_dom_post_author_next(self):
        row, h = _row(), _harvest(dom={"postAuthor": "Adani Group"})
        read_name(row, h)
        assert row.profile_name == "Adani Group"
        assert row.src.get("name") == "dom-post-label"

    def test_og_title_is_read_and_facebook_suffix_stripped(self):
        html = {"main": '<meta property="og:title" content="Adani Group | Facebook">'}
        row, h = _row(), _harvest(html=html)
        read_name(row, h)
        assert row.profile_name == "Adani Group"
        assert row.src.get("name") == "og:title"

    def test_title_tag_strips_leading_unread_count(self):
        html = {"main": "<title>(3) Adani Group</title>"}
        row, h = _row(), _harvest(html=html)
        read_name(row, h)
        assert row.profile_name == "Adani Group"
        assert row.src.get("name") == "title-tag"

    def test_generic_facebook_title_is_rejected_as_untrusted(self):
        # a <title> reading "Facebook" is the browser tab, not a real name --
        # GENERIC_NAMES filters it out even though nothing else answered
        html = {"main": "<title>Facebook</title>"}
        row, h = _row(), _harvest(html=html)
        read_name(row, h)
        assert row.profile_name == ""

    def test_graphql_source_is_trusted_even_for_a_generic_looking_string(self):
        # trusted=True sources skip the GENERIC_NAMES filter entirely --
        # only og:title/title-tag are marked untrusted
        row, h = _row(), _harvest(ents=[{"id": "1", "name": "Facebook"}])
        read_name(row, h)
        assert row.profile_name == "Facebook"

    def test_graphql_loose_is_the_final_fallback(self):
        row, h = _row(), _harvest(gql=[{"page_name": "Adani Group"}])
        read_name(row, h)
        assert row.profile_name == "Adani Group"
        assert row.src.get("name") == "graphql-loose"

    def test_name_score_is_computed_against_the_row_target(self):
        row, h = _row(target="Adani"), _harvest(ents=[{"id": "1", "name": "Adani Group"}])
        read_name(row, h)
        assert row.name_score is not None and row.name_score > 0

    def test_no_source_answers_leaves_profile_name_blank(self):
        row, h = _row(), _harvest()
        read_name(row, h)
        assert row.profile_name == ""

    def test_earlier_tier_wins_over_a_later_one_when_both_present(self):
        row, h = _row(), _harvest(
            ents=[{"id": "1", "name": "Real Name From Graphql"}],
            dom={"name": "Dom Name"},
        )
        read_name(row, h)
        assert row.profile_name == "Real Name From Graphql"


class TestTakeChip:
    def test_plain_followers_count_is_exact(self):
        row = _row()
        take_chip(row, "1,234 followers", "test")
        assert row.followers == 1234
        assert row.followers_exact == "yes"

    def test_abbreviated_followers_count_is_not_exact(self):
        row = _row()
        take_chip(row, "154M followers", "test")
        assert row.followers == 154_000_000
        assert row.followers_exact == "no"

    def test_friends_chip_sets_friends_not_followers(self):
        row = _row()
        take_chip(row, "53 friends", "test")
        assert row.friends == 53
        assert row.followers is None

    def test_following_chip_is_ignored_entirely(self):
        row = _row()
        take_chip(row, "12 following", "test")
        assert row.followers is None
        assert row.friends is None

    def test_likes_chip_is_treated_as_followers_with_a_note(self):
        row = _row()
        take_chip(row, "1.2K likes", "test")
        assert row.followers == 1200
        assert "publishes likes" in row.notes

    def test_already_set_followers_is_never_overwritten(self):
        row = _row()
        row.followers = 999
        take_chip(row, "154M followers", "test")
        assert row.followers == 999

    def test_already_set_friends_is_never_overwritten(self):
        row = _row()
        row.friends = 5
        take_chip(row, "53 friends", "test")
        assert row.friends == 5

    def test_unparseable_chip_text_is_a_no_op(self):
        row = _row()
        take_chip(row, "not a real chip at all", "test")
        assert row.followers is None
        assert row.friends is None

    def test_rounded_count_adds_a_note(self):
        row = _row()
        take_chip(row, "2K followers", "test")
        assert "rounded" in row.notes

    def test_exact_count_adds_no_rounded_note(self):
        row = _row()
        take_chip(row, "2000 followers", "test")
        assert "rounded" not in row.notes


class TestReadCounts:
    def test_ent_social_chips_are_tried_first(self):
        ents = [{"id": "1", "profile_social_context": {
            "content": [{"text": {"text": "5000 followers"}}]
        }}]
        row, h = _row(), _harvest(ents=ents)
        read_counts(row, h)
        assert row.followers == 5000
        assert row.src.get("followers") == "graphql-social-context"

    def test_dom_counter_line_is_split_on_separators_and_each_part_tried(self):
        row, h = _row(), _harvest(dom={"counter": "5,000 followers • 12 following"})
        read_counts(row, h)
        assert row.followers == 5000

    def test_ent_ints_tier_used_when_no_chip_source_answered(self):
        ents = [{"id": "1", "follower_count": 8000}]
        row, h = _row(), _harvest(ents=ents)
        read_counts(row, h)
        assert row.followers == 8000
        assert row.followers_exact == "yes"
        assert row.src.get("followers") == "graphql"

    def test_gql_ints_tier_used_when_ent_ints_empty(self):
        row, h = _row(), _harvest(gql=[{"fan_count": 9000}])
        read_counts(row, h)
        assert row.followers == 9000
        assert row.src.get("followers") == "graphql-loose"

    def test_page_text_regex_is_the_final_fallback(self):
        row, h = _row(), _harvest(text={"main": "Adani Group\n123,456 followers\nAbout"})
        read_counts(row, h)
        assert row.followers == 123456
        assert row.src.get("followers") == "page-text"

    def test_already_set_followers_short_circuits_before_ent_ints(self):
        ents = [{"id": "1", "follower_count": 111}]
        row, h = _row(), _harvest(ents=ents)
        row.followers = 999
        read_counts(row, h)
        assert row.followers == 999

    def test_max_of_multiple_ent_int_candidates_is_used(self):
        ents = [{"id": "1", "follower_count": 100, "fan_count": 500}]
        row, h = _row(), _harvest(ents=ents)
        read_counts(row, h)
        assert row.followers == 500

    def test_out_of_range_values_are_excluded_from_ent_ints(self):
        ents = [{"id": "1", "follower_count": -5}]
        row, h = _row(), _harvest(ents=ents)
        read_counts(row, h)
        assert row.followers is None

    def test_nothing_found_anywhere_leaves_followers_none(self):
        row, h = _row(), _harvest()
        read_counts(row, h)
        assert row.followers is None


class TestReadCreated:
    def test_ent_strs_joined_date_is_parsed(self):
        ents = [{"id": "1", "joined_date": "June 2020"}]
        row, h = _row(), _harvest(ents=ents)
        read_created(row, h)
        assert row.created_iso == "2020-06"

    def test_falls_back_to_epoch_int_when_no_string_form(self):
        ents = [{"id": "1", "profile_creation_time": 1590000000}]
        row, h = _row(), _harvest(ents=ents)
        read_created(row, h)
        assert row.created_iso  # a real ISO date was produced
        assert row.created_iso.startswith("2020-")

    def test_unparseable_string_falls_through_to_the_int_tier(self):
        ents = [{
            "id": "1",
            "joined_date": "not a real date",
            "profile_creation_time": 1590000000,
        }]
        row, h = _row(), _harvest(ents=ents)
        read_created(row, h)
        assert row.created_iso.startswith("2020-")

    def test_no_joined_field_anywhere_leaves_created_iso_blank(self):
        row, h = _row(), _harvest()
        read_created(row, h)
        assert row.created_iso == ""
