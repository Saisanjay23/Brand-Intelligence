"""profile_service._best_matching_keyword -- the classification helper
behind the Add URLs modal's Executive/Domain split (see
profile_service.add_manual_urls). A URL typed into the Executive box must
end up correctly bucketed as an individual-type keyword even when the URL
text itself doesn't happen to contain any of the client's configured
names -- that's the bug this split exists to close (see the function's own
docstring): a hand-added executive URL used to have NO way into the
individual bucket at all unless it fuzzy-matched, and silently published
as Brand Infringement instead.
"""

from __future__ import annotations

from backend.services.profile_service import _best_matching_keyword


class TestBestMatchingKeyword:
    def test_prefers_a_real_fuzzy_match(self):
        """A genuine match still wins over just falling back to the first
        candidate -- keeps per-keyword counts/coverage meaningful."""
        got = _best_matching_keyword(
            "https://x.com/GautamAdani official", ["Gautam Adani", "Karan Adani"],
        )
        assert got == "Gautam Adani"

    def test_ignores_punctuation_and_case(self):
        got = _best_matching_keyword("https://x.com/gautam-adani_official", ["Gautam Adani"])
        assert got == "Gautam Adani"

    def test_no_match_returns_blank_not_a_guess(self):
        got = _best_matching_keyword("https://x.com/totally-unrelated-handle", ["Gautam Adani"])
        assert got == ""

    def test_blank_when_no_candidates(self):
        assert _best_matching_keyword("https://x.com/anything", []) == ""

    def test_returns_first_matching_candidate_in_order(self):
        got = _best_matching_keyword("https://x.com/adanigroup", ["Adani Group", "Adani"])
        assert got == "Adani Group"
