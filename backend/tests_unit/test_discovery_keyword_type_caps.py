"""Individual-vs-domain keyword classification and cap resolution
(backend/services/discovery_service.py) -- what lets an analyst cap
executive-name sweeps and brand-name sweeps independently per platform.
Pure functions, no I/O.
"""

from __future__ import annotations

from backend.services.discovery_service import (
    _effective_cap, _is_individual_keyword, _split_by_keyword_type,
)

CLIENT = {
    "name_keywords": ["Jane Doe", "John Smith"],
    "domain_keywords": ["Acme", "Acme Corp"],
    "asset_name_individual_keywords": ["J. Doe"],
}


# Classification

def test_name_keyword_is_individual():
    assert _is_individual_keyword("Jane Doe", CLIENT) is True


def test_asset_name_individual_keyword_is_individual():
    assert _is_individual_keyword("J. Doe", CLIENT) is True


def test_domain_keyword_is_not_individual():
    assert _is_individual_keyword("Acme", CLIENT) is False


def test_classification_is_case_insensitive():
    assert _is_individual_keyword("jane doe", CLIENT) is True
    assert _is_individual_keyword("JANE DOE", CLIENT) is True


def test_unrecognised_adhoc_keyword_falls_back_to_domain():
    """A manual POST /discovery caller can supply anything -- a keyword this
    client's own config doesn't recognise at all must not silently become
    "individual" (the more generous cap in most configs)."""
    assert _is_individual_keyword("Totally Unrelated Term", CLIENT) is False


def test_split_partitions_a_mixed_keyword_list():
    individual, domain = _split_by_keyword_type(
        ["Jane Doe", "Acme", "John Smith", "Random"], CLIENT,
    )
    assert individual == ["Jane Doe", "John Smith"]
    assert domain == ["Acme", "Random"]


def test_split_of_empty_list_is_two_empty_lists():
    assert _split_by_keyword_type([], CLIENT) == ([], [])


# Cap combination

def test_effective_cap_of_no_caps_is_uncapped():
    assert _effective_cap() == 0
    assert _effective_cap(0, 0) == 0


def test_effective_cap_takes_the_smaller_nonzero_value():
    assert _effective_cap(50, 20) == 20
    assert _effective_cap(20, 50) == 20


def test_effective_cap_ignores_a_zero_uncapped_dimension():
    # 0 means "no limit on this dimension" -- it must never win a min()
    # against a real limit, or "uncapped tab, capped keyword-type" would
    # collapse to "cap 0", i.e. accidentally uncapped instead of capped.
    assert _effective_cap(0, 30) == 30
    assert _effective_cap(30, 0) == 30


def test_effective_cap_of_a_single_real_value():
    assert _effective_cap(15) == 15
