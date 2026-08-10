"""name_score (backend/shared/text.py) -- the display-name matching
algorithm every platform's discovery/analysis engine scores every found
profile against. Had zero direct test coverage despite driving name_yes,
Row.risk/priority, and the Match Level filter/badge (see the Live Results
filter fix -- resultsFilter.ts/profile_repository.py both key off this
score's output range). handle_score (its username-matching sibling) has a
full test file; this backstops the other half.
"""

from __future__ import annotations

from backend.shared.text import name_score


class TestDocumentedExamples:
    """The exact cases from name_score's own docstring."""

    def test_same_words_reordered_is_a_perfect_match(self):
        assert name_score("Adani Gautam", "Gautam Adani") == 100

    def test_target_fully_covered_with_extra_words_is_perfect(self):
        assert name_score("Gautam Adani Official", "Gautam Adani") == 100

    def test_typo_squat_scores_very_high_not_perfect(self):
        s = name_score("Gautamm Adani", "Gautam Adani")
        assert 85 <= s < 100

    def test_half_the_target_present_scores_around_half(self):
        assert name_score("Gautam", "Gautam Adani") == 50


class TestGuards:
    def test_empty_candidate_scores_zero(self):
        assert name_score("", "Gautam Adani") == 0

    def test_empty_target_scores_zero(self):
        assert name_score("Gautam Adani", "") == 0

    def test_both_empty_scores_zero(self):
        assert name_score("", "") == 0

    def test_whitespace_only_is_treated_as_empty(self):
        assert name_score("   ", "Gautam Adani") == 0


class TestCaseAndPunctuationAreNotMeaning:
    def test_case_insensitive(self):
        assert name_score("GAUTAM ADANI", "gautam adani") == 100

    def test_punctuation_stripped(self):
        assert name_score("Gautam-Adani!!", "Gautam Adani") == 100

    def test_extra_whitespace_collapsed(self):
        assert name_score("Gautam    Adani", "Gautam Adani") == 100


class TestUnrelatedNamesScoreLow:
    def test_completely_different_name_scores_low(self):
        assert name_score("Random Person Xyz", "Gautam Adani") < 30

    def test_single_shared_short_word_is_not_enough_for_a_high_score(self):
        # "The" alone carries no brand identity
        assert name_score("The Random Company", "The Adani Group") < 60


class TestCoverageWeighting:
    """The whole reason this isn't plain token-set: a subset match must not
    score as if the full target were present."""

    def test_a_single_word_subset_of_a_two_word_target_is_not_a_full_match(self):
        assert name_score("Adani", "Gautam Adani") == 50

    def test_a_single_word_subset_of_a_three_word_target_scores_lower_still(self):
        s = name_score("Adani", "Gautam Sanjay Adani")
        assert s <= 40

    def test_reverse_direction_extra_words_in_target_not_candidate(self):
        # candidate has the full target's words plus more -- coverage of
        # the TARGET is what's measured, and the target's every word is
        # present, so this should still score highly
        assert name_score("Official Gautam Adani Group Page", "Gautam Adani") == 100
