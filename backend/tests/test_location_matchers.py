"""Facebook location matchers must not invent a place from prose.

`location` reaches client-facing published incidents, so a false positive
here is not a cosmetic bug -- it is a fabricated fact in a takedown record.
A live Page produced the prose case below.
"""

from __future__ import annotations

from backend.platforms.facebook.analysis_engine import (RE_FROM, RE_LIVES_IN,
                                                         is_place)


class TestNoFalsePositivesFromProse:
    def test_marketing_copy_is_not_a_location(self):
        # captured live from facebook.com/AdaniCement
        prose = (
            "Committed to building nations with goodness. From classrooms to "
            "cement plants, from learning concepts to witnessing them"
        )
        m = RE_FROM.search(prose)
        assert m is None, f"matched prose mid-sentence: {m.group(1)!r}"

    def test_from_mid_sentence_never_matches(self):
        for prose in (
            "He returned from Mumbai last week",
            "Everything from design to delivery",
            "Learn from the best in the business",
        ):
            assert RE_FROM.search(prose) is None, prose


class TestStillReadsRealFields:
    def test_reads_an_about_tab_from_field(self):
        about = "Work\nStudied at IIT\nFrom Ahmedabad, Gujarat\nJoined 2011"
        m = RE_FROM.search(about)
        assert m and m.group(1).strip() == "Ahmedabad, Gujarat"

    def test_reads_an_about_tab_lives_in_field(self):
        about = "Intro\nLives in Mumbai, Maharashtra\nFrom Ahmedabad"
        m = RE_LIVES_IN.search(about)
        assert m and m.group(1).strip() == "Mumbai, Maharashtra"

    def test_matches_at_the_very_start_of_the_text(self):
        m = RE_LIVES_IN.search("Lives in Delhi")
        assert m and m.group(1).strip() == "Delhi"

    def test_a_real_city_still_validates_as_a_place(self):
        m = RE_FROM.search("From Ahmedabad, Gujarat\n")
        assert m and is_place(m.group(1))
