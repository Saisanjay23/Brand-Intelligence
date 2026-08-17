"""The published incident is what actually leaves this tool for a client,
so its categorisation and its claims have to be right.

Two things covered here that were previously wrong:
  - a hand-added URL (no keyword provenance) was ALWAYS filed as brand
    infringement, so every tip-sourced executive impersonation was
    mis-categorised;
  - "we could not determine whether this account is active" was published
    as `isActive: false`, an assertion about a profile nobody had checked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services import incident_publisher as pub

CLIENT = {
    "client_id": "acme",
    "name": "Acme Corp",
    "domain": "acme.com",
    "name_keywords": ["Jane Doe", "John Smith"],
    "domain_keywords": ["acme", "acme corp"],
    "asset_name_individual_keywords": [],
    "asset_name_domain_keywords": [],
}


def _doc(**over) -> dict:
    base = {
        "id": "p1", "platform": "facebook", "url": "https://www.facebook.com/fake",
        "display_name": "Jane Doe", "keywords": [], "client_id": "acme",
    }
    base.update(over)
    return base


# Categorisation

def test_individual_keyword_match_is_an_impersonation():
    cat, sub, asset = pub._category_and_asset_name(_doc(keywords=["Jane Doe"]), CLIENT)
    assert (cat, sub) == pub.CATEGORY_PERSON
    assert asset == "Jane Doe"


def test_domain_keyword_match_is_brand_infringement():
    cat, sub, asset = pub._category_and_asset_name(_doc(keywords=["acme"]), CLIENT)
    assert (cat, sub) == pub.CATEGORY_BRAND
    assert asset == "Acme Corp"


def test_keyword_matching_is_case_insensitive():
    """A keyword saved as "Jane Doe" and recorded on the profile as
    "jane doe" is the same keyword; an exact match filed it as brand."""
    cat, sub, _ = pub._category_and_asset_name(_doc(keywords=["jane doe"]), CLIENT)
    assert (cat, sub) == pub.CATEGORY_PERSON


def test_hand_added_url_is_classified_from_its_display_name():
    """No keywords at all -- what POST /profiles/add-urls produces. This
    used to fall through to brand infringement unconditionally."""
    cat, sub, asset = pub._category_and_asset_name(_doc(keywords=[], display_name="Jane Doe Official"), CLIENT)
    assert (cat, sub) == pub.CATEGORY_PERSON
    assert asset == "Jane Doe"


def test_hand_added_url_with_no_signal_still_falls_back_to_brand():
    cat, sub, asset = pub._category_and_asset_name(_doc(keywords=[], display_name="Totally Unrelated"), CLIENT)
    assert (cat, sub) == pub.CATEGORY_BRAND
    assert asset == "Acme Corp"


def test_configured_drk_asset_name_is_preferred():
    """The client's curated Digital Risk Keyword lists were stored, returned
    by the API and rendered as a dropdown, but never consulted -- so every
    incident needed a manual assetName override."""
    client = {**CLIENT, "asset_name_domain_keywords": ["Acme Group Ltd"]}
    _, _, asset = pub._category_and_asset_name(_doc(keywords=["acme"]), client)
    assert asset == "Acme Group Ltd"


# IsActive tri-state

def test_recent_post_is_active():
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert pub._is_active(_doc(last_post_date=recent)) is True


def test_old_post_is_inactive():
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    assert pub._is_active(_doc(last_post_date=old)) is False


def test_no_post_date_is_unknown_not_inactive():
    """Telegram exposes no last-post date, Instagram often doesn't either,
    and a cut-short analysis never gets one. Publishing that as
    `isActive: false` asserts something nobody checked -- and drags the
    risk rating down with it."""
    assert pub._is_active(_doc()) is None
    assert pub._is_active(_doc(last_post_date="")) is None
    assert pub._is_active(_doc(last_post_date="not-a-date")) is None


def test_incident_carries_unknown_activity_as_null():
    inc = pub.build_incident_doc(_doc(), CLIENT)
    assert inc["socialProfileInfo"]["isActive"] is None


# The rest of the published shape

def test_incident_is_dated():
    """`date` was hard-coded None, so every published incident arrived
    undated and a consumer couldn't tell this morning's finding from March's."""
    when = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    inc = pub.build_incident_doc(_doc(analysed_at=when), CLIENT)
    assert inc["date"] is not None
    assert inc["date"].startswith("2026-03-01")


def test_evidence_block_reports_what_was_actually_read():
    thin = pub.build_incident_doc(_doc(), CLIENT)
    rich = pub.build_incident_doc(
        _doc(followers=5000, location="Mumbai", last_post_date="2026-08-01T00:00:00+00:00",
             profile_image_url="https://cdn/x.jpg", entity_type="profile", verified=True,
             screenshot="acme/facebook/123.png"),
        CLIENT,
    )
    assert rich["evidence"]["fieldsRead"] > thin["evidence"]["fieldsRead"]
    assert rich["evidence"]["screenshot"] is True
    assert thin["evidence"]["screenshot"] is False


def test_analyst_overrides_still_win():
    inc = pub.build_incident_doc(
        _doc(incident_overrides={"assetName": "Hand Picked", "socialProfileInfo.location": "Delhi"}),
        CLIENT,
    )
    assert inc["assetName"] == "Hand Picked"
    assert inc["socialProfileInfo"]["location"] == "Delhi"


def test_org_id_is_the_client_id():
    """published_incidents is keyed on (orgId, source) -- a wrong orgId here
    would collide two tenants' findings."""
    assert pub.build_incident_doc(_doc(), CLIENT)["orgId"] == "acme"
