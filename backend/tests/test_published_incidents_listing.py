"""GET /published-incidents -- the findings feed that was previously
missing entirely (only `upsert` existed; no read path). Covers the one
non-obvious piece of logic: a caller passes a platform id ("twitter"), but
no raw platform id is stored on a published-incident document -- only the
display name (`assetType`, e.g. "Twitter") `incident_publisher.
build_incident_doc` writes. `list_published` has to resolve one to the
other before the repository query runs, or a platform-scoped read would
silently return nothing.
"""

from __future__ import annotations

from backend.services import incident_publisher as pub


def test_platform_id_resolves_to_the_stored_display_name(monkeypatch):
    captured = {}

    async def _fake_find(org_id, *, platform=None, limit=50, offset=0):
        captured["org_id"] = org_id
        captured["platform"] = platform
        return [], 0

    monkeypatch.setattr(pub.incidents_db, "find", _fake_find)

    import asyncio
    asyncio.run(pub.list_published("acme", platform="twitter"))

    assert captured["platform"] == pub.PLATFORMS["twitter"].name


def test_no_platform_filter_passes_through_unset(monkeypatch):
    captured = {}

    async def _fake_find(org_id, *, platform=None, limit=50, offset=0):
        captured["platform"] = platform
        return [], 0

    monkeypatch.setattr(pub.incidents_db, "find", _fake_find)

    import asyncio
    asyncio.run(pub.list_published("acme"))

    assert captured["platform"] is None


def test_unknown_platform_id_passes_through_unresolved(monkeypatch):
    """Not this function's job to validate platform ids -- an unrecognised
    one is passed straight through and simply matches nothing, same as any
    other filter value with no matching documents."""
    captured = {}

    async def _fake_find(org_id, *, platform=None, limit=50, offset=0):
        captured["platform"] = platform
        return [], 0

    monkeypatch.setattr(pub.incidents_db, "find", _fake_find)

    import asyncio
    asyncio.run(pub.list_published("acme", platform="myspace"))

    assert captured["platform"] == "myspace"


def test_response_shape_matches_every_other_list_endpoint(monkeypatch):
    """{items, total, limit, offset} -- the one shape a caller only has to
    parse once (see shared/pagination.py)."""
    async def _fake_find(org_id, *, platform=None, limit=50, offset=0):
        return [{"id": "1", "orgId": org_id}], 1

    monkeypatch.setattr(pub.incidents_db, "find", _fake_find)

    import asyncio
    out = asyncio.run(pub.list_published("acme", limit=25, offset=0))

    assert out == {"items": [{"id": "1", "orgId": "acme"}], "total": 1, "limit": 25, "offset": 0}
