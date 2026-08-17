"""Capture-and-replay primitives (backend/platforms/replay.py).

The one thing that must never happen here is a replayed request that
silently asks for page 1 again: it returns real, well-formed results, adds
nothing new, and is indistinguishable from genuine progress unless the
caller notices. So the cases below care much less about the happy path than
about every way a cursor can fail to be placed -- each of which must return
None (meaning "stay on the scroll path") rather than a request that looks
valid and isn't.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, parse_qsl, urlparse

import pytest

from backend.platforms import replay

MARKER = "result_ids_shown"


def _cursor(page: int) -> str:
    """A cursor shaped like Facebook's: a JSON string carrying the marker
    key that page_state() uses to tell a search cursor from an unrelated
    connection's page_info."""
    return json.dumps({"result_ids_shown": [f"id-{page}"], "page_number": page})


def _post_template(variables: dict, *, extra: dict | None = None) -> replay.RequestTemplate:
    body = {"variables": json.dumps(variables), "doc_id": "12345", "fb_dtsg": "TOKEN"}
    body.update(extra or {})
    from urllib.parse import urlencode
    return replay.RequestTemplate(
        url="https://www.facebook.com/api/graphql/",
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=urlencode(body),
    )


class TestLooksLikeCursor:
    def test_json_object_carrying_the_marker_is_a_cursor(self):
        assert replay.looks_like_cursor(_cursor(3), MARKER) is True

    def test_json_object_without_the_marker_is_not(self):
        assert replay.looks_like_cursor(json.dumps({"other": 1}), MARKER) is False

    def test_plain_string_is_not(self):
        assert replay.looks_like_cursor("not-json", MARKER) is False

    def test_none_and_empty_are_not(self):
        assert replay.looks_like_cursor(None, MARKER) is False
        assert replay.looks_like_cursor("", MARKER) is False

    def test_json_array_is_not_a_cursor(self):
        assert replay.looks_like_cursor(json.dumps([1, 2]), MARKER) is False


class TestFindCursorKey:
    def test_key_is_discovered_by_shape_not_by_name(self):
        # the whole point: a platform renaming this variable must not break
        # resume, because the VALUE still looks like a cursor
        variables = {"count": 10, "someRenamedKey": _cursor(2)}
        assert replay.find_cursor_key(variables, MARKER) == "someRenamedKey"

    def test_first_page_request_falls_back_to_the_conventional_name(self):
        variables = {"count": 10, "cursor": None}
        assert replay.find_cursor_key(variables, MARKER) == "cursor"

    def test_absent_conventional_name_yields_nothing_rather_than_inventing_one(self):
        # inventing a key produces a request the platform ignores -- which
        # returns page 1 and reads as progress. Refusing is the safe answer.
        assert replay.find_cursor_key({"count": 10}, MARKER) is None

    def test_non_dict_yields_nothing(self):
        assert replay.find_cursor_key("nope", MARKER) is None


class TestWithCursorOnFormEncodedPost:
    def test_cursor_is_swapped_and_everything_else_preserved(self):
        template = _post_template({"count": 10, "cursor": _cursor(1)})
        out = replay.with_cursor(template, _cursor(9), marker=MARKER)
        assert out is not None
        body = dict(parse_qsl(out.post_data))
        # the signed parts must survive untouched -- they are the reason
        # this approach works at all
        assert body["doc_id"] == "12345"
        assert body["fb_dtsg"] == "TOKEN"
        assert json.loads(body["variables"])["cursor"] == _cursor(9)

    def test_other_variables_are_left_alone(self):
        template = _post_template({"count": 10, "query": "acme", "cursor": _cursor(1)})
        out = replay.with_cursor(template, _cursor(4), marker=MARKER)
        variables = json.loads(dict(parse_qsl(out.post_data))["variables"])
        assert variables["count"] == 10
        assert variables["query"] == "acme"

    def test_url_method_and_headers_are_unchanged(self):
        template = _post_template({"cursor": _cursor(1)})
        out = replay.with_cursor(template, _cursor(2), marker=MARKER)
        assert out.url == template.url
        assert out.method == "POST"
        assert out.headers == template.headers

    def test_body_without_a_variables_field_refuses(self):
        template = replay.RequestTemplate(
            url="https://www.facebook.com/api/graphql/", method="POST",
            headers={}, post_data="doc_id=1&fb_dtsg=TOKEN",
        )
        assert replay.with_cursor(template, _cursor(1), marker=MARKER) is None

    def test_unparseable_variables_refuses(self):
        template = replay.RequestTemplate(
            url="https://www.facebook.com/api/graphql/", method="POST",
            headers={}, post_data="variables=not-json&doc_id=1",
        )
        assert replay.with_cursor(template, _cursor(1), marker=MARKER) is None

    def test_variables_with_no_placeable_cursor_refuses(self):
        template = _post_template({"count": 10})
        assert replay.with_cursor(template, _cursor(1), marker=MARKER) is None


class TestWithCursorOnQueryStringGet:
    def _get_template(self, variables: dict) -> replay.RequestTemplate:
        from urllib.parse import urlencode
        q = urlencode({"variables": json.dumps(variables), "features": "{}"})
        return replay.RequestTemplate(
            url=f"https://x.com/i/api/graphql/abc/SearchTimeline?{q}",
            method="GET", headers={"authorization": "Bearer X"},
        )

    def test_cursor_is_swapped_in_the_query_string(self):
        template = self._get_template({"rawQuery": "acme", "cursor": _cursor(1)})
        out = replay.with_cursor(template, _cursor(7), marker=MARKER)
        assert out is not None
        variables = json.loads(parse_qs(urlparse(out.url).query)["variables"][0])
        assert variables["cursor"] == _cursor(7)
        assert variables["rawQuery"] == "acme"

    def test_other_query_params_survive(self):
        template = self._get_template({"cursor": _cursor(1)})
        out = replay.with_cursor(template, _cursor(2), marker=MARKER)
        assert parse_qs(urlparse(out.url).query)["features"] == ["{}"]

    def test_query_without_variables_refuses(self):
        template = replay.RequestTemplate(
            url="https://x.com/i/api/graphql/abc/SearchTimeline?features=%7B%7D",
            method="GET", headers={},
        )
        assert replay.with_cursor(template, _cursor(1), marker=MARKER) is None


class TestForbiddenHeadersAreNotResent:
    """The browser supplies these itself, correctly and consistently with
    the session's fingerprint. Echoing captured copies back is at best
    ignored and at worst a mismatch that is its own detection signal."""

    def test_identity_bearing_headers_are_stripped(self):
        sendable = replay._sendable({
            "user-agent": "Mozilla/5.0", "cookie": "c_user=1", "referer": "https://x/",
            "host": "www.facebook.com", "content-length": "10", "origin": "https://x",
        })
        assert sendable == {}

    def test_sec_and_proxy_prefixed_headers_are_stripped(self):
        sendable = replay._sendable({"sec-fetch-site": "same-origin", "proxy-authorization": "x"})
        assert sendable == {}

    def test_application_headers_are_kept(self):
        sendable = replay._sendable({
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": "SearchQuery",
            "authorization": "Bearer X",
        })
        assert sendable == {
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": "SearchQuery",
            "authorization": "Bearer X",
        }


class FakePage:
    def __init__(self, result):
        self._result = result
        self.calls: list = []

    async def evaluate(self, _js, args):
        self.calls.append(args)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class TestReplayExecution:
    @pytest.mark.asyncio
    async def test_successful_response_is_returned(self):
        page = FakePage({"ok": True, "status": 200, "text": '{"data":1}'})
        out = await replay.replay(page, _post_template({"cursor": _cursor(1)}))
        assert out.ok is True
        assert out.status == 200
        assert out.text == '{"data":1}'
        assert out.refused is False

    @pytest.mark.asyncio
    async def test_an_evaluate_failure_is_data_not_an_exception(self):
        page = FakePage(RuntimeError("context destroyed"))
        out = await replay.replay(page, _post_template({"cursor": _cursor(1)}))
        assert out.ok is False
        assert out.status == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
    async def test_session_shaped_statuses_are_flagged_refused(self, status):
        page = FakePage({"ok": True, "status": status, "text": ""})
        out = await replay.replay(page, _post_template({"cursor": _cursor(1)}))
        assert out.refused is True

    @pytest.mark.asyncio
    async def test_a_404_is_not_treated_as_a_session_problem(self):
        # a wrong URL is a bug, not a reason to quarantine a good account
        page = FakePage({"ok": True, "status": 404, "text": ""})
        out = await replay.replay(page, _post_template({"cursor": _cursor(1)}))
        assert out.refused is False

    @pytest.mark.asyncio
    async def test_the_refused_status_maps_to_a_session_reason(self):
        # the value the sweep puts in `error` must be something
        # discovery_service's classify_failure recognises, or the pool never
        # learns the account was blocked
        from backend.shared.resilience import classify_failure

        assert classify_failure("replay refused: http-429") == "rate_limited"
        assert classify_failure("replay refused: http-403") is not None
