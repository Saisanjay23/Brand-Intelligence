"""The "About this account" panels on X and Instagram.

Both publish fields the engines could not otherwise report at all:

    twitter    `location` was blank on 52% of stored rows -- the account
               holder simply never typed one. The panel carries the country
               X believes the account operates from, plus a RENAME COUNT,
               which is the clearest single tell that one account has been
               recycled through several identities and which no other
               payload this project reads exposes.

    instagram  `location` was blank on 100% of stored rows and the join
               date has always been documented here as not exposed. The
               panel publishes both.

Live-confirmed 2026-08-22. Shapes below are trimmed from real captures.
"""

from __future__ import annotations

from backend.platforms.instagram.analysis_engine import Scraper as IgScraper
from backend.platforms.instagram.discovery_engine import about_country
from backend.platforms.twitter.discovery_engine import about_account_from


# Trimmed from the real AboutAccountQuery response.
TW_ABOUT = {
    "data": {"user_result_by_screen_name": {"result": {
        "__typename": "User",
        "about_profile": {
            "account_based_in": "India",
            "created_country_accurate": True,
            "location_accurate": True,
            "source": "India Android App",
            "username_changes": {"count": "19",
                                 "last_changed_at_msec": "1732173166140"},
        },
        "core": {"screen_name": "Adani_Gautam_"},
    }}}
}

# Trimmed from the real Bloks payload behind Instagram's panel. The country
# rides on its own NAMED state key, which is why it is read from there
# rather than from the label/value Text components beside it.
IG_BLOKS = (
    'for (;;);{"payload":{"layout":{"bloks_payload":{"data":['
    '{"id":"a","type":"gs","data":{"key":"IG_ABOUT_THIS_ACCOUNT:about_this_account_country_visibility",'
    '"mode":"p","initial":true}},'
    '{"id":"b","type":"gs","data":{"key":"IG_ABOUT_THIS_ACCOUNT:about_this_account_country",'
    '"mode":"p","initial":"India"}}]}}}}'
)


class TestTwitterAboutPanel:
    def test_reads_the_country_the_profile_never_stated(self):
        about = about_account_from(TW_ABOUT)
        assert about is not None
        assert about.account_based_in == "India"

    def test_reads_the_rename_count(self):
        assert about_account_from(TW_ABOUT).username_changes == 19

    def test_converts_the_rename_timestamp_to_a_date(self):
        assert about_account_from(TW_ABOUT).last_username_change_iso == "2024-11-21"

    def test_extra_unread_payload_fields_do_not_break_parsing(self):
        """`created_country_accurate`/`location_accurate`/`source` are real
        fields in the live payload (see TW_ABOUT) that this project does not
        currently consume -- see AboutAccount's own docstring for why they
        were deliberately left off the dataclass. Parsing must not choke on
        their presence."""
        about = about_account_from(TW_ABOUT)
        assert not hasattr(about, "source")
        assert about.account_based_in == "India"

    def test_a_payload_without_the_panel_yields_nothing(self):
        assert about_account_from({"data": {"user": {"result": {}}}}) is None

    def test_a_malformed_rename_count_is_dropped_not_guessed(self):
        blob = {"about_profile": {"account_based_in": "X",
                                  "username_changes": {"count": "many"}}}
        got = about_account_from(blob)
        assert got.account_based_in == "X"
        assert got.username_changes is None


class TestInstagramAboutPanel:
    def test_reads_the_country_from_its_named_state_key(self):
        assert about_country(IG_BLOKS) == "India"

    def test_the_visibility_key_is_not_mistaken_for_the_country(self):
        """Both keys share a prefix; matching the wrong one would store
        "true" as a location."""
        assert about_country(IG_BLOKS) != "true"

    def test_a_payload_without_the_key_yields_nothing(self):
        assert about_country('{"payload":{}}') == ""

    def test_empty_input_is_safe(self):
        assert about_country("") == ""


class TestInstagramJoinDate:
    def test_month_and_year_become_a_month_precise_iso(self):
        assert IgScraper._parse_joined("April 2025") == "2025-04"

    def test_no_day_is_invented(self):
        """The panel publishes only a month. Padding to "-01" would put a
        date in the record Instagram never stated."""
        assert IgScraper._parse_joined("April 2025").count("-") == 1

    def test_a_year_before_instagram_existed_is_rejected(self):
        assert IgScraper._parse_joined("April 1999") == ""

    def test_unparseable_text_yields_nothing(self):
        for text in ("", "sometime", "2025", "Blursday 2025"):
            assert IgScraper._parse_joined(text) == ""
