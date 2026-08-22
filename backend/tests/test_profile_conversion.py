"""`_row_to_fields`/`_hit_to_fields` -- the Row/Hit -> plain field dict
conversion that carries scoring across into what `db.profiles.save`
writes. Pure, no I/O.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.services.analysis_service import _row_to_fields
from backend.shared import keywords as kw
from backend.services.discovery_service import _hit_to_fields
from backend.shared.models.row import Row


def test_row_to_fields_carries_the_scored_fields():
    row = Row(url="https://x", target="Brand", original_feed="brand_official")
    row.profile_id = "123"
    row.profile_name = "Brand Impersonator"
    row.has_custom_pic = True
    row.name_score = 91
    row.followers = 2000

    fields = _row_to_fields(row)

    assert fields["has_logo"] is True
    assert fields["risk_score"] == row.risk
    assert fields["priority"] == row.priority
    assert fields["entity_id"] == "123"
    assert fields["display_name"] == "Brand Impersonator"


def test_hit_to_fields_is_identity_only_not_scored():
    hit = SimpleNamespace(
        url="https://instagram.com/fake", entity_id="e1", name="Fake Brand",
        entity_type="profile", keyword="Brand", tab="people", source="graphql",
        avatar="", has_custom_pic=False,
    )
    # A childless parent searches itself, which is the shape every client
    # had before parent/child keyword groups existed -- so this stays the
    # exact pre-groups case. See backend/shared/keywords.py.
    plan = kw.build_plans({"domain_keywords": ["Brand"]})[0]
    fields = _hit_to_fields(hit, "instagram", plan)

    assert fields["has_logo"] is False  # not scored yet -- discovery never scores
    assert fields["username"] == "fake"  # last URL segment for handle-based platforms
    assert fields["discovery_source"] == "graphql"
    assert "risk_score" not in fields and "priority" not in fields
