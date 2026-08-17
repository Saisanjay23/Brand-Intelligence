"""profile_service._to_card / _to_full -- the two response shapes GET
/profiles picks between by `phase`. Regression coverage for a real bug:
_to_full silently dropped `entity_type` entirely, so the Facebook People/
Pages/Groups filter -- which the frontend re-checks client-side against
`r.entity_type` after the server-side query already filtered correctly --
hid every analysis-phase row again, since `entity_type` was always
undefined on that response shape. `_to_card` already carried the field;
`_to_full` did not.
"""

from __future__ import annotations

from backend.services.profile_service import _to_card, _to_full


def _doc(**overrides) -> dict:
    base = {
        "id": "abc123", "client_id": "1266", "platform": "facebook",
        "url": "https://www.facebook.com/groups/152272458887295/",
        "display_name": "Allu Arjun Fans", "entity_type": "group",
    }
    base.update(overrides)
    return base


class TestToCard:
    def test_carries_entity_type(self):
        card = _to_card(_doc())
        assert card["entity_type"] == "group"

    def test_blank_when_absent(self):
        doc = _doc()
        del doc["entity_type"]
        assert _to_card(doc)["entity_type"] == ""

    def test_client_name_carried_when_client_given(self):
        assert _to_card(_doc(), client={"name": "Acme Corp"})["client_name"] == "Acme Corp"

    def test_client_name_blank_when_no_client(self):
        assert _to_card(_doc())["client_name"] == ""


class TestToFull:
    def test_carries_entity_type(self):
        """The bug: this used to never include the key at all."""
        full = _to_full(_doc(), client=None)
        assert full["entity_type"] == "group"

    def test_blank_when_absent(self):
        doc = _doc()
        del doc["entity_type"]
        assert _to_full(doc, client=None)["entity_type"] == ""

    def test_page_and_profile_values_also_carried(self):
        assert _to_full(_doc(entity_type="page"), client=None)["entity_type"] == "page"
        assert _to_full(_doc(entity_type="profile"), client=None)["entity_type"] == "profile"

    def test_carries_has_name_match_target_and_official_feed(self):
        """The same class of bug as entity_type: stored (ANALYSIS_FIELDS)
        and PATCH-editable (ProfilePatch) since analysis existed, but never
        reached this response shape -- so an analyst's edit to Target/
        Original Feed, or a scraper's own name-match reading, was silently
        invisible on every analysis-phase GET and every export built from it."""
        doc = _doc(has_name_match=True, target="Acme Inc", official_feed="https://x.com/AcmeInc")
        full = _to_full(doc, client=None)
        assert full["has_name_match"] is True
        assert full["target"] == "Acme Inc"
        assert full["official_feed"] == "https://x.com/AcmeInc"

    def test_has_name_match_is_none_not_false_when_never_checked(self):
        """Tri-state like is_active: None means unknown, not "confirmed no
        match" -- a UI/export that renders None as "No" would be lying
        about a check that never actually ran."""
        assert _to_full(_doc(), client=None)["has_name_match"] is None

    def test_target_and_official_feed_blank_when_absent(self):
        full = _to_full(_doc(), client=None)
        assert full["target"] == ""
        assert full["official_feed"] == ""

    def test_client_name_carried_when_client_given(self):
        assert _to_full(_doc(), client={"name": "Acme Corp"})["client_name"] == "Acme Corp"
