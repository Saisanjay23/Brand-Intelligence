"""Regression coverage for two related Facebook discovery bugs:

1. Some cards showed a bare numeric id with no photo (discovery_source
   "id-backfill"/"processed-not-shown" -- ids Facebook's search matched but
   never rendered as a full edge, so name/avatar were never even
   attempted). _extract_entity (used by Discovery._resolve_missing) reads
   a profile page's own embedded payloads for that id's NAME -- these
   tests pin its scoping so a future change can't quietly start attaching
   the WRONG profile's name to a candidate, which would surface as a
   false-positive impersonation match (a bogus high name-score badge on a
   profile that was never actually a match) rather than the honest "no
   name available" a scoping miss should produce.

2. AVATAR recovery from a profile-page resolve visit is, as of this file,
   OFF BY DEFAULT (TRUST_PAGE_CONTEXT_AVATAR = False) after three
   different attempts at scoping it safely (id-scoping, identity
   confirmation, structural RenderedProfile markers) were each
   individually confirmed live to still leak the SCRAPER'S OWN photo onto
   a candidate for a privacy-restricted profile -- Facebook substitutes
   the viewer's own photo into every picture field it renders (JSON AND
   DOM alike) when it can't show a non-friend the real, restricted photo,
   and no combination of signals tested reliably told a genuine photo
   apart from a substituted one in every case. This module's own stated
   rule -- blank beats wrong, always -- applies to its logical conclusion:
   name-only. The extraction mechanism itself is kept and tested (passing
   trust_page_context_avatar=True explicitly) so a FUTURE reliable
   positive signal can re-enable it without rebuilding the plumbing, but
   the default-off behavior is the one regression test that must never
   silently regress.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.platforms.facebook.discovery_engine import (
    Discovery, GENERIC_NAMES, RE_DEFAULT_PIC, TRUST_PAGE_CONTEXT_AVATAR,
    _extract_entity, _photo_asset_id, kind_for_tab, profile_url_for,
)


def test_extracts_name_and_avatar_for_the_matching_id():
    """The extraction mechanism itself, exercised with trust explicitly on
    -- currently dormant in production (see module docstring) but kept
    correct for when a future signal re-enables it."""
    blobs = [
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
    ]
    name, avatar, has_custom = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fbcdn.net/pic.jpg"
    assert has_custom is True


def test_avatar_never_extracted_by_default_even_when_a_real_photo_is_present():
    """THE regression test for today's final architecture decision. A
    dict with a perfectly clean, unambiguous, id-scoped photo must still
    come back with a blank avatar under the DEFAULT call -- because there
    is currently no reliable way to tell that clean-looking case apart
    from a privacy-substituted one from the payload shape alone (see
    module docstring). Name extraction is unaffected."""
    blobs = [
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
    ]
    name, avatar, has_custom = _extract_entity(blobs, "12345")  # no trust_page_context_avatar passed
    assert name == "Jane Doe"
    assert avatar == "", "avatar recovery must be off by default -- see TRUST_PAGE_CONTEXT_AVATAR"
    assert has_custom is False


def test_trust_page_context_avatar_constant_defaults_off():
    """Guards against someone silently flipping the module-level default
    back on without deliberately re-examining the incident this closes."""
    assert TRUST_PAGE_CONTEXT_AVATAR is False


def test_ignores_other_entities_on_the_same_page():
    """The page for id 12345 also mentions a suggested friend (99999) --
    that name/photo must never leak onto the 12345 candidate. This is the
    exact false-positive-impersonation risk: a wrong name here could
    coincidentally read as a closer keyword match than the profile
    actually is."""
    blobs = [
        {"id": "99999", "name": "Someone Else", "profile_picture": {"uri": "https://scontent.fbcdn.net/other.jpg"}},
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
    ]
    name, avatar, _ = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fbcdn.net/pic.jpg"


def test_blank_when_no_dict_matches_the_id():
    """Blank beats wrong: an id this can't verify must never fall back to
    guessing from whatever's on the page."""
    blobs = [{"id": "99999", "name": "Someone Else"}]
    name, avatar, has_custom = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert name == ""
    assert avatar == ""
    assert has_custom is False


def test_placeholder_avatar_never_counts_as_a_custom_photo():
    blobs = [{"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://static.xx.fbcdn.net/rsrc.php/silhouette.png"}}]
    name, avatar, has_custom = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert name == "Jane Doe"
    assert avatar == ""
    assert has_custom is False


def test_extracts_avatar_from_profile_page_specific_picture_keys():
    """Regression test for a confirmed real bug (in the dormant, trust=True
    extraction mechanism): a SEARCH result snippet carries the photo under
    `profile_picture`, but a profile PAGE's own embedded JSON never uses
    that key at all -- the identical uploaded photo shows up under
    profilePicLarge/Medium/Small instead. A resolve visit was reading a
    key that structurally cannot exist on the page it reads, so it always
    came back with no avatar -- confirmed live on an actual stuck Facebook
    card, where the name resolved correctly from the very same id-scoped
    dict but the picture silently vanished."""
    blobs = [{
        "id": "12345", "name": "Jane Doe",
        "profilePicLarge": {"uri": "https://scontent.fblr8-1.fna.fbcdn.net/v/t1.30497-1/real_photo_n.png"},
    }]
    name, avatar, has_custom = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fblr8-1.fna.fbcdn.net/v/t1.30497-1/real_photo_n.png"
    assert has_custom is True


def test_picture_key_priority_prefers_largest_resolution():
    blobs = [{
        "id": "12345", "name": "Jane Doe",
        "profilePicSmall": {"uri": "https://scontent.fbcdn.net/small.jpg"},
        "profilePicLarge": {"uri": "https://scontent.fbcdn.net/large.jpg"},
    }]
    _, avatar, _ = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert avatar == "https://scontent.fbcdn.net/large.jpg"


def test_page_context_picture_keys_ignored_without_identity_confirmation():
    """id-scoping alone is NOT enough trust for these keys -- the caller
    must explicitly opt in via trust_page_context_avatar (which nothing in
    production ever does; see module docstring)."""
    blobs = [{
        "id": "12345", "name": "Jane Doe",
        "profilePicLarge": {"uri": "https://scontent.fbcdn.net/viewers-own-photo.jpg"},
    }]
    name, avatar, has_custom = _extract_entity(blobs, "12345")  # trust defaults False
    assert name == "Jane Doe"
    assert avatar == "", "an unconfirmed page-context picture key must never be trusted"
    assert has_custom is False


def test_default_pic_regex_no_longer_flags_the_t1_30497_1_tag_as_a_placeholder():
    """Regression test: `t1.30497-1` used to be treated as Facebook's own
    silhouette asset tag. Verified wrong against 500 confirmed-real photos
    already in production data -- every one used tag t39.30808-1 or
    t1.6435-1, never t1.30497-1 -- while a profile page's own
    profilePicLarge field serves that SAME real uploaded photo (same
    scontent host, same realistic random-id filename) tagged t1.30497-1
    instead. It's a different CDN rendering-context tag for the identical
    photo, not a default-avatar marker; matching on it discarded a real
    photo URL entirely rather than just flagging it."""
    real_photo_url = (
        "https://scontent.fblr8-1.fna.fbcdn.net/v/t1.30497-1/"
        "453178253_471506465671661_2781666950760530985_n.png?stp=dst-png&cstp=mx2048x2048&ctp=s200x200"
    )
    assert not RE_DEFAULT_PIC.search(real_photo_url), (
        "a real profile-page photo must not be misclassified as the default silhouette"
    )
    # the genuine default-silhouette patterns must still be caught
    assert RE_DEFAULT_PIC.search("https://static.xx.fbcdn.net/rsrc.php/v3/y6/r/dOWi9zYW9ck.png")


def test_generic_chrome_name_is_rejected_not_treated_as_the_profile_name():
    """A login wall / checkpoint page's own boilerplate ("Facebook",
    "Notifications") must never be mistaken for a profile's real name --
    _resolve_missing also has its own RE_LOGIN/RE_CHECKPOINT/RE_GONE guard
    before ever calling this, but the name-level check here is a second,
    independent line of defense against the same false-positive risk."""
    for generic in GENERIC_NAMES:
        blobs = [{"id": "12345", "name": generic.title()}]
        name, _, _ = _extract_entity(blobs, "12345")
        assert name == "", f"{generic!r} should have been rejected as a real name"


class _FakePage:
    """Just enough of Playwright's Page surface for _resolve_missing's one()
    to run without a real browser -- no embedded payloads, no DOM, so every
    id resolves blank. This test isn't about WHAT gets resolved (that's
    _extract_entity's job, covered above); it's about WHETHER an id gets a
    resolve attempt AT ALL."""

    def __init__(self, visited: list[str]):
        self._visited = visited

    def on(self, _event, _handler):
        pass

    async def goto(self, url, wait_until=None, timeout=None):
        self._visited.append(url)

    async def wait_for_timeout(self, _ms):
        pass

    async def wait_for_function(self, _js, timeout=None):
        # a page that never renders profile data -- the real Playwright
        # call would time out here; raising mirrors that so the code under
        # test takes its "extract whatever arrived anyway" path
        raise TimeoutError("never became ready")

    async def inner_text(self, _selector):
        return ""

    async def evaluate(self, _js):
        return []

    async def title(self):
        return ""

    async def close(self):
        pass


class _SelfProbePage:
    """Stands in for the ONE extra page visit _resolve_missing now makes per
    batch, to _photo_asset_id-fingerprint the scraper's own current avatar
    (see Discovery._self_avatar_asset). Returns no avatar at all, so
    self_avatar_asset comes back "" and every existing test's assertions
    about the REAL per-id resolve pages stay exactly as they were --
    without this, that extra visit would land in the same shared
    ctx.visited/ready_waits counters those tests check."""

    def on(self, _event, _handler):
        pass

    async def goto(self, _url, wait_until=None, timeout=None):
        pass

    async def wait_for_function(self, _js, timeout=None):
        pass

    async def evaluate(self, _js):
        return ""

    async def close(self):
        pass


class _FakeCtx:
    def __init__(self):
        self.visited: list[str] = []
        self._handed_out = 0

    async def new_page(self):
        self._handed_out += 1
        if self._handed_out == 1:
            return _SelfProbePage()
        return _FakePage(self.visited)


class _WrongPageFakePage:
    """Simulates landing somewhere that ISN'T eid's own profile -- e.g. the
    scraping account's own logged-in home feed, which Facebook can land on
    silently (no login-wall/checkpoint/"isn't available" text to trip the
    existing guard) if `eid` was invalid or the request otherwise didn't
    resolve as expected. The page has a real, juicy title and a real,
    large DOM avatar available -- exactly what a scraper's own account nav
    chrome looks like -- but its canonical/og:url does NOT reference eid.
    """

    def __init__(self, canonical_url: str, title: str, dom_avatar: str, embedded: list | None = None):
        self._canonical_url = canonical_url
        self._title = title
        self._dom_avatar = dom_avatar
        # raw strings JS_EMBEDDED would return (each a JSON document) --
        # lets a test give the page its own id-scoped payload record, which
        # is what the Pages identity path reads (see _entity_url_path)
        self._embedded = embedded or []
        self.ready_waits = 0

    def on(self, _event, _handler):
        pass

    async def goto(self, _url, wait_until=None, timeout=None):
        pass

    async def wait_for_timeout(self, _ms):
        pass

    async def wait_for_function(self, _js, timeout=None):
        self.ready_waits += 1

    async def inner_text(self, _selector):
        return ""  # no login/checkpoint/gone text -- looks like a normal, live page

    async def evaluate(self, js):
        if "canonical" in js:
            return self._canonical_url
        if "svg image" in js:
            return self._dom_avatar
        return self._embedded  # JS_EMBEDDED

    async def title(self):
        return self._title

    async def close(self):
        pass


class _SinglePageCtx:
    def __init__(self, page):
        self._page = page
        self._handed_out = 0

    async def new_page(self):
        self._handed_out += 1
        if self._handed_out == 1:
            return _SelfProbePage()  # see its docstring
        return self._page


def test_resolve_missing_never_attaches_a_different_pages_identity_to_the_candidate():
    """Regression test for the reported incident: the scraping account's
    OWN photo and name showed up on a discovery card. Root cause -- both
    fallbacks (page title, DOM avatar scan) had no verification that the
    page they landed on was actually eid's profile; they'd happily report
    whatever was on whatever page loaded, including the logged-in
    scraper's own nav-bar avatar/name if a bad id silently redirected
    somewhere else. This pins the fix: fallback data is only trusted once
    the page's own canonical/og:url confirms it matches eid -- otherwise
    the candidate must come back blank, never populated with someone
    else's identity."""
    target_eid = "555000111"
    wrong_page = _WrongPageFakePage(
        canonical_url="https://www.facebook.com/some.other.account",
        title="Jane Analyst",
        dom_avatar="https://scontent.fbcdn.net/analysts-own-photo.jpg",
    )
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=_SinglePageCtx(wrong_page))

    resolved = asyncio.run(d._resolve_missing([target_eid], "profile"))

    hit = resolved[target_eid]
    assert hit.name == "", f"must not attach the wrong page's name, got {hit.name!r}"
    assert hit.avatar == "", f"must not attach the wrong page's avatar, got {hit.avatar!r}"
    assert hit.has_custom_pic is False


def test_resolve_missing_still_uses_fallback_name_when_the_page_is_confirmed_correct():
    """Positive control for the test above -- the identity check must not
    make every resolve blank. When the canonical/og:url DOES confirm this
    is eid's own profile, the page-title name fallback still works.
    Avatar stays blank regardless -- there is no DOM-avatar fallback any
    more (see module docstring: confirmed live that even an identity-
    confirmed page's rendered avatar can be a viewer-substituted photo,
    not the target's own)."""
    target_eid = "555000111"
    right_page = _WrongPageFakePage(
        canonical_url=f"https://www.facebook.com/profile.php?id={target_eid}",
        title="Real Target Name | Facebook",
        dom_avatar="https://scontent.fbcdn.net/real-target-photo.jpg",
    )
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=_SinglePageCtx(right_page))

    resolved = asyncio.run(d._resolve_missing([target_eid], "profile"))

    hit = resolved[target_eid]
    assert hit.name == "Real Target Name"
    assert hit.avatar == "", "there is no DOM-avatar fallback any more -- avatar must stay blank"
    assert hit.has_custom_pic is False


def test_resolve_missing_visits_every_candidate_not_just_the_first_60():
    """Regression test for the actual bug: `_resolve_missing` used to slice
    its input to `ids[:60]` (RESOLVE_MAX), a hard count cap. Since ids are
    sorted, that meant the SAME candidates past the 60th lost their
    resolve attempt on every single re-sweep, forever, not a rotating
    sample -- exactly the "some cards permanently show a bare id/no photo
    even though the real profile clearly has both" bug. There must be no
    count-based cap: every id handed to this function has to get a visit."""
    ids = [str(i) for i in range(10_000, 10_200)]  # 200 ids -- well past the old 60-item cap
    ctx = _FakeCtx()
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=ctx)

    asyncio.run(d._resolve_missing(ids, "profile"))

    visited_ids = {url.rsplit("=", 1)[-1] for url in ctx.visited}
    assert visited_ids == set(ids), (
        f"expected all {len(ids)} candidates to get a resolve visit, only {len(visited_ids)} did"
    )


def test_resolve_waits_for_the_page_to_render_instead_of_a_fixed_sleep():
    """Regression test for the timing failure behind "the card shows a bare
    id but opening the profile by hand plainly shows a name and photo".
    Resolution used to run after a flat `wait_for_timeout(1200)` -- a race,
    not a wait: any profile that took longer than 1.2s to render (slow
    connection, heavy profile, lazy-loaded avatar) got extracted from a
    half-built page and resolved blank. This pins that the resolve step
    actually waits on a page-readiness CONDITION."""
    page = _WrongPageFakePage(
        canonical_url="https://www.facebook.com/profile.php?id=555000111",
        title="Real Target Name | Facebook",
        dom_avatar="https://scontent.fbcdn.net/real-target-photo.jpg",
    )
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=_SinglePageCtx(page))

    asyncio.run(d._resolve_missing(["555000111"], "profile"))

    assert page.ready_waits == 1, "resolve must wait for the page to actually render"


def test_resolve_still_extracts_when_the_page_never_signals_ready():
    """The readiness wait is a ceiling, not a precondition -- a profile that
    never fully renders must still be extracted from whatever DID arrive
    (blank beats wrong, but partial beats blank). _FakePage's
    wait_for_function raises, standing in for that timeout."""
    ids = ["10001", "10002"]
    ctx = _FakeCtx()
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=ctx)

    resolved = asyncio.run(d._resolve_missing(ids, "profile"))

    assert set(resolved) == set(ids), "a readiness timeout must not drop the candidate"


def test_page_identity_confirmed_via_its_own_payload_link_not_just_canonical():
    """A Facebook PAGE reached by numeric id redirects to its vanity URL, so
    the canonical (/AdaniGroup) never contains the numeric eid and
    profile_id(canonical) returns the slug instead. Gating the name/photo
    fallbacks on canonical-vs-eid alone therefore rejected every Page --
    silently disabling resolution for exactly the entity type that needed
    it most (the numbered Page cards). Matching the canonical against the
    id-scoped link the page's OWN payload publishes for eid confirms
    identity just as strictly."""
    eid = "61550474825599"
    page = _WrongPageFakePage(
        canonical_url="https://www.facebook.com/AdaniGroup",
        title="Adani Group | Facebook",
        dom_avatar="https://scontent.fbcdn.net/adani-logo.jpg",
        embedded=[f'{{"id": "{eid}", "profile_url": "https://www.facebook.com/AdaniGroup"}}'],
    )
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=_SinglePageCtx(page))

    hit = asyncio.run(d._resolve_missing([eid], "page"))[eid]

    assert hit.name == "Adani Group"
    assert hit.avatar == "", "no DOM-avatar fallback any more -- see module docstring"
    assert hit.has_custom_pic is False


def test_page_identity_still_rejected_when_the_payload_link_does_not_match():
    """Negative control for the test above -- the vanity-URL path must not
    become a way for ANY page to self-certify. A payload whose eid record
    points somewhere other than the canonical is not confirmation, so the
    unscoped title/DOM fallbacks stay off and the candidate comes back
    blank rather than wearing another entity's name and photo."""
    eid = "61550474825599"
    page = _WrongPageFakePage(
        canonical_url="https://www.facebook.com/SomeoneElse",
        title="Someone Else | Facebook",
        dom_avatar="https://scontent.fbcdn.net/someone-elses-photo.jpg",
        embedded=[f'{{"id": "{eid}", "profile_url": "https://www.facebook.com/AdaniGroup"}}'],
    )
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=_SinglePageCtx(page))

    hit = asyncio.run(d._resolve_missing([eid], "page"))[eid]

    assert hit.name == "", f"must not attach the wrong page's name, got {hit.name!r}"
    assert hit.avatar == "", f"must not attach the wrong page's avatar, got {hit.avatar!r}"


def test_profile_url_for_uses_profile_php_only_for_people():
    assert profile_url_for("12345") == "https://www.facebook.com/profile.php?id=12345"
    assert profile_url_for("12345", "profile") == "https://www.facebook.com/profile.php?id=12345"


def test_profile_url_for_pages_never_uses_profile_php():
    """Regression test: Pages don't resolve at profile.php?id= -- that URL
    is personal-profile-only, so visiting it with a Page's numeric id lands
    on Facebook's "content isn't available" page. Every candidate this
    fired for then read as blocked/unresolved forever -- a Facebook Pages
    card permanently stuck on its bare numeric id and no photo, no matter
    how many times it got re-swept. Pages resolve at the bare id path."""
    assert profile_url_for("61550474825599", "page") == "https://www.facebook.com/61550474825599"


def test_resolve_missing_visits_the_page_url_not_profile_php_for_pages():
    """Same regression as above, exercised through _resolve_missing --
    pins that the actual browser navigation (not just profile_url_for in
    isolation) uses the correct URL for a Pages-tab candidate."""
    ids = ["61550474825599"]
    ctx = _FakeCtx()
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=ctx)

    asyncio.run(d._resolve_missing(ids, "page"))

    assert ctx.visited == ["https://www.facebook.com/61550474825599"]


def test_profile_url_for_groups_uses_the_groups_path():
    """Same regression class as Pages above -- a group id must resolve at
    /groups/<id>/, never profile.php?id= (personal-profile-only) or the
    bare Pages path (a different entity entirely)."""
    assert profile_url_for("152272458887295", "group") == "https://www.facebook.com/groups/152272458887295/"


def test_resolve_missing_visits_the_groups_url_for_groups():
    ids = ["152272458887295"]
    ctx = _FakeCtx()
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=ctx)

    asyncio.run(d._resolve_missing(ids, "group"))

    assert ctx.visited == ["https://www.facebook.com/groups/152272458887295/"]


class TestKindForTab:
    def test_people_tab_is_profile(self):
        assert kind_for_tab("people") == "profile"

    def test_pages_tab_is_page(self):
        assert kind_for_tab("pages") == "page"

    def test_groups_tab_is_group(self):
        assert kind_for_tab("groups") == "group"

    def test_unknown_tab_falls_back_to_profile(self):
        assert kind_for_tab("something-new") == "profile"


RENDERED_EDGE_ID = "111"      # a real rendered search result
RENDERED_UNPARSED_ID = "222"  # Facebook rendered it; we failed to read its edge
NEVER_SHOWN_ID = "999"        # backend matched it, Facebook never displayed it


def _search_payload() -> str:
    """One pagination response: one parseable edge, a result_ids_shown that
    also names a second (unparsed) rendered id, and a processed_unicorn_ids
    naming a third id Facebook never put on screen."""
    cursor = json.dumps({
        "result_ids_shown": [RENDERED_EDGE_ID, RENDERED_UNPARSED_ID],
        "is_end_of_serp": True,
        "flow_cursors_serialized": {"t": json.dumps({"processed_unicorn_ids": [NEVER_SHOWN_ID]})},
    })
    return json.dumps({"data": {"serpResponse": {"results": {
        "edges": [{"rendering_strategy": {"view_model": {
            "__typename": "SearchProfileViewModel",
            "profile": {
                "__typename": "User", "id": RENDERED_EDGE_ID, "name": "Real Person",
                "profile_url": "https://www.facebook.com/real.person",
                "profile_picture": {"uri": "https://scontent.fbcdn.net/real.jpg"},
            },
        }}}],
        "page_info": {"has_next_page": False, "end_cursor": cursor},
    }}}})


class _SearchFakePage:
    """The search-results page for one sweep()."""

    def __init__(self):
        self.payload = _search_payload()

    def on(self, _event, _handler):
        pass

    async def goto(self, _url, wait_until=None, timeout=None):
        pass

    async def wait_for_function(self, _js, timeout=None):
        pass

    async def wait_for_timeout(self, _ms):
        pass

    async def inner_text(self, _selector):
        return ""

    async def evaluate(self, js):
        if "application/json" in js:
            return [self.payload]  # JS_EMBEDDED -- first page is server-rendered
        return None  # scrollTo

    async def title(self):
        return ""

    async def close(self):
        pass


class _SweepCtx:
    """First new_page() is the search page; every later one is a resolve
    visit that finds nothing (so backfilled ids stay blank-named)."""

    def __init__(self):
        self.search_page = _SearchFakePage()
        self._handed_out = 0
        self.resolve_visits: list[str] = []

    async def new_page(self):
        self._handed_out += 1
        if self._handed_out == 1:
            return self.search_page
        return _FakePage(self.resolve_visits)


def _sweep_args():
    return SimpleNamespace(
        timeout=5, settle=1, page_wait=0.01, patience=1, concurrency=1,
        progress_every=100, max_results=0, max_pages=0, max_seconds=0,
    )


def test_sweep_never_returns_ids_facebook_chose_not_to_display():
    """THE regression test for the numbered-card bug.

    `processed_unicorn_ids` is Facebook's internal candidate bookkeeping --
    entities its search matched and then filtered out before display
    (deactivated, privacy-restricted, blocked to this viewer, deduped).
    A real user searching by hand never sees them, and there is frequently
    no viewable profile behind the id at all, so they arrived with no name
    and no photo and rendered as a bare numeric id.

    Measured on live data, every single blank card came from this source
    (161 of 263) while not one genuinely-rendered result was ever blank.
    This tool's contract is fidelity to what a real user actually sees, so
    these must never become rows -- only a COUNT on the sweep."""
    d = Discovery(args=_sweep_args(), ctx=_SweepCtx())

    sweep = asyncio.run(d.sweep("adani", "people"))

    ids = {h.entity_id for h in sweep.hits}
    assert NEVER_SHOWN_ID not in ids, (
        "an id Facebook deliberately never displayed must never become a result row"
    )
    assert not any(h.source == "processed-not-shown" for h in sweep.hits)
    assert sweep.unshown == 1, "the gap must still be COUNTED for observability"


def test_sweep_still_backfills_ids_facebook_did_render():
    """The other half of the same fix -- dropping never-displayed ids must
    NOT weaken the real safety net. An id Facebook genuinely rendered
    (`result_ids_shown`) that we failed to parse as an edge is real data
    loss on our side, so it is still recovered and still reported."""
    d = Discovery(args=_sweep_args(), ctx=_SweepCtx())

    sweep = asyncio.run(d.sweep("adani", "people"))

    ids = {h.entity_id for h in sweep.hits}
    assert ids == {RENDERED_EDGE_ID, RENDERED_UNPARSED_ID}
    assert sweep.backfilled == 1
    # the genuinely-rendered edge keeps its real name/photo
    edge = next(h for h in sweep.hits if h.entity_id == RENDERED_EDGE_ID)
    assert edge.name == "Real Person"
    assert edge.source == "graphql"
    # ...and a rendered-but-unparsed id is ordered AFTER every confirmed one
    assert sweep.hits[0].entity_id == RENDERED_EDGE_ID


def test_photo_asset_id_ignores_rotating_signed_query_params():
    a = "https://scontent.fblr8-1.fna.fbcdn.net/v/t1.30497-1/123_456_789_n.png?oh=AAAA&_nc_ohc=xxx"
    b = "https://scontent.fblr8-2.fna.fbcdn.net/v/t1.30497-1/123_456_789_n.png?oh=BBBB&_nc_ohc=yyy"
    assert _photo_asset_id(a) == _photo_asset_id(b) == "123_456_789_n.png"


def test_photo_asset_id_blank_for_a_url_with_no_recognizable_asset_pattern():
    assert _photo_asset_id("https://scontent.fbcdn.net/some/other/shape.jpg") == ""
    assert _photo_asset_id("") == ""


class _RealisticLeakingProfilePage:
    """Reproduces the actual live incident end-to-end: a fully identity-
    confirmed profile page (canonical matches eid exactly) whose embedded
    JSON has a perfectly clean, plausible, id-scoped `profile_picture` --
    no RenderedProfile markers, nothing structurally suspicious about it
    at all -- but which is, in fact, the scraper's own substituted photo
    (Facebook's actual behavior for a privacy-restricted profile: it
    substitutes the viewer's own photo into EVERY picture field it
    renders, JSON and DOM alike, indistinguishably from a genuine one).
    This is exactly the shape that defeated three earlier, narrower
    defenses (id-scoping, on_target_profile, RenderedProfile markers) in
    live testing before avatar recovery was disabled outright."""

    def __init__(self, eid: str):
        self._eid = eid

    def on(self, _e, _h):
        pass

    async def goto(self, _url, wait_until=None, timeout=None):
        pass

    async def wait_for_function(self, _js, timeout=None):
        pass

    async def inner_text(self, _sel):
        return ""

    async def evaluate(self, js):
        if "canonical" in js:
            return f"https://www.facebook.com/profile.php?id={self._eid}"
        if "svg image" in js:
            return "https://scontent.fbcdn.net/v/t1.30497-1/leaked_n.jpg"
        return [json.dumps({
            "id": self._eid, "name": "Real Target Name",
            "profile_picture": {"uri": "https://scontent.fbcdn.net/v/t1.30497-1/leaked_n.jpg"},
        })]

    async def title(self):
        return "Real Target Name | Facebook"

    async def close(self):
        pass


def test_resolve_missing_never_leaks_an_avatar_even_from_a_fully_confirmed_clean_looking_page():
    """THE end-to-end regression test for today's final fix. Every earlier,
    narrower defense (id-scoping, identity confirmation, structural
    markers) was individually confirmed live to still let this exact
    shape through. There is currently no way to distinguish it from a
    genuinely safe photo from the payload alone, so avatar recovery from a
    profile-page resolve visit is off, full stop -- this must hold even
    for a page that looks completely legitimate by every signal this
    module has ever tried. Name recovery is unaffected."""
    eid = "999000111"
    d = Discovery(args=SimpleNamespace(timeout=5), ctx=_SinglePageCtx(_RealisticLeakingProfilePage(eid)))

    hit = asyncio.run(d._resolve_missing([eid], "profile"))[eid]

    assert hit.name == "Real Target Name"
    assert hit.avatar == "", f"avatar recovery from a profile-page visit must be off entirely, got {hit.avatar!r}"
    assert hit.has_custom_pic is False


def test_first_matching_dict_wins_when_several_mention_the_same_id():
    """Multiple payloads on the page can independently mention the same
    entity (e.g. embedded state + an XHR echo) -- the first non-blank
    name/photo found is kept, later ones don't overwrite it with something
    worse."""
    blobs = [
        {"id": "12345", "name": "Jane Doe", "profile_picture": {"uri": "https://scontent.fbcdn.net/pic.jpg"}},
        {"id": "12345", "name": "Stale Cached Name"},
    ]
    name, avatar, _ = _extract_entity(blobs, "12345", trust_page_context_avatar=True)
    assert name == "Jane Doe"
    assert avatar == "https://scontent.fbcdn.net/pic.jpg"
