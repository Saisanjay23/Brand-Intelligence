"""The standalone engine (backend/engine/): credential resolution, request
and result shapes, output sinks, and the two invariants that make the
package worth having.

Those invariants are the reason most of this file exists:

  1. IMPORT PURITY -- `import backend.engine` must not pull in Motor,
     FastAPI or `backend.database`. This fails silently: on any developer
     machine all three are installed, so an accidental top-level import
     works perfectly here and only breaks on the deployment that has no
     database. Checked in a SUBPROCESS, because any other test in this
     session may already have imported those modules into `sys.modules`.

  2. FIELD-MAPPING PARITY -- `runner._hit_to_fields` / `_row_to_fields`
     restate the mapping that lives inside the Mongo-bound service modules
     (which cannot be imported without Motor). Restated code drifts, so
     the copies are pinned against the originals here.

No network, no browser, no database -- everything below is either a pure
function or reads a tmp_path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.engine.credentials import CredentialStore
from backend.engine.models import (
    PLATFORM_TABS,
    AnalysisRequest,
    DiscoveryRequest,
    EngineResult,
    PlatformOutcome,
    platform_for_url,
)
from backend.engine.runner import _hit_to_fields, _row_to_fields, _tri, _username_from
from backend.shared.models.row import Row

# Invariant 1: the package stays importable without a database


def test_importing_the_engine_does_not_pull_in_mongo_or_fastapi():
    """The whole point of the package. A subprocess, so this measures the
    engine's own import graph rather than whatever this pytest session has
    already loaded."""
    probe = (
        "import sys; import backend.engine; "
        "banned=('backend.database','motor','pymongo','fastapi'); "
        "print(sorted(m for m in sys.modules if m.startswith(banned)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2],
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", f"engine leaked database/web imports: {out.stdout}"


def test_cli_help_works_without_touching_mongo():
    """`--help` must work on a machine with no database configured at all;
    it is the first thing anyone runs."""
    out = subprocess.run(
        [sys.executable, "-m", "backend.engine", "--help"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2],
    )
    assert out.returncode == 0, out.stderr
    for command in ("discover", "analyze", "platforms"):
        assert command in out.stdout


# Invariant 2: the restated field mappings match their originals


def test_platform_tabs_match_the_service_copy():
    from backend.services.discovery_service import PLATFORM_TABS as SERVICE_TABS

    assert PLATFORM_TABS == SERVICE_TABS


def test_hit_fields_match_the_service_mapping():
    """Same keys, same values, for the keys the Mongo path actually writes.

    The engine's version adds `platform`/`tab`/`rank` (a standalone caller
    has no surrounding document to carry those) and scores the name against
    an explicit target, so those are compared separately below.
    """
    from backend.services.discovery_service import _hit_to_fields as service_mapping

    hit = _FakeHit()
    mine = _hit_to_fields(hit, "twitter", target="")
    theirs = service_mapping(hit, "twitter")

    assert set(theirs) <= set(mine), f"engine mapping dropped keys: {set(theirs) - set(mine)}"
    for key, value in theirs.items():
        assert mine[key] == value, f"{key}: engine {mine[key]!r} != service {value!r}"


def test_row_fields_match_the_service_mapping():
    from backend.services.analysis_service import _row_to_fields as service_mapping

    row = Row(url="https://x.com/someone", target="Acme", original_feed="https://x.com/acme")
    row.profile_name, row.profile_id, row.followers, row.location = "Acme Support", "42", 1234, "Pune"
    row.has_custom_pic, row.name_score, row.last_post_iso, row.status = True, 100, "2026-08-01", "OK"
    row.mark("followers", "graphql")

    mine = _row_to_fields(row, "twitter", evidence_root=None)
    theirs = service_mapping(row)

    for key, value in theirs.items():
        assert mine[key] == value, f"{key}: engine {mine[key]!r} != service {value!r}"
    # the derived score must survive the mapping, not be recomputed differently
    assert mine["risk_score"] == row.risk
    assert mine["priority"] == row.priority


class _FakeHit:
    """The universal discovery-result shape (platforms/facebook/discovery_engine.py::Hit)."""

    entity_id = "994922996350337024"
    name = "Acme Corp"
    url = "https://x.com/acme"
    avatar = "https://pbs.twimg.com/profile_images/1/a.jpg"
    has_custom_pic = True
    verified = False
    entity_type = "profile"
    keyword = "Acme"
    tab = "people"
    rank = 3
    source = "graphql"


# Url -> platform


@pytest.mark.parametrize("url,expected", [
    ("https://x.com/acme", "twitter"),
    ("https://twitter.com/acme", "twitter"),
    ("https://mobile.twitter.com/acme", "twitter"),
    ("https://www.facebook.com/acme", "facebook"),
    ("https://m.facebook.com/acme", "facebook"),
    ("https://www.instagram.com/acme/", "instagram"),
    ("https://youtube.com/@acme", "youtube"),
    ("https://youtu.be/abc123", "youtube"),
    ("https://t.me/acme", "telegram"),
    ("https://www.tiktok.com/@acme", "tiktok"),
])
def test_platform_inferred_from_url(url, expected):
    assert platform_for_url(url) == expected


def test_unknown_host_infers_nothing_rather_than_guessing():
    """A wrong guess sends a profile to the wrong scraper; "" makes the
    caller pass --platform explicitly."""
    assert platform_for_url("https://example.com/acme") == ""
    assert platform_for_url("not a url") == ""


def test_lookalike_host_is_not_mistaken_for_the_real_one():
    """`notfacebook.com` must not match `facebook.com` -- suffix matching
    has to be on a dot boundary, which is the whole reason this engine
    exists to catch impersonation in the first place."""
    assert platform_for_url("https://notfacebook.com/acme") == ""
    assert platform_for_url("https://facebook.com.evil.net/acme") == ""


# Username derivation


def test_username_from_url():
    assert _username_from("https://x.com/acme", "twitter") == "acme"
    assert _username_from("https://www.tiktok.com/@acme", "tiktok") == "acme"


def test_bare_host_yields_no_username():
    """The bug the service mapping documents: a naive last-path-segment
    split returns the HOSTNAME, and the handle comparison then scores the
    platform's own domain against the brand."""
    assert _username_from("https://twitter.com/", "twitter") == ""
    assert _username_from("https://twitter.com", "twitter") == ""


# Tri-state flags


def test_unknown_is_not_false():
    """"" means the scraper could not determine the field, which is not the
    same as determining it false -- collapsing them publishes "inactive"
    about profiles whose last-post date was never visible."""
    assert _tri("Yes") is True
    assert _tri("No") is False
    assert _tri("") is None


# Requests


def test_discovery_request_round_trips_through_json():
    request = DiscoveryRequest(keywords=["Acme"], platforms=["twitter"], max_results=5)
    assert DiscoveryRequest.from_dict(json.loads(json.dumps(request.to_dict()))) == request


def test_discovery_request_drops_blank_keywords():
    assert DiscoveryRequest.from_dict({"keywords": ["Acme", "  ", ""]}).keywords == ["Acme"]


def test_target_defaults_to_first_keyword():
    assert DiscoveryRequest(keywords=["Acme", "Acme Corp"]).resolved_target() == "Acme"
    assert DiscoveryRequest(keywords=["Acme"], target="Acme Inc").resolved_target() == "Acme Inc"


def test_tabs_fall_back_to_the_platform_vocabulary():
    """Each platform's discovery engine only understands its own fixed tab
    names, so a caller-supplied tab list must never be applied blindly to
    every platform."""
    request = DiscoveryRequest(keywords=["Acme"])
    assert request.tabs_for("facebook") == ["people", "pages", "groups"]
    assert request.tabs_for("youtube") == ["channels"]
    assert DiscoveryRequest(keywords=["A"], tabs={"facebook": ["pages"]}).tabs_for("facebook") == ["pages"]


def test_analysis_request_round_trips_through_json():
    request = AnalysisRequest(urls=["https://x.com/acme"], target="Acme")
    assert AnalysisRequest.from_dict(json.loads(json.dumps(request.to_dict()))) == request


# Results


def test_a_run_is_ok_when_any_platform_produced_results():
    """One platform failing is not the run failing -- the results that did
    come back must not be thrown away over it."""
    result = EngineResult(kind="discovery", platforms=[
        PlatformOutcome("twitter", "done", found=4),
        PlatformOutcome("facebook", "failed", reason="session expired"),
    ])
    assert result.ok is True


def test_a_run_is_not_ok_when_nothing_ran():
    result = EngineResult(kind="discovery", platforms=[
        PlatformOutcome("twitter", "skipped", reason="missing"),
        PlatformOutcome("facebook", "failed", reason="session expired"),
    ])
    assert result.ok is False
    assert EngineResult(kind="discovery").ok is False


def test_partial_counts_as_ok():
    result = EngineResult(kind="discovery", platforms=[PlatformOutcome("twitter", "partial", found=2)])
    assert result.ok is True


# Credentials


COOKIES = [
    {"name": "auth_token", "value": "x", "domain": ".twitter.com"},
    {"name": "ct0", "value": "y", "domain": ".twitter.com"},
]


def test_missing_when_nothing_is_configured(tmp_path):
    store = CredentialStore(tmp_path)
    assert store.state_for("twitter") == "missing"
    assert "no cookie file" in store.why_not("twitter")


def test_a_plain_cookie_export_is_ready(tmp_path):
    (tmp_path / "twitter.json").write_text(json.dumps(COOKIES), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.state_for("twitter") == "ready"
    assert store.why_not("twitter") == ""


def test_an_export_missing_a_required_cookie_is_incomplete(tmp_path):
    (tmp_path / "twitter.json").write_text(json.dumps(COOKIES[:1]), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.state_for("twitter") == "incomplete"
    assert "ct0" in store.why_not("twitter")


def test_two_half_broken_exports_do_not_add_up_to_one_session(tmp_path):
    """The correctness point sessions/manager.py documents for the Mongo
    pool: required cookies are checked PER credential, never unioned across
    the pool, or two accounts that each cannot log in jointly report ready."""
    (tmp_path / "twitter-a.json").write_text(json.dumps(COOKIES[:1]), encoding="utf-8")
    (tmp_path / "twitter-b.json").write_text(json.dumps(COOKIES[1:]), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert len(store.sessions_for("twitter")) == 2
    assert store.state_for("twitter") == "incomplete"


def test_a_v2_pool_file_yields_one_credential_per_live_account(tmp_path):
    """This repo's own session/*.json shape -- several accounts per file."""
    (tmp_path / "twitter.json").write_text(json.dumps({
        "version": 2,
        "sessions": [
            {"id": "1", "identifier": "main@example.com", "status": "ready", "cookies": COOKIES},
            {"id": "2", "identifier": "spare@example.com", "status": "ready", "cookies": COOKIES},
        ],
    }), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.state_for("twitter") == "ready"
    assert [s["identifier"] for s in store.sessions_for("twitter")] == ["main@example.com", "spare@example.com"]


def test_dead_pool_entries_are_skipped(tmp_path):
    """The file already records the verdict; re-trying a known-dead account
    just to rediscover it wastes a browser launch."""
    (tmp_path / "twitter.json").write_text(json.dumps({
        "version": 2,
        "sessions": [
            {"id": "1", "identifier": "burned", "status": "checkpointed", "cookies": COOKIES},
            {"id": "2", "identifier": "expired-one", "status": "expired", "cookies": COOKIES},
            {"id": "3", "identifier": "good", "status": "ready", "cookies": COOKIES},
        ],
    }), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert [s["identifier"] for s in store.sessions_for("twitter")] == ["good"]


def test_a_pool_file_of_only_dead_accounts_explains_itself(tmp_path):
    """"re-export while logged in" is useless advice when the real problem
    is that every account in the file is already marked dead."""
    (tmp_path / "twitter.json").write_text(json.dumps({
        "version": 2,
        "sessions": [{"id": "1", "identifier": "burned", "status": "expired", "cookies": COOKIES}],
    }), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.state_for("twitter") == "missing"
    assert "expired/checkpointed" in store.why_not("twitter")


def test_cookies_from_another_platform_are_not_borrowed(tmp_path):
    """Domain filtering is what keeps one platform's login out of another's
    browser context."""
    (tmp_path / "twitter.json").write_text(json.dumps([
        {"name": "auth_token", "value": "x", "domain": ".facebook.com"},
        {"name": "ct0", "value": "y", "domain": ".facebook.com"},
    ]), encoding="utf-8")
    assert CredentialStore(tmp_path).state_for("twitter") == "incomplete"


def test_a_burned_credential_leaves_rotation(tmp_path):
    (tmp_path / "twitter.json").write_text(json.dumps({
        "version": 2,
        "sessions": [
            {"id": "1", "identifier": "first", "status": "ready", "cookies": COOKIES},
            {"id": "2", "identifier": "second", "status": "ready", "cookies": COOKIES},
        ],
    }), encoding="utf-8")
    store = CredentialStore(tmp_path)
    first = store.sessions_for("twitter")[0]
    store.burn(first["id"], "expired mid-run")
    assert [s["identifier"] for s in store.sessions_for("twitter")] == ["second"]


def test_an_api_key_file_is_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    (tmp_path / "youtube.key").write_text("test-key-value", encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.state_for("youtube") == "ready"
    # the adapters read this from the environment, so a key from a FILE only
    # works because the store puts it there
    import os
    assert os.environ["YOUTUBE_API_KEY"] == "test-key-value"


def test_an_api_key_from_the_environment_is_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "from-env")
    assert CredentialStore(tmp_path).state_for("youtube") == "ready"


def test_unreadable_files_are_skipped_rather_than_fatal(tmp_path):
    """One bad file must never take down a run that has other usable
    credentials."""
    (tmp_path / "twitter-broken.json").write_text("{not json at all", encoding="utf-8")
    (tmp_path / "twitter-good.json").write_text(json.dumps(COOKIES), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.state_for("twitter") == "ready"


def test_a_proxy_file_is_applied_to_its_platform(tmp_path):
    (tmp_path / "twitter.json").write_text(json.dumps(COOKIES), encoding="utf-8")
    (tmp_path / "proxies.json").write_text(json.dumps({
        "twitter": {"server": "http://proxy.example:8080"},
        "_default": {"server": "http://fallback.example:8080"},
    }), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.sessions_for("twitter")[0]["proxy"]["server"] == "http://proxy.example:8080"


def test_the_default_proxy_covers_platforms_without_their_own(tmp_path):
    (tmp_path / "facebook.json").write_text(json.dumps([
        {"name": "c_user", "value": "1", "domain": ".facebook.com"},
        {"name": "xs", "value": "2", "domain": ".facebook.com"},
    ]), encoding="utf-8")
    (tmp_path / "proxies.json").write_text(
        json.dumps({"_default": {"server": "http://fallback.example:8080"}}), encoding="utf-8")
    store = CredentialStore(tmp_path)
    assert store.sessions_for("facebook")[0]["proxy"]["server"] == "http://fallback.example:8080"


def test_report_covers_every_enabled_platform(tmp_path, monkeypatch):
    # `_api_key_sessions` deliberately exports the key it finds into the
    # process environment (that is how a key from a FILE reaches the
    # adapters), so a key set by an earlier test would otherwise make
    # YouTube read as ready here.
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    report = CredentialStore(tmp_path).report()
    from backend.platforms import registry

    assert {p["platform"] for p in report} == {p for p, x in registry.PLATFORMS.items() if x.enabled}
    unready = [p for p in report if p["state"] != "ready"]
    assert unready, "an empty tmp_path should leave every platform unready"
    assert all(p["fix"] for p in unready), "an unready platform must say what to fix"
    assert all(not p["fix"] for p in report if p["state"] == "ready")


# Sinks


def _result() -> EngineResult:
    return EngineResult(
        kind="discovery",
        profiles=[
            {"platform": "twitter", "display_name": "Acme", "url": "https://x.com/acme",
             "name_score": 100, "sources": {"followers": "graphql"}},
            {"platform": "facebook", "display_name": "Acme Inc", "url": "https://fb.com/acme",
             "name_score": 90, "sources": {}},
        ],
        platforms=[PlatformOutcome("twitter", "done", found=1)],
    )


def test_json_sink_keeps_the_per_platform_outcomes(tmp_path):
    """A file of N profiles means something different when a platform was
    skipped for a dead cookie -- only this shape carries that."""
    from backend.engine import sinks

    out = sinks.write(_result(), tmp_path / "out.json")
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["found"] == 2
    assert written["ok"] is True
    assert written["platforms"][0]["platform"] == "twitter"


def test_jsonl_sink_writes_one_profile_per_line(tmp_path):
    from backend.engine import sinks

    out = sinks.write(_result(), tmp_path / "out.jsonl")
    lines = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert [p["display_name"] for p in lines] == ["Acme", "Acme Inc"]


def test_csv_sink_is_readable_and_has_no_blank_rows(tmp_path):
    """`newline=""` is required, not stylistic: without it every row is
    separated by a blank one on Windows."""
    import csv as csv_module

    from backend.engine import sinks

    out = sinks.write(_result(), tmp_path / "out.csv")
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv_module.DictReader(fh))
    assert len(rows) == 2
    assert rows[0]["display_name"] == "Acme"
    # dicts have no useful CSV rendering; they must survive as JSON
    assert json.loads(rows[0]["sources"]) == {"followers": "graphql"}


def test_csv_columns_are_ordered_for_a_human_but_drop_nothing(tmp_path):
    from backend.engine import sinks

    out = sinks.write(_result(), tmp_path / "out.csv")
    header = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert header[:3] == ["platform", "display_name", "url"]
    assert "sources" in header


def test_format_is_inferred_from_the_extension(tmp_path):
    from backend.engine import sinks

    assert sinks.write(_result(), tmp_path / "a.jsonl").read_text(encoding="utf-8").count("\n") == 2
    with pytest.raises(ValueError, match="unknown output format"):
        sinks.write(_result(), tmp_path / "a.xml")
