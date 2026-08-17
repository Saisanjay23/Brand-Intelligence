"""Capture a search request the page itself made, then re-issue it with a
different pagination cursor.

WHY THIS EXISTS
Every scroll-driven engine here (facebook/, twitter/, tiktok/) paginates by
scrolling the browser and passively intercepting whatever XHR the page's own
JavaScript fires. That works, but it has no entry point other than "page 1":
a sweep that stops on its time budget at page 40 starts again at page 1 on
the next run, so for a broad keyword the tail beyond one budget is
unreachable no matter how many times the sweep is repeated.

Resuming needs the ability to ask for an arbitrary page. The obvious way --
building the search request from scratch -- means reproducing each
platform's request signing (Facebook's `fb_dtsg`/`doc_id`, X's bearer plus
transaction id, TikTok's `X-Bogus`/`msToken`). That is a large amount of
guesswork that breaks whenever a platform rotates its scheme.

This module does the opposite and never signs anything. The page has
already made a correctly-signed paginated request; we capture it verbatim
and swap ONLY the cursor. Everything else -- tokens, doc ids, headers --
is whatever the live session just used.

WHAT IS PERSISTED
Only the cursor string. Never the captured request: `fb_dtsg` and bearer
tokens are session-scoped and short-lived, so a template stored today is
worthless tomorrow. Each run captures a fresh template from a fresh page
load and then jumps it to the stored cursor -- which is why resume survives
a token rotation that a stored-credentials approach would not.

WHY THE REPLAY RUNS INSIDE THE PAGE
`replay()` issues the request via `fetch()` in the page's own context rather
than through Playwright's APIRequestContext. A request originating from the
page carries the real header order, the real TLS fingerprint, the real
referer chain and the real cookie jar. The same request issued out-of-band
carries none of those and is a considerably louder bot signal -- which
matters here more than it usually would, because these sessions are
hand-made accounts that cannot be cheaply replaced (see stealth/).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, parse_qs

from backend.shared.logging import get_logger

log = get_logger("platforms.replay")

# Headers the Fetch spec forbids a caller from setting: the browser supplies
# its own, correctly. Stripping them is not a limitation to work around --
# it is the point. The browser's own User-Agent/Referer/Cookie/sec-* values
# are exactly the ones consistent with this session's fingerprint, whereas
# echoing back captured copies risks a subtle mismatch that is itself a
# detection signal.
_FORBIDDEN_HEADERS = frozenset({
    "accept-charset", "accept-encoding", "access-control-request-headers",
    "access-control-request-method", "connection", "content-length", "cookie",
    "cookie2", "date", "dnt", "expect", "host", "keep-alive", "origin",
    "referer", "te", "trailer", "transfer-encoding", "upgrade", "via",
    "user-agent",
})


def _sendable(headers: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in (headers or {}).items():
        lk = str(k).lower()
        if lk in _FORBIDDEN_HEADERS or lk.startswith(("sec-", "proxy-")):
            continue
        out[str(k)] = str(v)
    return out


@dataclass
class RequestTemplate:
    """One real, already-signed search request, captured as it went out."""

    url: str
    method: str
    headers: dict[str, str]
    post_data: str = ""

    @classmethod
    def from_request(cls, request: Any) -> Optional["RequestTemplate"]:
        """Build from a Playwright Request. Returns None rather than raising:
        a template that cannot be captured is a reason to stay on the scroll
        path, never a reason to fail the sweep."""
        try:
            return cls(
                url=request.url,
                method=(request.method or "GET").upper(),
                headers=dict(request.headers or {}),
                post_data=request.post_data or "",
            )
        except Exception as e:
            log.debug(f"could not capture request template: {type(e).__name__}: {e}")
            return None


def looks_like_cursor(value: Any, marker: str) -> bool:
    """A cursor is a string that JSON-decodes to a dict carrying `marker`.

    `marker` is the platform's own tell -- "result_ids_shown" for Facebook's
    search cursor (see facebook/discovery_engine.py::page_state, which uses
    the identical test to avoid picking up the notification dropdown's
    unrelated page_info). Matching on structure rather than on a key NAME is
    what keeps this working when a platform renames the variable that holds
    the cursor, which is a far more common change than restructuring the
    cursor itself.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(decoded, dict) and marker in decoded


def find_cursor_key(variables: dict, marker: str, fallback: str = "cursor") -> Optional[str]:
    """Which key in a GraphQL `variables` object currently holds the cursor.

    Discovered from the captured request's own contents (the value that
    looks like a cursor wins) instead of being hardcoded, then falling back
    to the conventional name only when the captured request carried no
    cursor at all -- i.e. when it was the FIRST page request rather than a
    paginated one. A caller that needs certainty should capture a template
    from a scroll-triggered request, which always carries one.
    """
    if not isinstance(variables, dict):
        return None
    for key, value in variables.items():
        if looks_like_cursor(value, marker):
            return key
    # No value looks like a cursor: this was a first-page request. Only use
    # the conventional name if the request actually carries it (normally
    # present and null on page 1). Inventing a key that isn't there would
    # produce a request the platform ignores, which returns page 1 again --
    # indistinguishable from real progress and the worst outcome available.
    return fallback if fallback in variables else None


def _swap_in_variables(raw_variables: str, cursor: str, marker: str) -> Optional[str]:
    try:
        variables = json.loads(raw_variables)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(variables, dict):
        return None
    key = find_cursor_key(variables, marker)
    if not key:
        return None
    variables[key] = cursor
    return json.dumps(variables, separators=(",", ":"))


def with_cursor(
    template: RequestTemplate, cursor: str, *, marker: str, variables_field: str = "variables",
) -> Optional[RequestTemplate]:
    """A copy of `template` asking for the page at `cursor`.

    Handles both shapes these platforms use: a form-encoded POST body
    (Facebook) and a query-string GET (X). Returns None when the cursor
    could not be placed -- the caller must then stay on the scroll path
    rather than send a request that would silently re-fetch page 1 and be
    mistaken for real progress.
    """
    if template.method == "POST" and template.post_data:
        pairs = parse_qsl(template.post_data, keep_blank_values=True)
        if not any(k == variables_field for k, _ in pairs):
            return None
        out_pairs = []
        placed = False
        for k, v in pairs:
            if k == variables_field:
                swapped = _swap_in_variables(v, cursor, marker)
                if swapped is None:
                    return None
                out_pairs.append((k, swapped))
                placed = True
            else:
                out_pairs.append((k, v))
        if not placed:
            return None
        return RequestTemplate(
            url=template.url, method=template.method,
            headers=dict(template.headers), post_data=urlencode(out_pairs),
        )

    parts = urlparse(template.url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if variables_field not in query:
        return None
    swapped = _swap_in_variables(query[variables_field][0], cursor, marker)
    if swapped is None:
        return None
    query[variables_field] = [swapped]
    new_query = urlencode([(k, v) for k, vs in query.items() for v in vs])
    return RequestTemplate(
        url=urlunparse(parts._replace(query=new_query)),
        method=template.method, headers=dict(template.headers), post_data="",
    )


# Runs in the page, so the request carries this session's real identity.
# Returns the body as text plus the status, and never throws across the
# bridge: a rejected request must reach the caller as data it can classify
# (a 429 is a session problem, an empty body is a parser problem), not as
# an opaque evaluate() exception.
_JS_REPLAY = """
async ([url, method, headers, body]) => {
  try {
    const init = {method, headers, credentials: 'include'};
    if (method !== 'GET' && method !== 'HEAD') init.body = body;
    const res = await fetch(url, init);
    return {ok: true, status: res.status, text: await res.text()};
  } catch (e) {
    return {ok: false, status: 0, text: String(e && e.message || e)};
  }
}
"""


@dataclass
class ReplayResult:
    status: int
    text: str
    ok: bool

    @property
    def refused(self) -> bool:
        """Status codes that mean the SESSION was rejected, not that the
        parser is wrong. Fed to shared/resilience.py::classify_failure by
        callers so a replayed request feeds the session pool exactly like a
        scrolled one does."""
        return self.status in (401, 403, 429) or self.status >= 500


async def replay(page: Any, template: RequestTemplate) -> ReplayResult:
    """Issue `template` from inside `page`. Never raises."""
    try:
        raw = await page.evaluate(
            _JS_REPLAY,
            [template.url, template.method, _sendable(template.headers), template.post_data],
        )
    except Exception as e:
        log.debug(f"replay evaluate failed: {type(e).__name__}: {e}")
        return ReplayResult(status=0, text="", ok=False)
    if not isinstance(raw, dict):
        return ReplayResult(status=0, text="", ok=False)
    return ReplayResult(
        status=int(raw.get("status") or 0),
        text=str(raw.get("text") or ""),
        ok=bool(raw.get("ok")),
    )
