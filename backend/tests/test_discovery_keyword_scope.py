"""Scoping a sweep to ONE keyword category (individual / domain / both).

Both is the default and must stay the behaviour of every caller that
predates the option -- the round-robin engine and the scheduler both go
through the same entry point and neither sends a scope.
"""

from __future__ import annotations

import pytest

from backend.controllers.discovery_controller import _validated_keyword_type
from backend.dto.discovery_dto import DiscoveryIn
from backend.shared import keywords as kw
from backend.shared.errors import ValidationError


CLIENT = {
    "keyword_groups": {
        "individual": [
            {"parent": "Gautam Adani", "children": ["gautamadani", "adani gautam"]},
            {"parent": "Pranav Adani", "children": ["pranavadani"]},
        ],
        "domain": [
            {"parent": "Adani", "children": ["adani group", "adanigroup"]},
        ],
    },
    "name_keywords": ["Gautam Adani", "Pranav Adani"],
    "domain_keywords": ["Adani"],
}


def _scoped(scope: str) -> list[str]:
    """The search terms `run_discovery` would be left with for `scope` --
    the same one-line filter it applies, kept in step with it by
    test_the_service_applies_exactly_this_filter below."""
    plans = kw.build_plans(CLIENT)
    if scope:
        plans = [p for p in plans if p.kw_type == scope]
    return [p.search for p in plans]


class TestTheDefaultIsBoth:
    def test_an_omitted_scope_is_none(self):
        assert DiscoveryIn(client_id="c1", keywords=["x"]).keyword_type is None

    def test_no_scope_sweeps_every_category(self):
        assert _scoped("") == [
            "Gautam Adani", "gautamadani", "adani gautam",
            "Pranav Adani", "pranavadani",
            "Adani", "adani group", "adanigroup",
        ]


class TestScoping:
    def test_individual_only_drops_the_domain_terms(self):
        terms = _scoped(kw.INDIVIDUAL)
        assert terms == [
            "Gautam Adani", "gautamadani", "adani gautam",
            "Pranav Adani", "pranavadani",
        ]
        assert "adanigroup" not in terms

    def test_domain_only_drops_the_individual_terms(self):
        terms = _scoped(kw.DOMAIN)
        assert terms == ["Adani", "adani group", "adanigroup"]
        assert "gautamadani" not in terms

    def test_the_two_scopes_partition_the_unscoped_set(self):
        """Nothing is lost between them and nothing is swept twice."""
        both = _scoped("")
        assert sorted(_scoped(kw.INDIVIDUAL) + _scoped(kw.DOMAIN)) == sorted(both)


class TestValidation:
    @pytest.mark.parametrize("value", ["individual", "domain", "INDIVIDUAL", "  Domain  "])
    def test_a_real_category_is_accepted_and_normalised(self, value):
        assert _validated_keyword_type(value) in kw.KEYWORD_TYPES

    @pytest.mark.parametrize("value", ["people", "individuals", "brand", "x"])
    def test_an_unknown_category_is_rejected(self, value):
        """Rejected rather than ignored: silently falling back to "both"
        would run the exact sweep the caller was trying to avoid, with
        nothing in the result to reveal it."""
        with pytest.raises(ValidationError):
            _validated_keyword_type(value)


class TestTheServiceAppliesExactlyThisFilter:
    def test_the_scope_filter_matches_plan_kw_type(self):
        """Guards the coupling between the DTO's vocabulary and the
        KeywordPlan category names -- if either side is ever renamed, the
        filter would silently match nothing and every scoped sweep would
        raise "no keywords to sweep" instead."""
        types = {p.kw_type for p in kw.build_plans(CLIENT)}
        assert types <= set(kw.KEYWORD_TYPES)
        assert types == {kw.INDIVIDUAL, kw.DOMAIN}


class TestEmptyCategoryIsAnError:
    """A scope a client has no keywords in must fail loudly. Sweeping
    nothing and reporting success is the one outcome this must never
    produce -- see the raise in discovery_service.run_discovery."""

    ONLY_DOMAIN = {
        "keyword_groups": {"individual": [], "domain": [{"parent": "Acme", "children": []}]},
        "name_keywords": [], "domain_keywords": ["Acme"],
    }

    def test_scoping_to_a_category_with_no_keywords_yields_no_plans(self):
        plans = [p for p in kw.build_plans(self.ONLY_DOMAIN) if p.kw_type == kw.INDIVIDUAL]
        assert plans == []

    def test_the_other_category_still_has_plans_to_report_in_the_error(self):
        plans = [p for p in kw.build_plans(self.ONLY_DOMAIN) if p.kw_type == kw.DOMAIN]
        assert [p.search for p in plans] == ["Acme"]
