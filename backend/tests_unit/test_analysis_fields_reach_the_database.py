"""A field an engine reads must actually survive to the database.

THE DEFECT CLASS THIS GUARDS
    `profile_repository.save()` writes through a WHITELIST
    (`ANALYSIS_FIELDS`). Anything the service mapping emits that is not on
    that list is dropped in total silence -- no exception, no warning, no
    failed test. The engine looks correct because it IS correct; the value
    just never lands.

    Measured on 2026-08-22, before this file existed: every Twitter profile
    visit resolved a real join date (`row.created_iso` was set on 100% of
    stored rows, and `sources` recorded `created: graphql` on all 179 of
    them), yet only 5 of those 179 rows carried a `created_at` in Mongo.
    The value was read, tagged with its source, mapped -- and discarded at
    the whitelist. `bio` was the same story on both Twitter and Instagram.

    That is invisible to every other kind of test, which is why the check
    here is structural rather than field-by-field: any NEW field added to
    the mapping in future fails this test until it is also allowed through.
"""

from __future__ import annotations

from backend.database.repositories.profile_repository import ANALYSIS_FIELDS
from backend.services.analysis_service import _row_to_fields
from backend.shared.models.row import Row

# Keys the mapping emits that are deliberately NOT analysis-owned:
#   url        the document key itself, not a field written onto it
#   entity_id  owned by discovery; analysis must never write it back
#              (see the long note above ANALYSIS_FIELDS -- writing a vanity
#              slug over a canonical numeric id breaks the dedup key)
#   keyword    discovery's field; analysis always emits it blank
NOT_ANALYSIS_OWNED = {"url", "entity_id", "keyword"}


def _full_row() -> Row:
    row = Row(url="https://x.com/someone", target="Acme",
              original_feed="https://x.com/acme")
    row.profile_name = "Acme Support"
    row.profile_id = "42"
    row.followers = 1234
    row.friends = 56
    row.location = "Pune"
    row.bio = "Official account of Acme"
    row.created_iso = "2016-06-01"
    row.last_post_iso = "2026-08-19"
    row.posts_seen = "yes"
    row.has_custom_pic = True
    row.name_score = 100
    row.status = "OK"
    row.screenshot = "client/twitter/42.png"
    return row


class TestEveryMappedFieldIsWritable:
    def test_no_mapped_field_is_silently_dropped_by_the_whitelist(self):
        fields = _row_to_fields(_full_row(), platform_id="twitter",
                                want_screenshot=True)
        dropped = set(fields) - set(ANALYSIS_FIELDS) - NOT_ANALYSIS_OWNED
        assert not dropped, (
            "these fields are produced by the analysis mapping but are not in "
            f"ANALYSIS_FIELDS, so save() will discard them silently: {sorted(dropped)}"
        )


class TestTheTwoFieldsThatWereBeingLost:
    def test_created_at_is_mapped(self):
        fields = _row_to_fields(_full_row(), platform_id="twitter",
                                want_screenshot=True)
        assert fields["created_at"] == "2016-06-01"

    def test_created_at_is_writable(self):
        assert "created_at" in ANALYSIS_FIELDS

    def test_bio_is_mapped(self):
        fields = _row_to_fields(_full_row(), platform_id="twitter",
                                want_screenshot=True)
        assert fields["bio"] == "Official account of Acme"

    def test_bio_is_writable(self):
        assert "bio" in ANALYSIS_FIELDS

    def test_a_platform_that_cannot_see_a_join_date_writes_nothing(self):
        """Facebook and Instagram do not expose one. save() drops "" on its
        own, so the key is simply never written rather than a blank being
        stored over something a previous pass got right."""
        row = _full_row()
        row.created_iso = ""
        fields = _row_to_fields(row, platform_id="facebook", want_screenshot=True)
        assert fields["created_at"] == ""
