"""Handle (username) matching against a client's official handle.

The gap this closes: every name_score() call in the engines compares a
profile's DISPLAY NAME. A username squat ("@adani_care_official") was
therefore invisible to every automated signal the tool had -- the only
record of it was an analyst ticking a checkbox by hand.
"""

from __future__ import annotations

from backend.shared.text import handle_score


class TestExactAndPunctuation:
    def test_identical(self):
        assert handle_score("adanigroup", "adanigroup") == 100

    def test_punctuation_and_case_are_not_meaning(self):
        # the same handle wearing different punctuation is the same handle
        for variant in ("@Adani_Group", "adani.group", "ADANI-GROUP", " adanigroup "):
            assert handle_score(variant, "adanigroup") == 100, variant

    def test_full_url_is_accepted(self):
        # an operator pasting from the browser has a URL, not a bare handle
        for url in (
            "https://www.facebook.com/adanigroup",
            "https://twitter.com/adanigroup/",
            "instagram.com/@adanigroup",
        ):
            assert handle_score(url, "adanigroup") == 100, url


class TestSquats:
    def test_typo_squat_scores_high(self):
        # a deliberate misspelling is the whole point -- must not score low
        assert handle_score("adanigrouup", "adanigroup") >= 85

    def test_decorated_handle_scores_high(self):
        # official handle wrapped in decoration: plain edit distance
        # under-rates these badly, which is why containment is special-cased
        for squat in ("adanigroupofficial", "officialadanigroup", "adanigroup_india"):
            assert handle_score(squat, "adanigroup") >= 90, squat

    def test_unrelated_handle_scores_low(self):
        assert handle_score("randomperson", "adanigroup") < 50


class TestDiscoveryWiring:
    """_hit_to_fields is where the score actually reaches the database."""

    @staticmethod
    def _hit(url: str, name: str = "Adani Care"):
        from types import SimpleNamespace

        return SimpleNamespace(
            url=url, entity_id="123", keyword="Adani", name=name,
            entity_type="profile", source="search", avatar="",
            has_custom_pic=True, verified=False,
        )

    def test_scores_handle_against_configured_official_handle(self):
        from backend.services.discovery_service import _hit_to_fields

        f = _hit_to_fields(self._hit("https://twitter.com/adanigroupindia"), "twitter", "adanigroup")
        assert f["username"] == "adanigroupindia"
        assert f["username_score"] >= 90

    def test_unrelated_handle_scores_low_but_is_still_recorded(self):
        from backend.services.discovery_service import _hit_to_fields

        f = _hit_to_fields(self._hit("https://twitter.com/randomperson"), "twitter", "adanigroup")
        assert f["username_score"] < 50

    def test_field_is_ABSENT_when_client_has_no_official_handle(self):
        # the important one: a missing measurement must not be stored as 0,
        # or a "low username score" filter would sweep up every profile of
        # every client that never configured a handle
        from backend.services.discovery_service import _hit_to_fields

        f = _hit_to_fields(self._hit("https://twitter.com/adanigroup"), "twitter", "")
        assert "username_score" not in f

    def test_field_is_absent_when_url_yields_no_handle(self):
        from backend.services.discovery_service import _hit_to_fields

        f = _hit_to_fields(self._hit("https://twitter.com/"), "twitter", "adanigroup")
        assert "username_score" not in f

    def test_name_score_still_computed_independently(self):
        # the two signals must not interfere -- a handle match should never
        # silently alter the display-name score or vice versa
        from backend.services.discovery_service import _hit_to_fields

        with_handle = _hit_to_fields(self._hit("https://twitter.com/adanigroup"), "twitter", "adanigroup")
        without = _hit_to_fields(self._hit("https://twitter.com/adanigroup"), "twitter", "")
        assert with_handle["name_score"] == without["name_score"]


class TestGuards:
    def test_blank_either_side_is_zero_not_a_match(self):
        # a client with no official handle configured must never produce a
        # score that could be read as evidence
        assert handle_score("", "adanigroup") == 0
        assert handle_score("adanigroup", "") == 0
        assert handle_score("", "") == 0
        assert handle_score(None, "adanigroup") == 0  # type: ignore[arg-type]

    def test_short_official_handle_does_not_match_by_containment(self):
        # "hp" is a substring of "shopping" -- containment on a very short
        # official handle would flag half the platform as an impersonator
        assert handle_score("shopping", "hp") < 90

    def test_punctuation_only_handle_is_zero(self):
        assert handle_score("___", "adanigroup") == 0
