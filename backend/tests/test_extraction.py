"""Ordered extraction strategies + blame reporting.

The behaviour under test is what makes a silent parser break diagnosable:
first-usable-strategy wins, and every strategy that failed is reported with
the exact project file and line to open.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.shared.extraction import (ExtractionResult, run_strategies,
                                        _default_is_empty)


class TestOrdering:
    @pytest.mark.asyncio
    async def test_first_strategy_wins_and_later_ones_never_run(self):
        ran: list[str] = []

        def primary():
            ran.append("primary")
            return "from-network"

        def secondary():
            ran.append("secondary")  # pragma: no cover - must not happen
            return "from-dom"

        res = await run_strategies("name", [("network", primary), ("dom", secondary)])
        assert res.value == "from-network"
        assert res.strategy == "network"
        assert res.failures == []
        assert ran == ["primary"], "a working primary must not cost a DOM pass"

    @pytest.mark.asyncio
    async def test_falls_through_to_dom_when_network_raises(self):
        def primary():
            raise KeyError("edges")

        res = await run_strategies("name", [("network", primary), ("dom", lambda: "x")])
        assert res.value == "x"
        assert res.strategy == "dom"
        assert res.degraded is True, "succeeding on a fallback is still degraded"

    @pytest.mark.asyncio
    async def test_falls_through_when_a_strategy_is_silently_empty(self):
        # the actual drift signature: no exception, just nothing recognised
        res = await run_strategies("name", [("network", lambda: []), ("dom", lambda: "x")])
        assert res.value == "x"
        assert res.failures[0].reason == "returned nothing"

    @pytest.mark.asyncio
    async def test_async_strategies_are_supported(self):
        async def primary():
            raise ValueError("payload shape changed")

        async def secondary():
            await asyncio.sleep(0)
            return "recovered"

        res = await run_strategies("name", [("network", primary), ("dom", secondary)])
        assert res.value == "recovered"


class TestBlameReporting:
    @pytest.mark.asyncio
    async def test_names_the_project_file_and_line_that_raised(self):
        def primary():
            payload: dict = {}
            return payload["edges"]  # the line an engineer must open

        res = await run_strategies("name", [("network", primary), ("dom", lambda: "x")])
        f = res.failures[0]
        assert f.file.endswith("test_extraction.py"), f.file
        assert f.line > 0
        assert "KeyError" in f.reason
        assert "payload[" in f.source, f.source

    @pytest.mark.asyncio
    async def test_blames_project_code_not_library_internals(self):
        # json.loads raises deep inside the stdlib; reporting json/decoder.py
        # would point at a file nobody in this repo can fix
        import json

        def primary():
            return json.loads("not json at all")

        res = await run_strategies("name", [("network", primary), ("dom", lambda: "x")])
        f = res.failures[0]
        assert "backend" in f.file or f.file.endswith("test_extraction.py"), f.file
        assert "decoder.py" not in f.file

    @pytest.mark.asyncio
    async def test_empty_strategy_is_blamed_at_its_own_definition(self):
        def finds_nothing():
            return None

        res = await run_strategies("name", [("network", finds_nothing), ("dom", lambda: "x")])
        f = res.failures[0]
        assert f.line > 0
        assert "finds_nothing" in f.function

    @pytest.mark.asyncio
    async def test_report_is_human_readable_and_mentions_every_failure(self):
        res = await run_strategies("followers", [
            ("network", lambda: (_ for _ in ()).throw(KeyError("count"))),
            ("dom", lambda: None),
        ])
        assert not res.ok
        text = res.report()
        assert "Every extraction strategy failed" in text
        assert "[network]" in text and "[dom]" in text


class TestTotalFailure:
    @pytest.mark.asyncio
    async def test_all_failing_returns_not_ok_rather_than_raising(self):
        # a dead field must not sink the whole profile -- the caller decides
        res = await run_strategies("loc", [("a", lambda: None), ("b", lambda: "")])
        assert res.ok is False
        assert res.value is None
        assert len(res.failures) == 2

    @pytest.mark.asyncio
    async def test_cancellation_is_never_swallowed(self):
        # a cancelled job must stay cancelled, not fall through and keep
        # driving the browser
        def cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await run_strategies("x", [("network", cancelled), ("dom", lambda: "x")])


class TestEmptiness:
    def test_zero_and_false_are_real_readings_not_emptiness(self):
        # a genuinely-zero follower count must not be discarded in favour of
        # a weaker strategy's guess
        assert _default_is_empty(0) is False
        assert _default_is_empty(False) is False

    def test_none_and_empty_containers_are_empty(self):
        for v in (None, "", [], {}, set(), ()):
            assert _default_is_empty(v) is True, v

    @pytest.mark.asyncio
    async def test_zero_result_is_accepted_and_stops_the_chain(self):
        res = await run_strategies("followers", [("network", lambda: 0), ("dom", lambda: 999)])
        assert res.value == 0
        assert res.strategy == "network"
