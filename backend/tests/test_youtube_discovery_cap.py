"""Discovery.sweep()'s max_results cap must bound what's actually RETURNED,
not just when the fetch loop stops asking for more.

Found live: the loop-break check (`len(by_id) >= max_results`) only fires
at the top of the NEXT iteration, after a whole search.list response page
has already been absorbed into by_id. YouTube commonly returns far more
than a small configured cap in one page, so a client configured for
max_results=5 was actually getting however many channels came back in that
page -- the same bug, confirmed the same way, as Twitter's identical
pattern (see platforms/twitter/discovery_engine.py's own fix).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.platforms.youtube.discovery_engine import Discovery


def _channel_item(cid: str) -> dict:
    return {
        "id": {"channelId": cid},
        "snippet": {"channelTitle": f"Channel {cid}", "thumbnails": {}},
    }


def _discovery(max_results: int, api_page: list[dict]) -> Discovery:
    d = Discovery.__new__(Discovery)  # skip __init__ -- no real API client needed
    d.a = SimpleNamespace(max_results=max_results, max_seconds=60)
    d.api = SimpleNamespace(
        search_channels=AsyncMock(return_value=(api_page, "")),  # "" token -> one page, then exhausted
    )
    return d


class TestCapIsActuallyEnforcedOnTheReturnedHits:
    @pytest.mark.asyncio
    async def test_a_small_cap_truncates_a_larger_single_page(self):
        # one API page returns 20 channels -- more than the configured cap
        page = [_channel_item(f"c{i}") for i in range(20)]
        d = _discovery(max_results=5, api_page=page)
        out = await d.sweep("adani", "channels")
        assert len(out.hits) == 5

    @pytest.mark.asyncio
    async def test_a_cap_larger_than_the_page_is_a_no_op(self):
        page = [_channel_item(f"c{i}") for i in range(3)]
        d = _discovery(max_results=50, api_page=page)
        out = await d.sweep("adani", "channels")
        assert len(out.hits) == 3

    @pytest.mark.asyncio
    async def test_no_cap_configured_returns_everything(self):
        page = [_channel_item(f"c{i}") for i in range(20)]
        d = _discovery(max_results=0, api_page=page)
        out = await d.sweep("adani", "channels")
        assert len(out.hits) == 20

    @pytest.mark.asyncio
    async def test_the_kept_hits_are_the_first_ones_not_an_arbitrary_subset(self):
        page = [_channel_item(f"c{i}") for i in range(10)]
        d = _discovery(max_results=3, api_page=page)
        out = await d.sweep("adani", "channels")
        assert [h.entity_id for h in out.hits] == ["c0", "c1", "c2"]


class TestYouTubeAPIQuotaAndErrorHandling:
    @pytest.mark.asyncio
    async def test_http_429_quota_exceeded_raises_quota_exceeded(self, monkeypatch):
        import io
        import urllib.error
        import urllib.request
        from backend.platforms.youtube.discovery_engine import YouTubeAPI, QuotaExceeded

        body = '{"error": {"code": 429, "message": "Quota exceeded for quota metric Search Queries"}}'
        err = urllib.error.HTTPError("http://example.com", 429, "Too Many Requests", {}, io.BytesIO(body.encode("utf-8")))

        from unittest.mock import MagicMock
        api = YouTubeAPI(key="dummy-key")
        monkeypatch.setattr(urllib.request, "urlopen", MagicMock(side_effect=err))

        with pytest.raises(QuotaExceeded):
            await api.search_channels("test")

    @pytest.mark.asyncio
    async def test_http_403_invalid_key_raises_runtime_error(self, monkeypatch):
        import io
        import urllib.error
        import urllib.request
        from unittest.mock import MagicMock
        from backend.platforms.youtube.discovery_engine import YouTubeAPI

        body = '{"error": {"code": 403, "message": "API key not valid"}}'
        err = urllib.error.HTTPError("http://example.com", 403, "Forbidden", {}, io.BytesIO(body.encode("utf-8")))

        api = YouTubeAPI(key="dummy-key")
        monkeypatch.setattr(urllib.request, "urlopen", MagicMock(side_effect=err))

        with pytest.raises(RuntimeError, match="API key invalid"):
            await api.get("search", q="test")

    @pytest.mark.asyncio
    async def test_sweep_stops_with_quota_on_quota_exceeded(self):
        from backend.platforms.youtube.discovery_engine import QuotaExceeded
        d = Discovery.__new__(Discovery)
        d.a = SimpleNamespace(max_results=50, max_seconds=60)
        d.api = SimpleNamespace(
            search_channels=AsyncMock(side_effect=QuotaExceeded("YouTube daily quota exhausted")),
        )
        out = await d.sweep("adani", "channels")
        assert out.stopped == "quota"
        assert "quota" in out.error.lower()

