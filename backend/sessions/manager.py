"""Session pool lifecycle: paste, launch-and-log-in, rotate, quarantine,
and the periodic background health sweep that notices a session has gone
bad before a job finds out the hard way.

ROTATION, not failover: `get_healthy_session` always hands back the
least-recently-used available session, not the first ready one -- handing
back #1 every time would drive every request through it until the
platform bans it, then #2, and so on. Spreading load evenly across the
pool is the entire reason a pool exists.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from backend.config.settings import settings
from backend.database.repositories import session_repository as sessions_db
from backend.sessions.cookies import load_cookies
from backend.shared.errors import ConflictError, NotFoundError, ValidationError
from backend.shared.logging import get_logger

log = get_logger("sessions.manager")

DEAD_STATES = {"expired", "checkpointed", "unreadable"}

# where a manual login starts, and the cookie that proves it worked
LOGIN_FLOW = {
    "facebook": ("https://www.facebook.com/login", "c_user"),
    "twitter": ("https://x.com/login", "auth_token"),
    "instagram": ("https://www.instagram.com/accounts/login/", "sessionid"),
}

CHECK_INTERVAL_S = 30 * 60  # generous on purpose -- this opens a real browser
BATCH_SIZE = 5  # sessions live-checked per platform per monitor sweep
MONITORED = ("facebook", "instagram", "twitter", "telegram")

_logins: dict[str, dict] = {}  # platform -> LoginRun-shaped dict
_monitor_task: Optional[asyncio.Task] = None


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _is_available(item: dict, now: float) -> bool:
    if item["status"] in DEAD_STATES:
        return False
    return item["rate_limited_until"] <= now


def pool_summary_of(items: list[dict], now: float) -> dict:
    return {
        "total": len(items),
        "available": sum(1 for s in items if _is_available(s, now)),
        "dead": sum(1 for s in items if s["status"] in DEAD_STATES),
    }


def _get_platform(platform_id: str):
    from backend.platforms import registry

    try:
        return registry.get(platform_id)
    except KeyError:
        raise NotFoundError(f"unknown platform {platform_id!r}") from None


def _session_in_use(platform_id: str, session_id: str) -> bool:
    """Is a currently-RUNNING job actually holding this exact session right
    now -- not "was picked at some point", the live answer. `Job` carries
    `session_id`/`session_platform`, stamped by discovery_service.py /
    analysis_service.py at the moment session_for_job() hands one out, and
    naturally clears itself: once the job's status leaves "running" (done,
    failed, cancelled -- including via round_robin_service's own crash
    recovery, see its module), this stops matching with no explicit
    "release" call required anywhere."""
    from backend.services.job_service import job_manager

    return any(
        j.status == "running" and j.session_platform == platform_id and j.session_id == session_id
        for j in job_manager.jobs.values()
    )


def _public(s: dict) -> dict:
    """One pool entry as an API caller may see it -- never the cookie
    values. A session cookie IS the credential."""
    proxy = s.get("proxy") or {}
    proxy_host = ""
    if proxy.get("server"):
        parsed = urlparse(proxy["server"])
        proxy_host = parsed.hostname or proxy["server"].split("://")[-1].split("@")[-1].split(":")[0]
    return {
        "id": s["id"], "identifier": s["identifier"], "status": s["status"],
        "rate_limited_until": s["rate_limited_until"], "last_used": s["last_used"],
        "use_count": s.get("use_count", 0),
        "in_use": _session_in_use(s.get("platform", ""), s["id"]),
        "cookie_count": len(s.get("cookies", []) or []), "proxy_host": proxy_host,
        "is_api_key": bool(s.get("api_key")),
        # so the Sessions panel can show "cooling off, 3rd consecutive
        # failure" instead of a bare red dot with no sense of whether this
        # is a blip or a burned account
        "consecutive_failures": s.get("consecutive_failures", 0),
        "available": _is_available(s, _now()),
    }


# ---------- state ----------

async def state_for(platform_id: str) -> str:
    """ready | missing | incomplete -- called by platforms.registry."""
    import os
    p = _get_platform(platform_id)
    if p.uses_api_key:
        items = await sessions_db.list_pool(platform_id)
        if items and any(s.get("api_key") for s in items):
            for it in items:
                if it.get("api_key") and _is_available(it, _now()):
                    os.environ[p.api_key_env] = str(it["api_key"])
                    return "ready"
            if os.environ.get(p.api_key_env):
                return "ready"
        elif os.environ.get(p.api_key_env):
            return "ready"
        return "missing"
    if p.env_keys:
        from backend.config.settings import settings
        session_path = settings.session_blob_path / "telegram.session"
        if all(os.environ.get(k) for k in p.env_keys) and session_path.exists():
            return "ready"
        items = await sessions_db.list_pool(platform_id)
        if items and items[0].get("api_id") and items[0].get("api_hash"):
            item = items[0]
            if not all(os.environ.get(k) for k in p.env_keys):
                os.environ["TELEGRAM_API_ID"] = str(item.get("api_id", ""))
                os.environ["TELEGRAM_API_HASH"] = str(item.get("api_hash", ""))
            if item.get("phone"):
                os.environ["TELEGRAM_PHONE"] = str(item.get("phone", ""))
            if item.get("session_blob") and not session_path.exists():
                settings.session_blob_path.mkdir(parents=True, exist_ok=True)
                session_path.write_bytes(item["session_blob"])
            if all(os.environ.get(k) for k in p.env_keys) and session_path.exists():
                return "ready"
        return "incomplete"
    items = await sessions_db.list_pool(platform_id)
    if not items:
        return "missing"

    # "ready" has to mean "a job started right now could actually run", and
    # that requires ONE session that is both complete and currently usable.
    #
    # This previously unioned cookie NAMES across the whole pool and never
    # looked at status or rate_limited_until at all, so:
    #   - twenty quarantined/checkpointed sessions still reported "ready",
    #   - two half-broken sessions could jointly satisfy required_cookies
    #     when neither one could log in on its own.
    # Everything downstream trusts this: discovery decides which platforms
    # to sweep, the scheduler's catch-up decides whether to queue analysis
    # (and re-queued a doomed job every 20 minutes on a dead pool), and
    # both health surfaces render it as a green light.
    required = set(p.required_cookies)
    now = _now()
    complete_and_available = False
    complete_but_unavailable = False
    for item in items:
        names = {c["name"] for c in (item.get("cookies") or []) if c.get("name")}
        if not required <= names:
            continue
        if _is_available(item, now):
            complete_and_available = True
            break
        complete_but_unavailable = True

    if complete_and_available:
        return "ready"
    if complete_but_unavailable:
        # a real, fully-formed session exists -- it is just quarantined or
        # cooling off. Distinct from "incomplete" (a botched cookie export),
        # because the fix is different: wait, or add another account.
        return "exhausted"
    return "incomplete"


async def status(platform_id: str, live_health: Optional[dict] = None) -> dict:
    from backend.platforms import registry

    p = _get_platform(platform_id)
    items = await sessions_db.list_pool(platform_id)
    now = _now()

    out: dict = {
        "platform": p.id, "name": p.name,
        "state": await registry.session_state(p),
        "kind": "api-key" if p.uses_api_key else "mtproto" if p.env_keys else "cookies",
        "can_login": p.id in LOGIN_FLOW,
        "cookie_count": sum(len(s.get("cookies", []) or []) for s in items),
        "sessions": [_public(s) for s in items],
        "pool_total": len(items),
        "pool_ready": sum(1 for s in items if _is_available(s, now)),
        "expires": "", "message": "", "last_verified": "",
    }

    if out["kind"] == "cookies" and items:
        soonest = None
        for s in items:
            for c in s["cookies"]:
                if c.get("name") in p.required_cookies and isinstance(c.get("expires"), int) and c["expires"] > 0:
                    soonest = c["expires"] if soonest is None else max(soonest, c["expires"])
        if soonest is not None:
            out["expires"] = datetime.fromtimestamp(soonest, timezone.utc).date().isoformat()
            if soonest <= now:
                out["state"] = "expired"

    health = (live_health or {}).get(p.id)
    if health is not None:
        checked_at = health.get("checked_at")
        out["last_verified"] = checked_at.isoformat() if checked_at else ""
        if out["state"] == "ready" and not health.get("ok", True):
            out["state"] = "checkpointed"
            out["message"] = health.get("detail", "")

    if run := _logins.get(p.id):
        out["login"] = run
    return out


def _pick_least_recently_used(available: list[dict]) -> Optional[dict]:
    if not available:
        return None
    return sorted(available, key=lambda s: s["last_used"])[0]


async def get_healthy_session(platform_id: str) -> Optional[dict]:
    items = await sessions_db.list_pool(platform_id)
    available = [s for s in items if _is_available(s, _now())]
    chosen = _pick_least_recently_used(available)
    if chosen is None:
        return None
    now = _now()
    await sessions_db.update_item(platform_id, chosen["id"], status="ready", rate_limited_until=0.0, last_used=now)
    # a real, durable count of how many times this session has actually
    # been handed to a job -- not a health check, an atomic $inc so two
    # round-robin slots picking sessions concurrently can't drop one
    # another's count
    use_count = await sessions_db.increment_use_count(platform_id, chosen["id"])
    chosen["status"], chosen["rate_limited_until"], chosen["last_used"], chosen["use_count"] = (
        "ready", 0.0, now, use_count,
    )
    return chosen


async def session_for_job(platform_id: str) -> tuple[object, dict]:
    """What a discovery/analysis job needs to actually run: the Platform
    metadata plus either `{}` (MTProto-authed) or a healthy pooled
    session's credentials+proxy."""
    from backend.platforms import registry

    plat = _get_platform(platform_id)
    if plat.env_keys:
        state = await registry.session_state(plat)
        if state != "ready":
            raise ConflictError(f"{platform_id} credentials {state}")
        return plat, {}
    if plat.uses_api_key:
        item = await get_healthy_session(platform_id)
        if item is None:
            raise ConflictError(f"{platform_id}: no healthy API keys available -- please add more keys or check quotas")
        import os
        os.environ[plat.api_key_env] = str(item.get("api_key", ""))
        return plat, {"id": item["id"], "identifier": item["identifier"], "api_key": item["api_key"]}
    item = await get_healthy_session(platform_id)
    if item is None:
        raise ConflictError(f"{platform_id}: no healthy sessions available -- please add more cookies")
    return plat, {"id": item["id"], "identifier": item["identifier"], "cookies": item["cookies"], "proxy": item["proxy"]}


async def mark_session_failed(
    platform_id: str, session_id: str, reason: str = "expired",
    rate_limited_until: float = 0,
) -> None:
    """Quarantine one session, backing off further each consecutive time.

    No-ops for key/MTProto-authed platforms, which have no pool at all
    (session_id is empty there).

    The cooldown is GRADUATED (settings.session_backoff_minutes, default
    15m -> 1h -> 6h -> 24h) and keyed on this session's own consecutive
    failure count, which `get_healthy_session` resets to zero as soon as
    the session is handed out and used successfully. A single rate-limit
    used to cost a flat 24 hours, so one bad afternoon quarantined an
    entire pool at once and left every platform dark until the next day --
    while `state_for` cheerfully kept reporting "ready" and the scheduler
    kept queueing jobs into the void.

    An explicit `rate_limited_until` still wins, for a platform that tells
    us exactly how long to wait (Telegram's FloodWait).
    """
    if not session_id:
        return
    item = await sessions_db.get_item(platform_id, session_id)
    fails = int((item or {}).get("consecutive_failures") or 0) + 1
    fields: dict = {"status": reason, "consecutive_failures": fails}

    if rate_limited_until:
        fields["rate_limited_until"] = float(rate_limited_until)
    else:
        ladder = settings.session_backoff_minutes or [15, 60, 360, 1440]
        minutes = ladder[min(fails, len(ladder)) - 1]
        fields["rate_limited_until"] = _now() + minutes * 60
        fields["quarantine_minutes"] = minutes

    if await sessions_db.update_item(platform_id, session_id, **fields):
        mins = (fields["rate_limited_until"] - _now()) / 60
        log.warning(
            f"{platform_id} session {session_id} marked {reason} "
            f"(failure #{fails}, cooling off ~{mins:.0f}m)"
        )


async def mark_session_ok(platform_id: str, session_id: str) -> None:
    """Clear a session's quarantine after it demonstrably worked.

    The backoff ladder in `mark_session_failed` only escalates while
    failures are CONSECUTIVE -- without this reset the counter would ratchet
    up over a session's whole lifetime and a healthy account would
    eventually sit on a 24h cooldown after four unrelated blips months
    apart. Called on a real read (analysis_service) and on a passing live
    health check (_record_item_result).
    """
    if not session_id:
        return
    await sessions_db.update_item(
        platform_id, session_id,
        status="ready", rate_limited_until=0.0, consecutive_failures=0,
    )


async def pool_summary(platform_id: str) -> dict:
    _get_platform(platform_id)
    return pool_summary_of(await sessions_db.list_pool(platform_id), _now())


# ---------- write ----------

async def save_cookies(platform_id: str, blob: str, identifier: str = "") -> dict:
    p = _get_platform(platform_id)
    if p.uses_api_key or p.env_keys:
        raise ConflictError(f"{platform_id}: uses credentials in .env, not cookies")
    cookies = load_cookies(blob, p.cookie_domain)
    if not cookies:
        raise ValidationError("no cookies for this platform in that export")
    missing = [n for n in p.required_cookies if n not in {c["name"] for c in cookies}]
    if missing:
        raise ValidationError(f"missing {', '.join(missing)} -- export while logged in")
    try:
        await sessions_db.add_item(platform_id, cookies, identifier)
    except ValueError as e:
        raise ConflictError(str(e))
    return await status(platform_id)


async def save_api_key(platform_id: str, key: str, identifier: str = "") -> dict:
    from backend.config.settings import write_env

    p = _get_platform(platform_id)
    if not p.uses_api_key:
        raise ConflictError(f"{platform_id}: does not use an API key")
    key = key.strip()
    if not key:
        raise ValidationError("empty key")
    write_env(p.api_key_env, key)
    try:
        await sessions_db.save_api_key_session(platform_id, key, identifier=identifier or "YouTube API Key")
    except ValueError as e:
        raise ConflictError(str(e))
    log.info(f"{platform_id}: API key saved ({identifier or 'YouTube API Key'})")
    return await status(platform_id)


async def update_session_credentials(platform_id: str, session_id: str, blob: str = "", api_key: str = "", identifier: Optional[str] = None) -> dict:
    p = _get_platform(platform_id)
    fields: dict = {}
    if identifier is not None and identifier.strip():
        fields["identifier"] = identifier.strip()
    if p.uses_api_key:
        if not api_key or not api_key.strip():
            raise ValidationError("empty API key")
        fields["api_key"] = api_key.strip()
        from backend.config.settings import write_env
        write_env(p.api_key_env, fields["api_key"])
    elif not p.env_keys:
        if not blob:
            raise ValidationError("empty cookie JSON")
        cookies = load_cookies(blob, p.cookie_domain)
        if not cookies:
            raise ValidationError("no cookies for this platform in that export")
        missing = [n for n in p.required_cookies if n not in {c["name"] for c in cookies}]
        if missing:
            raise ValidationError(f"missing {', '.join(missing)} -- export while logged in")
        fields["cookies"] = cookies
    res = await sessions_db.update_session_credentials(platform_id, session_id, **fields)
    if not res:
        raise NotFoundError(f"session {session_id!r} not found in {platform_id} pool")
    log.info(f"{platform_id}: updated session credentials for {session_id}")
    return await status(platform_id)


# Playwright's own accepted proxy schemes. See stealth/proxy.py::
# build_proxy_config, which passes `server` straight through to Playwright's
# context-launch option unvalidated -- this is the one place that stands
# between a malformed string and a browser launch failing three steps into
# a job, instead of at the moment the proxy is actually configured.
_ALLOWED_PROXY_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")


def _validate_proxy(proxy: dict) -> dict:
    """Rejects anything Playwright would reject (or silently misuse), and
    strips the result down to exactly the keys `build_proxy_config` reads --
    so a caller-supplied dict can never smuggle unrelated fields into the
    stored session document."""
    server = str(proxy.get("server") or "").strip()
    if not server:
        raise ValidationError("proxy.server is required")
    parsed = urlparse(server)
    if parsed.scheme not in _ALLOWED_PROXY_SCHEMES:
        raise ValidationError(
            f"proxy.server must start with one of {'/'.join(s + '://' for s in _ALLOWED_PROXY_SCHEMES)} -- got {server!r}"
        )
    if not parsed.hostname:
        raise ValidationError(f"proxy.server has no host: {server!r}")
    if not parsed.port:
        raise ValidationError(f"proxy.server has no port: {server!r}")

    out: dict = {"server": server}
    if username := str(proxy.get("username") or "").strip():
        out["username"] = username
    if password := str(proxy.get("password") or "").strip():
        out["password"] = password
    if tz := str(proxy.get("timezone_id") or "").strip():
        # not validated against the IANA database here -- resolve_timezone_id
        # (stealth/timezone.py) just hands whatever string this is straight
        # to Playwright's own `timezone_id` context option, which will
        # itself reject a bogus zone at browser-launch time; duplicating
        # that whole database here isn't worth it for a value an analyst
        # typed once and will notice is wrong the first time a session runs.
        out["timezone_id"] = tz
    return out


async def set_proxy(platform_id: str, session_id: str, proxy: Optional[dict]) -> dict:
    p = _get_platform(platform_id)
    if proxy and (p.uses_api_key or p.env_keys):
        # A per-session PROXY is a Playwright context option -- it only
        # means anything for the cookie-authed platforms that actually
        # launch a browser through sessions/manager.py::session_for_job.
        # YouTube (api_key) talks to a REST API directly, never a browser;
        # Telegram (env_keys/MTProto) connects via Telethon, which has its
        # own separate proxy mechanism this field was never wired to.
        # Accepting either here would store a proxy that LOOKS configured
        # in the pool but is silently never read by anything -- exactly the
        # trap this check exists to close.
        raise ConflictError(
            f"{platform_id}: has no per-session browser proxy "
            f"({'API-key' if p.uses_api_key else 'MTProto'} access doesn't route through one)"
        )
    item = await sessions_db.get_item(platform_id, session_id)
    if item is None:
        raise NotFoundError(f"{platform_id}: session {session_id!r} not in pool")
    if proxy:
        await sessions_db.update_item(platform_id, session_id, proxy=_validate_proxy(proxy))
    else:
        await sessions_db.unset_proxy(platform_id, session_id)
    return await status(platform_id)


async def delete(platform_id: str, session_id: str = "") -> dict:
    import os
    _get_platform(platform_id)
    if session_id:
        if await sessions_db.delete_item(platform_id, session_id):
            log.info(f"{platform_id}: removed session {session_id} from pool")
    else:
        n = await sessions_db.delete_pool(platform_id)
        if n:
            log.info(f"{platform_id}: whole session pool deleted ({n})")
    if platform_id == "youtube":
        os.environ.pop("YOUTUBE_API_KEY", None)
    elif platform_id == "telegram":
        os.environ.pop("TELEGRAM_API_ID", None)
        os.environ.pop("TELEGRAM_API_HASH", None)
        os.environ.pop("TELEGRAM_PHONE", None)
        from backend.config.settings import settings
        session_path = settings.session_blob_path / "telegram"
        for stale in (session_path.with_suffix(".session"), session_path.with_suffix(".session-journal")):
            if stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    pass
    return await status(platform_id)


# ---------- interactive login ----------

async def launch_login(platform_id: str, timeout_s: int = 300, identifier: str = "") -> dict:
    """Open a real browser and wait for the login cookie to appear.
    Deliberately headful and hands-off: a person logs in, this only
    watches for the cookie -- it never fills a password field."""
    p = _get_platform(platform_id)
    if platform_id not in LOGIN_FLOW:
        raise ValidationError(f"{platform_id} has no interactive login")

    url, proof = LOGIN_FLOW[platform_id]
    run = {"platform": platform_id, "status": "waiting", "message": "",
           "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "finished": ""}
    _logins[platform_id] = run

    from types import SimpleNamespace

    from backend.stealth.browser import Session

    opts = SimpleNamespace(headful=True, timeout=45, delay=0)
    session = Session(opts, [], load_images=True)
    try:
        ctx = await session.start()
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        run["message"] = "log in in the browser window"
        log.info(f"{platform_id}: waiting for manual login")

        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            cookies = await ctx.cookies()
            if any(c["name"] == proof for c in cookies):
                kept = load_cookies(json.dumps(cookies), p.cookie_domain)
                await sessions_db.add_item(platform_id, kept, identifier)
                run["status"], run["message"] = "saved", f"{len(kept)} cookies saved"
                log.info(f"{platform_id}: login captured ({len(kept)} cookies)")
                break
        else:
            run["status"], run["message"] = "timeout", "no login within the time limit"
    except Exception as e:
        run["status"], run["message"] = "failed", f"{type(e).__name__}: {e}"
        log.error(f"{platform_id}: login failed: {e}")
    finally:
        run["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await session.stop()
    return run


# ---------- background health monitor ----------

async def verify_session_item(platform_id: str, cookies: list[dict]) -> tuple[bool, str, bool]:
    """Exercises the platform's own check_session() against ONE specific
    set of cookies -- the same live check an analysis job runs at its own
    start, just invoked here without a job attached.

    Returns (ok, detail, conclusive). `conclusive` is False whenever the
    check itself couldn't run to completion -- browser launch failure, or
    the probe navigation raising (timeout, DNS failure, no internet in
    this environment right now, etc.) -- as opposed to the navigation
    succeeding and the platform's own page content showing a login/
    checkpoint wall. Only a conclusive result is trustworthy evidence that
    the SESSION (not the network) is the problem; a transient connectivity
    blip must never be recorded as "this session is now expired."
    """
    from backend.platforms import registry
    from backend.platforms.scan_options import ScanOptions

    plat = registry.get(platform_id)
    options = ScanOptions(evidence=None, delay=0, concurrency=1, headful=False)
    try:
        scraper = plat.scraper()(options, cookies)
    except Exception as e:
        return False, f"could not construct scraper: {type(e).__name__}: {e}", False
    try:
        await scraper.start()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", False
    try:
        ok = await scraper.check_session()
        return ok, "" if ok else "session invalid or checkpointed", True
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", False
    finally:
        try:
            await scraper.stop()
        except Exception:
            pass


async def _record_item_result(
    platform_id: str, session_id: str, identifier: str, ok: bool, detail: str, conclusive: bool = True,
) -> None:
    from backend.services import incident_service as incidents_engine

    previous = await sessions_db.record_item_health(platform_id, session_id, identifier, ok, detail)
    if not conclusive:
        # the check itself failed to run (network/transport error) -- leave
        # the session's actual status untouched, it may well still be fine
        if not ok:
            log.warning(f"{platform_id}/{identifier}: session check inconclusive, leaving status as-is -- {detail}")
        return
    if ok:
        # a passing live check is the strongest evidence a session works --
        # clear any quarantine and reset the consecutive-failure ladder
        await mark_session_ok(platform_id, session_id)
        return
    was_ok = previous is None or previous.get("ok", True)
    if was_ok and not ok:
        await mark_session_failed(platform_id, session_id, "expired")
        log.warning(f"{platform_id}/{identifier}: session went bad -- {detail}")
        await incidents_engine.record(
            platform_id, "session-check", "-- all clients --", "periodic-monitor",
            "SessionInvalid", f"Session {identifier!r} (id {session_id}): {detail or 'session invalid or checkpointed'}",
        )


async def _record_platform_summary(platform_id: str) -> None:
    summary = await pool_summary(platform_id)
    ok = summary["total"] == 0 or summary["available"] > 0
    detail = "" if ok else f"all {summary['total']} pooled sessions are unavailable ({summary['dead']} dead)"
    await sessions_db.record_platform_health(platform_id, ok, detail)
    if not ok:
        log.warning(f"{platform_id}: pool exhausted -- {detail}")


def _platform_busy(platform_id: str) -> bool:
    """Is a job currently using this platform's session pool?

    A job with `platform=None` sweeps EVERY ready platform -- which is what
    every discovery job is, and what the analysis catch-up is -- so it
    counts as busy for all of them. Matching only on `j.platform ==
    platform_id` meant a discovery sweep never registered as busy at all,
    and the monitor would launch up to BATCH_SIZE real browsers against
    cookies a live sweep was using at that moment. Two Playwright contexts
    on one account from one IP is the single most reliable way to earn a
    checkpoint.
    """
    from backend.services.job_service import job_manager

    return any(
        j.status == "running" and (j.platform is None or j.platform == platform_id)
        for j in job_manager.jobs.values()
    )


async def _pick_batch(platform_id: str, limit: int) -> list[tuple[str, str, list[dict]]]:
    items = await sessions_db.list_pool(platform_id)
    now = _now()
    candidates = [s for s in items if s["status"] not in DEAD_STATES and s["rate_limited_until"] <= now]
    if not candidates:
        return []
    stamped = []
    for s in candidates:
        last = await sessions_db.item_last_checked(platform_id, s["id"])
        stamped.append((last or datetime.min, s))
    stamped.sort(key=lambda t: t[0])
    return [(s["id"], s["identifier"], s["cookies"]) for _, s in stamped[:limit]]


async def verify_session(platform_id: str) -> tuple[bool, str, Optional[tuple[str, str]], bool]:
    from backend.platforms import registry

    plat = registry.get(platform_id)
    state = await registry.session_state(plat)
    if state != "ready":
        return False, f"session {state} (no cookies/credentials to check)", None, True
    if plat.uses_api_key or plat.env_keys:
        return True, "", None, True
    picked = await _pick_batch(platform_id, limit=1)
    if not picked:
        return False, "no available sessions in the pool to check", None, True
    session_id, identifier, cookies = picked[0]
    ok, detail, conclusive = await verify_session_item(platform_id, cookies)
    return ok, detail, (session_id, identifier), conclusive


async def check_one(platform_id: str) -> tuple[bool, str]:
    ok, detail, item, conclusive = await verify_session(platform_id)
    if item is not None:
        session_id, identifier = item
        await _record_item_result(platform_id, session_id, identifier, ok, detail, conclusive)
    await _record_platform_summary(platform_id)
    return ok, detail


async def check_all_once() -> dict[str, dict]:
    from backend.platforms import registry

    out: dict[str, dict] = {}
    for platform_id in MONITORED:
        if _platform_busy(platform_id):
            out[platform_id] = {"skipped": "job already running"}
            continue
        plat = registry.get(platform_id)
        if plat.uses_api_key or plat.env_keys:
            out[platform_id] = {"skipped": "no pool for this auth kind"}
            continue
        batch = await _pick_batch(platform_id, BATCH_SIZE)
        if not batch:
            out[platform_id] = {"skipped": "no available sessions to check"}
            await _record_platform_summary(platform_id)
            continue
        results = []
        for session_id, identifier, cookies in batch:
            ok, detail, conclusive = await verify_session_item(platform_id, cookies)
            await _record_item_result(platform_id, session_id, identifier, ok, detail, conclusive)
            results.append({"identifier": identifier, "ok": ok, "detail": detail})
        out[platform_id] = {"checked": len(batch), "results": results}
        await _record_platform_summary(platform_id)
    return out


async def cached_health() -> dict[str, dict]:
    return await sessions_db.cached_health()


async def _monitor_loop() -> None:
    while True:
        try:
            await check_all_once()
        except Exception as e:
            log.error(f"session monitor sweep failed: {type(e).__name__}: {e}")
        await asyncio.sleep(CHECK_INTERVAL_S)


def start_monitor() -> None:
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_monitor_loop())
        log.info(f"session monitor started -- checking every {CHECK_INTERVAL_S // 60}m")


def stop_monitor() -> None:
    global _monitor_task
    if _monitor_task is not None:
        _monitor_task.cancel()
        _monitor_task = None
