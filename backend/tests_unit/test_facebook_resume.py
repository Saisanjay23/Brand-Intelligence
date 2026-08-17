"""Facebook discovery's resume path (backend/platforms/facebook/
discovery_engine.py::Discovery.sweep with `resume_cursor`).

Drives the real sweep() against a fake page, because the failure this code
must not have cannot be seen from the outside: a replayed request that
returns page 1 again produces real, well-formed, correctly-parsed results
and looks exactly like progress. The tests that matter here are the ones
asserting the sweep NOTICES it is not advancing and falls back, and that it
reports its stopping position honestly for the next run.
"""

from __future__ import annotations

import json

import pytest

from backend.platforms.facebook import discovery_engine as fb
from backend.platforms.scan_options import DiscoveryOptions


def _cursor(page: int, *, has_next: bool = True, end_of_serp: bool = False) -> str:
    return json.dumps({
        "result_ids_shown": [f"shown-{page}"],
        "page_number": page,
        "is_end_of_serp": end_of_serp,
    })


def _payload(ids: list[str], *, page: int, has_next: bool = True, end_of_serp: bool = False) -> dict:
    """One pagination response: some result edges plus the page_info whose
    end_cursor drives everything."""
    return {
        "data": {
            "search": {
                "edges": [
                    {
                        "rendering_strategy": {
                            "view_model": {
                                "__typename": "SearchProfileViewModel",
                                "profile": {
                                    "__typename": "User",
                                    "id": eid,
                                    "name": f"Name {eid}",
                                    "profile_url": f"https://www.facebook.com/{eid}",
                                    "profile_picture": {"uri": f"https://cdn/{eid}.jpg"},
                                },
                            }
                        }
                    }
                    for eid in ids
                ],
                "page_info": {
                    "has_next_page": has_next,
                    "end_cursor": _cursor(page, has_next=has_next, end_of_serp=end_of_serp),
                },
            }
        }
    }


class FakePage:
    """Just enough Playwright Page for sweep(). `replies` is the queue of
    responses the in-page fetch returns, one per replay call."""

    def __init__(self, replies: list[dict]):
        self.replies = list(replies)
        self.replay_calls: list = []
        self.scrolls = 0
        self.closed = False

    async def goto(self, *_a, **_k):
        return None

    async def wait_for_function(self, *_a, **_k):
        return None

    async def wait_for_timeout(self, *_a, **_k):
        return None

    def on(self, *_a, **_k):
        return None

    async def close(self):
        self.closed = True

    async def evaluate(self, js, arg=None):
        if "fetch(" in js:  # the replay bridge
            self.replay_calls.append(arg)
            if not self.replies:
                return {"ok": True, "status": 200, "text": ""}
            return self.replies.pop(0)
        if "scrollTo" in js:
            self.scrolls += 1
            return None
        return []  # JS_EMBEDDED: no server-rendered first page in these tests


class FakeCtx:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


def _reply(payload: dict, status: int = 200) -> dict:
    return {"ok": True, "status": status, "text": json.dumps(payload)}


def _template() -> fb.replay.RequestTemplate:
    from urllib.parse import urlencode
    return fb.replay.RequestTemplate(
        url="https://www.facebook.com/api/graphql/",
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=urlencode({
            "variables": json.dumps({"count": 10, "cursor": _cursor(0)}),
            "doc_id": "1", "fb_dtsg": "TOKEN",
        }),
    )


async def _sweep(page: FakePage, *, resume_cursor: str, template=None, **opts):
    """Runs the real sweep, with the request template pre-seeded the way a
    live page's own search XHR would have seeded it."""
    d = fb.Discovery(DiscoveryOptions(**opts), FakeCtx(page))
    original = fb.replay.RequestTemplate.from_request
    fb.replay.RequestTemplate.from_request = staticmethod(lambda _r: template)
    try:
        # sweep() captures its template from an intercepted response; with no
        # real network here, inject it by having the sweep see one directly
        return await _run_with_template(d, page, resume_cursor, template)
    finally:
        fb.replay.RequestTemplate.from_request = original


async def _run_with_template(d, page, resume_cursor, template):
    """sweep() only ever sets `template` from on_response. There is no
    network in these tests, so drive the same effect by making the first
    replay reply also stand in for that capture: patch the module-level
    helper the sweep uses to build a jumped request so it uses our template
    regardless of what was captured."""
    real_with_cursor = fb.replay.with_cursor

    def _with_cursor(_t, cursor, **kw):
        return real_with_cursor(template, cursor, **kw) if template else None

    fb.replay.with_cursor = _with_cursor
    try:
        return await d.sweep("acme", "people", resume_cursor=resume_cursor)
    finally:
        fb.replay.with_cursor = real_with_cursor


class TestResumeAdvancesThroughPages:
    @pytest.mark.asyncio
    async def test_it_replays_from_the_stored_cursor(self):
        page = FakePage([_reply(_payload(["1", "2"], page=5, has_next=False))])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert sweep.resumed_from == _cursor(4)
        # the request actually sent carried the STORED cursor, not page 1's
        sent_body = page.replay_calls[0][3]
        from urllib.parse import parse_qsl
        variables = json.loads(dict(parse_qsl(sent_body))["variables"])
        assert variables["cursor"] == _cursor(4)

    @pytest.mark.asyncio
    async def test_results_from_replayed_pages_are_kept(self):
        page = FakePage([_reply(_payload(["1", "2"], page=5, has_next=False))])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert {h.entity_id for h in sweep.hits} == {"1", "2"}

    @pytest.mark.asyncio
    async def test_it_follows_the_chain_of_cursors(self):
        page = FakePage([
            _reply(_payload(["1"], page=5)),
            _reply(_payload(["2"], page=6)),
            _reply(_payload(["3"], page=7, has_next=False)),
        ])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert sweep.replayed_pages == 3
        assert {h.entity_id for h in sweep.hits} == {"1", "2", "3"}

    @pytest.mark.asyncio
    async def test_reaching_the_end_marks_the_sweep_complete(self):
        page = FakePage([_reply(_payload(["1"], page=5, has_next=False))])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert sweep.complete is True
        assert sweep.stopped == "exhausted"

    @pytest.mark.asyncio
    async def test_end_of_serp_is_reported_distinctly(self):
        page = FakePage([_reply(_payload(["1"], page=5, has_next=False, end_of_serp=True))])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert sweep.stopped == "end-of-serp"


class TestResumeRefusesToPretendItAdvanced:
    @pytest.mark.asyncio
    async def test_a_cursor_that_returns_nothing_new_abandons_the_resume(self):
        # the silent-page-1 case: well-formed results, none of them new
        page = FakePage([
            _reply(_payload(["1"], page=5)),
            _reply(_payload(["1"], page=5)),  # same ids again
        ])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        # resumed_from is cleared, so the caller cannot mistake this for a
        # successful resume, and the scroll path took over
        assert sweep.resumed_from == ""

    @pytest.mark.asyncio
    async def test_a_refused_replay_is_recorded_as_a_session_problem(self):
        from backend.shared.resilience import classify_failure

        page = FakePage([{"ok": True, "status": 429, "text": ""}])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert sweep.stopped == "error"
        assert classify_failure(sweep.error) == "rate_limited"

    @pytest.mark.asyncio
    async def test_no_usable_template_falls_back_to_scrolling(self):
        page = FakePage([])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=None)
        # never replayed, and the scroll loop ran instead
        assert page.replay_calls == []
        assert sweep.replayed_pages == 0
        assert page.scrolls > 0


class TestStoppingPositionIsReported:
    @pytest.mark.asyncio
    async def test_a_sweep_with_more_pages_left_reports_where_to_resume(self):
        page = FakePage([_reply(_payload(["1"], page=5))])
        sweep = await _sweep(
            page, resume_cursor=_cursor(4), template=_template(), max_seconds=0.001,
        )
        assert sweep.end_cursor == _cursor(5)

    @pytest.mark.asyncio
    async def test_an_exhausted_sweep_reports_no_resume_position(self):
        # nothing past the end to resume into; the next run must start fresh
        page = FakePage([_reply(_payload(["1"], page=5, has_next=False))])
        sweep = await _sweep(page, resume_cursor=_cursor(4), template=_template())
        assert sweep.end_cursor == ""


class TestResumeIsOptIn:
    @pytest.mark.asyncio
    async def test_without_a_cursor_the_sweep_never_replays(self):
        page = FakePage([])
        sweep = await _sweep(page, resume_cursor="", template=_template())
        assert page.replay_calls == []
        assert sweep.resumed_from == ""
        assert page.scrolls > 0
