"""Facebook's search-payload parsing chain (backend/platforms/facebook/
discovery_engine.py): iter_results, page_state, _processed_ids,
is_search_response, parse_lines, parse_embedded. None had direct unit
coverage -- this is the whole path from a raw XHR/embedded-script response
to Hit objects and pagination state, independent of the last-post-date
logic that IS already tested elsewhere.
"""

from __future__ import annotations

import json

from backend.platforms.facebook.discovery_engine import (_processed_ids,
                                                            is_search_response,
                                                            iter_results,
                                                            page_state,
                                                            parse_embedded,
                                                            parse_lines)


def _search_edge(entity_id: str, name: str, typename: str = "User", rank: int = 0) -> dict:
    return {
        "rendering_strategy": {
            "view_model": {
                "__typename": "SearchProfileViewModel",
                "profile": {
                    "__typename": typename,
                    "id": entity_id,
                    "name": name,
                    "profile_url": f"https://www.facebook.com/profile.php?id={entity_id}",
                    "profile_picture": {"uri": "https://scontent.fbcdn.net/real_photo.jpg"},
                },
            }
        }
    }


class TestIterResults:
    def test_extracts_a_hit_from_a_valid_edge(self):
        blob = {"edges": [_search_edge("100012345", "Adani Group")]}
        hits = list(iter_results(blob))
        assert len(hits) == 1
        assert hits[0].entity_id == "100012345"
        assert hits[0].name == "Adani Group"
        assert hits[0].entity_type == "profile"

    def test_page_typename_maps_to_page_entity_type(self):
        blob = {"edges": [_search_edge("200012345", "Adani Cement", typename="Page")]}
        hits = list(iter_results(blob))
        assert hits[0].entity_type == "page"

    def test_group_typename_maps_to_group_entity_type(self):
        # live-confirmed (2026-08-11, a real search/groups/?q= response):
        # a Group result rides the EXACT SAME SearchProfileViewModel/
        # .profile shape as User/Page, just with __typename "Group" -- no
        # separate parsing branch, this is the whole difference
        blob = {"edges": [_search_edge("152272458887295", "Allu Arjun Fans", typename="Group")]}
        hits = list(iter_results(blob))
        assert hits[0].entity_type == "group"
        assert hits[0].entity_id == "152272458887295"

    def test_rank_follows_the_edges_array_index(self):
        blob = {"edges": [
            _search_edge("1", "First"), _search_edge("2", "Second"), _search_edge("3", "Third"),
        ]}
        hits = list(iter_results(blob))
        assert [h.rank for h in hits] == [0, 1, 2]

    def test_non_search_view_model_is_skipped(self):
        blob = {"edges": [{
            "rendering_strategy": {"view_model": {"__typename": "SomeOtherViewModel", "profile": {}}}
        }]}
        assert list(iter_results(blob)) == []

    def test_non_digit_entity_id_is_skipped(self):
        blob = {"edges": [_search_edge("not-a-number", "Adani Group")]}
        assert list(iter_results(blob)) == []

    def test_tracking_params_are_stripped_from_the_url(self):
        # the split is on the literal "?__" -- a vanity-URL hit whose query
        # string IS the tracking params (no other real param before them)
        edge = _search_edge("100012345", "Adani Group")
        edge["rendering_strategy"]["view_model"]["profile"]["profile_url"] = \
            "https://www.facebook.com/AdaniGroup?__tn__=%2Cd%2CP-R&__eep__=6"
        hits = list(iter_results({"edges": [edge]}))
        assert hits[0].url == "https://www.facebook.com/AdaniGroup"

    def test_default_avatar_is_not_reported_as_a_custom_pic(self):
        edge = _search_edge("100012345", "Adani Group")
        # RE_DEFAULT_PIC matches Facebook's own static-asset host for the
        # placeholder silhouette avatar
        edge["rendering_strategy"]["view_model"]["profile"]["profile_picture"] = {
            "uri": "https://static.xx.fbcdn.net/rsrc.php/v3/default_avatar.png"
        }
        hits = list(iter_results({"edges": [edge]}))
        assert hits[0].has_custom_pic is False
        assert hits[0].avatar == ""

    def test_no_profile_picture_at_all_is_not_a_custom_pic(self):
        edge = _search_edge("100012345", "Adani Group")
        del edge["rendering_strategy"]["view_model"]["profile"]["profile_picture"]
        hits = list(iter_results({"edges": [edge]}))
        assert hits[0].has_custom_pic is False

    def test_finds_edges_nested_arbitrarily_deep(self):
        blob = {"data": {"search_results": {"edges": [_search_edge("100012345", "Adani Group")]}}}
        hits = list(iter_results(blob))
        assert len(hits) == 1

    def test_no_edges_anywhere_yields_nothing(self):
        assert list(iter_results({"unrelated": "data"})) == []

    def test_url_falls_back_to_a_constructed_url_when_none_in_payload(self):
        edge = _search_edge("100012345", "Adani Group")
        del edge["rendering_strategy"]["view_model"]["profile"]["profile_url"]
        hits = list(iter_results({"edges": [edge]}))
        assert hits[0].url == "https://www.facebook.com/profile.php?id=100012345"


def _cursor_json(**overrides) -> str:
    base = {"result_ids_shown": ["1", "2", "3"], "page_number": 2}
    base.update(overrides)
    return json.dumps(base)


class TestPageState:
    def test_reads_has_next_page_and_ids_shown(self):
        blob = {"page_info": {"has_next_page": True, "end_cursor": _cursor_json()}}
        ps = page_state(blob)
        assert ps is not None
        assert ps.has_next is True
        assert ps.ids_shown == ["1", "2", "3"]

    def test_end_of_serp_flag_is_read(self):
        blob = {"page_info": {"has_next_page": False, "end_cursor": _cursor_json(is_end_of_serp=True)}}
        assert page_state(blob).end_of_serp is True

    def test_total_results_comes_from_unit_id_logging_fields(self):
        blob = {"page_info": {
            "has_next_page": True,
            "end_cursor": _cursor_json(unit_id_logging_fields={"num_total_results": 250}),
        }}
        assert page_state(blob).total_results == 250

    def test_a_connection_without_result_ids_shown_is_not_the_search_cursor(self):
        # e.g. the notification dropdown's own page_info -- must be ignored,
        # not mistaken for search pagination
        blob = {"page_info": {"has_next_page": True, "end_cursor": json.dumps({"something_else": 1})}}
        assert page_state(blob) is None

    def test_non_json_end_cursor_is_ignored_not_raised(self):
        blob = {"page_info": {"has_next_page": True, "end_cursor": "not-json-at-all"}}
        assert page_state(blob) is None

    def test_no_page_info_anywhere_returns_none(self):
        assert page_state({"unrelated": "data"}) is None

    def test_page_info_missing_has_next_page_key_is_skipped(self):
        blob = {"page_info": {"end_cursor": _cursor_json()}}
        assert page_state(blob) is None

    def test_ids_are_coerced_to_strings(self):
        blob = {"page_info": {"has_next_page": True, "end_cursor": _cursor_json(result_ids_shown=[1, 2, 3])}}
        assert page_state(blob).ids_shown == ["1", "2", "3"]


class TestProcessedIds:
    def test_top_level_processed_unicorn_ids(self):
        cursor = {"processed_unicorn_ids": ["10", "20"]}
        assert _processed_ids(cursor) == ["10", "20"]

    def test_ids_inside_flow_cursors_serialized_json_strings(self):
        cursor = {"flow_cursors_serialized": {
            "some_flow": json.dumps({"processed_unicorn_ids": ["30", "40"]}),
        }}
        assert _processed_ids(cursor) == ["30", "40"]

    def test_both_sources_are_combined(self):
        cursor = {
            "processed_unicorn_ids": ["10"],
            "flow_cursors_serialized": {"f": json.dumps({"processed_unicorn_ids": ["20"]})},
        }
        assert sorted(_processed_ids(cursor)) == ["10", "20"]

    def test_malformed_flow_cursor_json_is_skipped_not_raised(self):
        cursor = {"flow_cursors_serialized": {"f": "not valid json"}}
        assert _processed_ids(cursor) == []

    def test_flow_cursor_string_without_the_marker_is_skipped_entirely(self):
        # the function only bothers parsing a flow cursor string if it
        # literally contains "processed_unicorn_ids" -- a cheap pre-filter
        cursor = {"flow_cursors_serialized": {"f": json.dumps({"other_field": 1})}}
        assert _processed_ids(cursor) == []

    def test_empty_cursor_returns_empty_list(self):
        assert _processed_ids({}) == []


class TestIsSearchResponse:
    def test_matching_query_name_is_a_search_response(self):
        assert is_search_response('{"query_name":"SearchCometResultsPaginatedResultsQuery"}') is True

    def test_unrelated_body_is_not(self):
        assert is_search_response('{"query_name":"SomeOtherQuery"}') is False

    def test_empty_or_none_body_is_not(self):
        assert is_search_response("") is False
        assert is_search_response(None) is False


class TestParseLines:
    def test_parses_each_json_line(self):
        text = '{"a": 1}\n{"b": 2}\n'
        assert list(parse_lines(text)) == [{"a": 1}, {"b": 2}]

    def test_non_json_lines_are_skipped(self):
        text = 'not json\n{"a": 1}\nalso not json'
        assert list(parse_lines(text)) == [{"a": 1}]

    def test_lines_not_starting_with_a_brace_are_skipped_even_if_valid_json_elsewhere(self):
        text = '[1, 2, 3]\n{"a": 1}'
        assert list(parse_lines(text)) == [{"a": 1}]

    def test_whitespace_around_a_line_is_tolerated(self):
        text = '   {"a": 1}   \n'
        assert list(parse_lines(text)) == [{"a": 1}]

    def test_empty_text_yields_nothing(self):
        assert list(parse_lines("")) == []


class TestParseEmbedded:
    def test_only_texts_mentioning_searchprofileviewmodel_are_considered(self):
        texts = ['{"foo": "bar"}', '{"SearchProfileViewModel": true}']
        assert list(parse_embedded(texts)) == [{"SearchProfileViewModel": True}]

    def test_text_not_starting_with_a_brace_is_skipped(self):
        texts = ['var x = {"SearchProfileViewModel": true}']  # doesn't START with {
        assert list(parse_embedded(texts)) == []

    def test_leading_whitespace_before_the_brace_is_tolerated(self):
        texts = ['   {"SearchProfileViewModel": true}']
        assert list(parse_embedded(texts)) == [{"SearchProfileViewModel": True}]

    def test_malformed_json_is_skipped_not_raised(self):
        texts = ['{"SearchProfileViewModel": not valid json}']
        assert list(parse_embedded(texts)) == []

    def test_none_and_empty_entries_in_the_list_are_skipped(self):
        texts = [None, "", '{"SearchProfileViewModel": true}']
        assert list(parse_embedded(texts)) == [{"SearchProfileViewModel": True}]

    def test_none_input_yields_nothing(self):
        assert list(parse_embedded(None)) == []
