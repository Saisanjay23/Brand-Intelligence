"""Parent/child keyword groups: children are SEARCHED, parents are MATCHED.

The whole feature rests on keeping those two jobs apart (see
backend/shared/keywords.py), and on the back-compat guarantee that a client
with no children configured behaves exactly as it did before groups
existed. Both are asserted here.
"""

from __future__ import annotations

import pytest

from backend.shared import keywords as kw
from backend.shared.text import contiguous_letters_match, name_score


NEW_CLIENT = {
    "keyword_groups": {
        "individual": [
            {"parent": "Gautam Adani",
             "children": ["gautamadani", "adani gautam", "gautam.adani.hq"]},
        ],
        "domain": [
            {"parent": "Adani", "children": ["adani group", "adanigroup"]},
        ],
    },
    "asset_name_individual_keywords": ["Adani Group"],
    "asset_name_domain_keywords": [],
    "name_keywords": ["Gautam Adani"],
    "domain_keywords": ["Adani"],
}

LEGACY_CLIENT = {
    "name_keywords": ["Gautam Adani", "Pranav Adani"],
    "domain_keywords": ["Adani"],
}


class TestBackCompat:
    """A client saved before this feature must sweep identically."""

    def test_a_legacy_client_searches_its_own_keywords(self):
        plans = kw.build_plans(LEGACY_CLIENT)
        assert [p.search for p in plans] == ["Gautam Adani", "Pranav Adani", "Adani"]

    def test_each_legacy_keyword_is_its_own_parent(self):
        for plan in kw.build_plans(LEGACY_CLIENT):
            assert plan.search == plan.parent

    def test_legacy_keyword_types_are_preserved(self):
        by_search = {p.search: p.kw_type for p in kw.build_plans(LEGACY_CLIENT)}
        assert by_search["Gautam Adani"] == kw.INDIVIDUAL
        assert by_search["Adani"] == kw.DOMAIN

    def test_a_childless_parent_in_a_new_style_group_also_searches_itself(self):
        client = {"keyword_groups": {"individual": [{"parent": "Solo", "children": []}]}}
        plans = kw.build_plans(client)
        assert [(p.search, p.parent) for p in plans] == [("Solo", "Solo")]


class TestAllKeywordsAreSearched:
    def test_both_parent_and_children_are_searched(self):
        searched = [p.search for p in kw.build_plans(NEW_CLIENT)]
        assert searched == [
            "Gautam Adani", "gautamadani", "adani gautam", "gautam.adani.hq",
            "Adani", "adani group", "adanigroup",
        ]

    def test_parent_and_children_roll_up_to_their_own_parent(self):
        by_search = {p.search: p.parent for p in kw.build_plans(NEW_CLIENT)}
        assert by_search["Gautam Adani"] == "Gautam Adani"
        assert by_search["gautamadani"] == "Gautam Adani"
        assert by_search["gautam.adani.hq"] == "Gautam Adani"
        assert by_search["Adani"] == "Adani"
        assert by_search["adanigroup"] == "Adani"


class TestScoringUsesTheParentNotTheSearchTerm:
    def _plan(self, search: str) -> kw.KeywordPlan:
        return next(p for p in kw.build_plans(NEW_CLIENT) if p.search == search)

    def test_a_real_impersonator_scores_against_the_parent(self):
        """The regression this whole split exists to prevent: scoring
        "Gautam Adani Official" against the permutation "gautam.adani.hq"
        that surfaced it rates a genuine impersonator far lower than
        scoring it against the real name."""
        plan = self._plan("gautam.adani.hq")
        parent, score = kw.resolve_parent(plan, "Gautam Adani Official", name_score)
        assert parent == "Gautam Adani"
        assert score == 100
        # and it really is better than scoring against the search term
        assert score > name_score("Gautam Adani Official", "gautam.adani.hq")

    def test_an_asset_name_also_lifts_the_score(self):
        """An asset name is the other name the same entity is known by, so
        a profile matching it is a match -- but it is still FILED under the
        parent, not under the asset name."""
        plan = self._plan("gautamadani")
        parent, score = kw.resolve_parent(plan, "Adani Group Official", name_score)
        assert parent == "Gautam Adani"
        assert score == 100

    def test_an_unrelated_profile_still_scores_low(self):
        plan = self._plan("gautamadani")
        _, score = kw.resolve_parent(plan, "Totally Unrelated Person", name_score)
        assert score < 50

    def test_exact_run_checks_every_match_term(self):
        plan = self._plan("gautam.adani.hq")
        assert kw.match_any(plan, "Gautam Adani Fanpage", contiguous_letters_match)
        assert not kw.match_any(plan, "Unrelated Handle", contiguous_letters_match)


class TestSharedChildBetweenTwoParents:
    """One permutation listed under two parents is ONE search (running it
    twice costs a real page load and risks the session), and each hit is
    filed under whichever parent it actually resembles."""

    CLIENT = {
        "keyword_groups": {
            "individual": [
                {"parent": "Gautam Adani", "children": ["adani"]},
                {"parent": "Pranav Adani", "children": ["adani"]},
            ],
            "domain": [],
        },
    }

    def test_it_is_searched_only_once(self):
        plans = kw.build_plans(self.CLIENT)
        assert [p.search for p in plans] == ["Gautam Adani", "adani", "Pranav Adani"]

    def test_the_plan_carries_both_parents(self):
        plan = next(p for p in kw.build_plans(self.CLIENT) if p.search == "adani")
        assert {t.parent for t in plan.targets} == {"Gautam Adani", "Pranav Adani"}

    def test_each_hit_is_filed_under_the_parent_it_resembles(self):
        plan = next(p for p in kw.build_plans(self.CLIENT) if p.search == "adani")
        assert kw.resolve_parent(plan, "Gautam Adani Official", name_score)[0] == "Gautam Adani"
        assert kw.resolve_parent(plan, "Pranav Adani Official", name_score)[0] == "Pranav Adani"


class TestNormalization:
    def test_a_blank_parent_drops_the_whole_group(self):
        groups = kw.normalize_groups({"individual": [{"parent": "  ", "children": ["x"]}]})
        assert groups[kw.INDIVIDUAL] == []

    def test_a_child_equal_to_its_parent_is_dropped(self):
        """A childless parent already searches itself, so keeping it as a
        child too would search the same term twice."""
        groups = kw.normalize_groups(
            {"individual": [{"parent": "Acme", "children": ["Acme", "acme corp"]}]})
        assert groups[kw.INDIVIDUAL][0]["children"] == ["acme corp"]

    def test_duplicate_parents_collapse(self):
        groups = kw.normalize_groups(
            {"individual": [{"parent": "Acme", "children": []},
                            {"parent": "ACME", "children": ["x"]}]})
        assert len(groups[kw.INDIVIDUAL]) == 1

    def test_malformed_input_never_raises(self):
        for bad in (None, [], "nonsense", {"individual": "not-a-list"},
                    {"individual": [None, 42, {"no_parent": 1}]}):
            groups = kw.normalize_groups(bad)
            assert groups == {kw.INDIVIDUAL: [], kw.DOMAIN: []}

    def test_children_are_deduped_case_insensitively(self):
        groups = kw.normalize_groups(
            {"domain": [{"parent": "A", "children": ["x", "X", " x "]}]})
        assert groups[kw.DOMAIN][0]["children"] == ["x"]


class TestFlatListsStayDerived:
    """`name_keywords`/`domain_keywords` must remain exactly the parents --
    every pre-existing consumer reads them and would break otherwise."""

    def test_flat_lists_are_the_parents(self):
        flat = kw.flat_keywords(kw.groups_for_client(NEW_CLIENT))
        assert flat["name_keywords"] == ["Gautam Adani"]
        assert flat["domain_keywords"] == ["Adani"]

    def test_groups_win_over_stale_flat_lists(self):
        """If a document somehow carries both and they disagree, the groups
        are authoritative -- that is what `upsert` writes from."""
        client = {
            "keyword_groups": {"individual": [{"parent": "Real", "children": []}], "domain": []},
            "name_keywords": ["Stale"],
        }
        assert kw.parents_of(kw.groups_for_client(client), kw.INDIVIDUAL) == ["Real"]


class TestRequestedScoping:
    def test_requesting_one_parent_sweeps_parent_and_its_children(self):
        plans = kw.build_plans(NEW_CLIENT, ["Adani"])
        assert [p.search for p in plans] == ["Adani", "adani group", "adanigroup"]

    def test_an_unknown_requested_term_is_still_swept(self):
        """An ad-hoc one-off search for something not in the client's config
        must not silently sweep nothing."""
        plans = kw.build_plans(NEW_CLIENT, ["brand new term"])
        assert [(p.search, p.parent) for p in plans] == [("brand new term", "brand new term")]

    def test_an_unknown_term_classifies_like_the_existing_rule(self):
        """Mirrors discovery_service._is_individual_keyword: individual when
        it is one of the client's individual asset names, domain otherwise."""
        assert kw.build_plans(NEW_CLIENT, ["Adani Group"])[0].kw_type == kw.INDIVIDUAL
        assert kw.build_plans(NEW_CLIENT, ["something else"])[0].kw_type == kw.DOMAIN

    def test_no_request_sweeps_everything(self):
        assert len(kw.build_plans(NEW_CLIENT)) == 7


@pytest.mark.parametrize("client", [None, {}, {"keyword_groups": {}}])
def test_an_empty_client_yields_no_plans(client):
    assert kw.build_plans(client) == []


class TestContiguousHandlesStayHighMatch:
    """The regression that motivated the High-Match lift in
    discovery_service._hit_to_fields.

    `name_score` is token-based, so a run-together impersonator handle
    shares no whole word with the name it copies and scores ZERO against
    it. The results grid's High/Medium/Low filter bands on `name_score`
    alone, so without the lift the most obvious impersonator -- the one
    that just deleted the space -- would file as LOW.
    """

    from backend.shared.models.scoring import NAME_THRESHOLD as _T

    CLIENT = {
        "keyword_groups": {
            "individual": [{"parent": "Gautam Adani", "children": ["gautamadani"]}],
            "domain": [],
        },
    }

    def _fields(self, display_name: str) -> dict:
        from types import SimpleNamespace

        from backend.services.discovery_service import _hit_to_fields

        plan = kw.build_plans(self.CLIENT)[0]
        hit = SimpleNamespace(
            url="https://instagram.com/x", entity_id="e1", name=display_name,
            entity_type="profile", keyword=plan.search, tab="people",
            source="api", avatar="", has_custom_pic=False, verified=False,
        )
        return _hit_to_fields(hit, "instagram", plan)

    def test_the_bare_token_score_really_is_zero(self):
        """Guards the premise -- if this ever stops being 0 the lift below
        is no longer load-bearing and should be re-examined, not silently
        kept."""
        assert name_score("gautamadani", "Gautam Adani") == 0

    def test_a_spaceless_handle_still_files_as_high(self):
        fields = self._fields("gautamadani")
        assert fields["name_exact_run"] is True
        assert fields["name_score"] >= self._T

    def test_a_run_together_name_with_extra_words_files_as_high(self):
        fields = self._fields("GautamAdaniOfficial")
        assert fields["name_exact_run"] is True
        assert fields["name_score"] >= self._T

    def test_the_lift_never_rescues_a_non_matching_name(self):
        """It can only raise a hit that already contains the full keyword
        letter-run; a typo-squat has no contiguous run and keeps its own
        (low) token score."""
        fields = self._fields("Gawtam Kumar")
        assert fields["name_exact_run"] is False
        assert fields["name_score"] < self._T

    def test_an_unrelated_name_stays_at_zero(self):
        fields = self._fields("Completely Unrelated")
        assert fields["name_exact_run"] is False
        assert fields["name_score"] == 0

    def test_a_reordered_name_is_high_on_its_token_score_alone(self):
        """No contiguous run, but the same words -- already High before the
        lift existed, and must stay that way."""
        fields = self._fields("Adani Gautam")
        assert fields["name_exact_run"] is False
        assert fields["name_score"] >= self._T
