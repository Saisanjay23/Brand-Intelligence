"""Resume threading in backend/services/discovery_service.py::
_run_incremental -- which engines get handed a stored cursor, and what
happens when looking one up fails.

The rule this protects: an engine that does not implement resuming must be
called EXACTLY as it was before resume existed. Five of the six platforms
are in that category, and passing an unexpected keyword argument to any of
them would turn a working sweep into a TypeError that `_run_incremental`
would then dutifully convert into a stopped="error" sweep -- i.e. resume
would silently disable discovery on every platform that doesn't support it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from backend.services import discovery_service


@dataclass
class FakeSweep:
    keyword: str = ""
    tab: str = ""
    hits: list = field(default_factory=list)
    stopped: str = ""
    error: str = ""
    complete: bool = False
    extraction: object = None


class ResumeAwareDiscoverer:
    """Stands in for Facebook's engine: declares `resume_cursor`."""

    Sweep = FakeSweep

    def __init__(self):
        self.seen: list[dict] = []

    async def sweep(self, keyword, tab, on_progress=None, resume_cursor=""):
        self.seen.append({"keyword": keyword, "tab": tab, "resume_cursor": resume_cursor})
        return FakeSweep(keyword=keyword, tab=tab)


class LegacyDiscoverer:
    """Stands in for every engine that has no resume support at all."""

    Sweep = FakeSweep

    def __init__(self):
        self.seen: list[dict] = []

    async def sweep(self, keyword, tab, on_progress=None):
        self.seen.append({"keyword": keyword, "tab": tab})
        return FakeSweep(keyword=keyword, tab=tab)


async def _noop_done(_sweep):
    return None


@pytest.fixture(autouse=True)
def _module_sweep_class(monkeypatch):
    """`_sweep_class_for` looks up a `Sweep` in the discoverer's own module;
    these fakes live in this test module, so point it here."""
    monkeypatch.setattr(discovery_service, "_sweep_class_for", lambda d: FakeSweep)


class TestResumeAwareEngine:
    @pytest.mark.asyncio
    async def test_a_stored_cursor_is_handed_to_the_engine(self):
        d = ResumeAwareDiscoverer()

        async def resume_for(keyword, tab):
            return f"cursor-for-{keyword}-{tab}"

        await discovery_service._run_incremental(
            d, [("acme", "people")], _noop_done, None, resume_for,
        )
        assert d.seen[0]["resume_cursor"] == "cursor-for-acme-people"

    @pytest.mark.asyncio
    async def test_each_keyword_tab_pair_gets_its_own_position(self):
        d = ResumeAwareDiscoverer()

        async def resume_for(keyword, tab):
            return f"{keyword}:{tab}"

        await discovery_service._run_incremental(
            d, [("acme", "people"), ("acme", "pages")], _noop_done, None, resume_for,
        )
        got = {(s["keyword"], s["tab"]): s["resume_cursor"] for s in d.seen}
        assert got == {("acme", "people"): "acme:people", ("acme", "pages"): "acme:pages"}

    @pytest.mark.asyncio
    async def test_no_stored_position_means_start_from_the_top(self):
        d = ResumeAwareDiscoverer()

        async def resume_for(keyword, tab):
            return ""

        await discovery_service._run_incremental(
            d, [("acme", "people")], _noop_done, None, resume_for,
        )
        # falls through to the engine's own default rather than being
        # passed an explicit empty string
        assert d.seen[0]["resume_cursor"] == ""

    @pytest.mark.asyncio
    async def test_no_resume_callback_at_all_is_the_pre_resume_behaviour(self):
        d = ResumeAwareDiscoverer()
        await discovery_service._run_incremental(d, [("acme", "people")], _noop_done)
        assert d.seen[0]["resume_cursor"] == ""

    @pytest.mark.asyncio
    async def test_a_failing_lookup_does_not_stop_the_sweep(self):
        # resume is an optimisation: a Mongo hiccup must degrade to a normal
        # full sweep, never to a sweep that doesn't happen
        d = ResumeAwareDiscoverer()

        async def resume_for(keyword, tab):
            raise RuntimeError("mongo down")

        sweeps = await discovery_service._run_incremental(
            d, [("acme", "people")], _noop_done, None, resume_for,
        )
        assert d.seen[0]["resume_cursor"] == ""
        assert sweeps[0].stopped == ""


class TestEngineWithoutResumeSupport:
    @pytest.mark.asyncio
    async def test_it_is_never_passed_a_resume_cursor(self):
        d = LegacyDiscoverer()

        async def resume_for(keyword, tab):
            return "a-cursor-it-cannot-accept"

        sweeps = await discovery_service._run_incremental(
            d, [("acme", "people")], _noop_done, None, resume_for,
        )
        assert d.seen == [{"keyword": "acme", "tab": "people"}]
        # and crucially the sweep succeeded rather than becoming an error
        assert sweeps[0].stopped == ""

    @pytest.mark.asyncio
    async def test_the_resume_lookup_is_not_even_called_for_it(self):
        d = LegacyDiscoverer()
        calls = []

        async def resume_for(keyword, tab):
            calls.append((keyword, tab))
            return "x"

        await discovery_service._run_incremental(
            d, [("acme", "people")], _noop_done, None, resume_for,
        )
        assert calls == []
