"""Evidence screenshots: the deliverable an impersonation report is built
on. Captures live in Mongo (GridFS), addressed by a plain string key -- not
a filesystem path, so there is no root to escape and nothing to contain;
an unrecognised key is simply a miss (see
database/repositories/evidence_repository.py).
"""

from __future__ import annotations

import pytest

from backend.services import analysis_service as svc
from backend.services import profile_service as profiles


# Key generation

def test_evidence_dir_is_partitioned_by_client_and_platform(monkeypatch):
    monkeypatch.setattr(svc.settings, "capture_evidence", True, raising=False)
    assert svc._evidence_dir("acme", "facebook") == "acme/facebook"


def test_evidence_dir_sanitises_a_hostile_client_id(monkeypatch):
    """client_id is caller-supplied (it is the SaaS's own org id passed
    straight through) and becomes part of a GridFS key -- a plain opaque
    string lookup with no directory semantics, so a `/` in client_id can't
    forge an extra path segment the way it could with a filesystem join."""
    monkeypatch.setattr(svc.settings, "capture_evidence", True, raising=False)
    key = svc._evidence_dir("../../etc", "facebook")
    client_segment = key.rsplit("/", 1)[0]
    assert "/" not in client_segment


def test_capture_can_be_disabled(monkeypatch):
    monkeypatch.setattr(svc.settings, "capture_evidence", False, raising=False)
    assert svc._evidence_dir("acme", "facebook") is None


# Serving: honest 404s, no filesystem involved

@pytest.mark.asyncio
async def test_serving_reports_a_missing_capture_as_not_found(monkeypatch):
    """The document points at a key nothing was ever uploaded under (store
    pruned, or predates evidence keys existing) -- a 404 with a reason,
    never a 500."""
    from backend.shared.errors import NotFoundError
    from backend.database.repositories import evidence_repository

    async def _fake_get(_id):
        return {"id": "p1", "screenshot": "acme/facebook/gone.png", "platform": "facebook"}

    async def _fake_read(_key):
        return None

    monkeypatch.setattr(profiles.profiles_db, "get_by_id", _fake_get)
    monkeypatch.setattr(evidence_repository, "read", _fake_read)
    with pytest.raises(NotFoundError):
        await profiles.screenshot_path("p1")


@pytest.mark.asyncio
async def test_a_garbage_stored_key_is_just_a_miss_not_an_error(monkeypatch):
    """Unlike a filesystem path, a key that looks like a traversal attempt
    (`../../../../etc/passwd`) is not special-cased -- it is simply a
    string GridFS never stored anything under, so it 404s exactly like any
    other missing key, with no separate containment check needed."""
    from backend.shared.errors import NotFoundError
    from backend.database.repositories import evidence_repository

    async def _fake_get(_id):
        return {"id": "p1", "screenshot": "../../../../etc/passwd", "platform": "facebook"}

    async def _fake_read(_key):
        return None

    monkeypatch.setattr(profiles.profiles_db, "get_by_id", _fake_get)
    monkeypatch.setattr(evidence_repository, "read", _fake_read)
    with pytest.raises(NotFoundError):
        await profiles.screenshot_path("p1")


@pytest.mark.asyncio
async def test_serving_returns_a_real_capture_with_a_useful_filename(monkeypatch):
    from backend.database.repositories import evidence_repository

    async def _fake_get(_id):
        return {
            "id": "p1", "screenshot": "acme/facebook/100012345.png",
            "platform": "facebook", "display_name": "Jane Doe",
        }

    async def _fake_read(key):
        assert key == "acme/facebook/100012345.png"
        return b"\x89PNG\r\n"

    monkeypatch.setattr(profiles.profiles_db, "get_by_id", _fake_get)
    monkeypatch.setattr(evidence_repository, "read", _fake_read)
    data, filename = await profiles.screenshot_path("p1")
    assert data == b"\x89PNG\r\n"
    # the download name should be meaningful when attached to a report
    assert filename == "facebook_Jane_Doe.png"


@pytest.mark.asyncio
async def test_no_capture_is_not_found(monkeypatch):
    from backend.shared.errors import NotFoundError

    async def _fake_get(_id):
        return {"id": "p1", "platform": "youtube"}

    monkeypatch.setattr(profiles.profiles_db, "get_by_id", _fake_get)
    with pytest.raises(NotFoundError):
        await profiles.screenshot_path("p1")


# The field is actually persisted

def test_screenshot_is_an_analysis_owned_field():
    from backend.database.repositories import profile_repository as repo

    assert "screenshot" in repo.ANALYSIS_FIELDS
    assert "screenshot_at" in repo.ANALYSIS_FIELDS


@pytest.mark.asyncio
async def test_evidence_retention_deletes_older_files(monkeypatch):
    from backend.database.repositories import evidence_repository
    from datetime import datetime, timezone, timedelta

    deleted_ids = []

    class FakeFile:
        def __init__(self, file_id, upload_date):
            self._id = file_id
            self.uploadDate = upload_date

    class FakeBucket:
        def __init__(self, files):
            self.files = files

        def find(self, query):
            cutoff = query.get("uploadDate", {}).get("$lt")
            class AsyncIter:
                def __init__(self, items):
                    self.items = [f for f in items if cutoff is None or f.uploadDate < cutoff]
                def __aiter__(self):
                    self._i = 0
                    return self
                async def __anext__(self):
                    if self._i >= len(self.items):
                        raise StopAsyncIteration
                    item = self.items[self._i]
                    self._i += 1
                    return item
            return AsyncIter(self.files)

        async def delete(self, file_id):
            deleted_ids.append(file_id)

    now = datetime.now(timezone.utc)
    old_file = FakeFile("old_1", now - timedelta(days=10))
    new_file = FakeFile("new_1", now - timedelta(days=2))

    fake_bucket = FakeBucket([old_file, new_file])
    monkeypatch.setattr(evidence_repository, "_bucket_for", lambda: fake_bucket)

    deleted_count = await evidence_repository.delete_older_than(7)
    assert deleted_count == 1
    assert "old_1" in deleted_ids
    assert "new_1" not in deleted_ids

