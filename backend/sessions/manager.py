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
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

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
    "linkedin": ("https://www.linkedin.com/login", "li_at"),
}

CHECK_INTERVAL_S = 30 * 60  # generous on purpose -- this opens a real browser
BATCH_SIZE = 5  # sessions live-checked per platform per monitor sweep
MONITORED = ("facebook", "instagram", "twitter", "telegram", "linkedin")

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
        "cookie_count": len(s["cookies"]), "proxy_host": proxy_host,
    }


# ---------- state ----------

async def state_for(platform_id: str) -> str:
    """ready | missing | incomplete -- called by platforms.registry."""
    p = _get_platform(platform_id)
    if not p.uses_cookies:
        return "ready"
    items = await sessions_db.list_pool(platform_id)
    if not items:
        return "missing"
    names: set[str] = set()
    for item in items:
        names.update(c["name"] for c in item["cookies"])
    return "ready" if set(p.required_cookies) <= names else "incomplete"


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
        "cookie_count": sum(len(s["cookies"]) for s in items),
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
    chosen["status"], chosen["rate_limited_until"], chosen["last_used"] = "ready", 0.0, now
    return chosen


async def session_for_job(platform_id: str) -> tuple[object, dict]:
    """What a discovery/analysis job needs to actually run: the Platform
    metadata plus either `{}` (key/MTProto-authed) or a healthy pooled
    session's cookies+proxy."""
    from backend.platforms import registry

    plat = _get_platform(platform_id)
    if plat.uses_api_key or plat.env_keys:
        state = await registry.session_state(plat)
        if state != "ready":
            raise ConflictError(f"{platform_id} credentials {state}")
        return plat, {}
    item = await get_healthy_session(platform_id)
    if item is None:
        raise ConflictError(f"{platform_id}: no healthy sessions available -- please add more cookies")
    return plat, {"id": item["id"], "identifier": item["identifier"], "cookies": item["cookies"], "proxy": item["proxy"]}


async def mark_session_failed(platform_id: str, session_id: str, reason: str = "expired", rate_limited_until: float = 0) -> None:
    """Quarantine one session. No-ops for key/MTProto-authed platforms,
    which have no pool at all (session_id is empty there)."""
    if not session_id:
        return
    fields: dict = {"status": reason}
    if rate_limited_until:
        fields["rate_limited_until"] = float(rate_limited_until)
    if await sessions_db.update_item(platform_id, session_id, **fields):
        log.warning(f"{platform_id} session {session_id} marked {reason}")


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
    await sessions_db.add_item(platform_id, cookies, identifier)
    return await status(platform_id)


async def save_api_key(platform_id: str, key: str) -> dict:
    from backend.config.settings import write_env

    p = _get_platform(platform_id)
    if not p.uses_api_key:
        raise ConflictError(f"{platform_id}: does not use an API key")
    key = key.strip()
    if not key:
        raise ValidationError("empty key")
    write_env(p.api_key_env, key)
    log.info(f"{platform_id}: API key saved")
    return await status(platform_id)


async def set_proxy(platform_id: str, session_id: str, proxy: Optional[dict]) -> dict:
    item = await sessions_db.get_item(platform_id, session_id)
    if item is None:
        raise NotFoundError(f"{platform_id}: session {session_id!r} not in pool")
    if proxy:
        await sessions_db.update_item(platform_id, session_id, proxy=proxy)
    else:
        await sessions_db.unset_proxy(platform_id, session_id)
    return await status(platform_id)


async def delete(platform_id: str, session_id: str = "") -> dict:
    _get_platform(platform_id)
    if session_id:
        if await sessions_db.delete_item(platform_id, session_id):
            log.info(f"{platform_id}: removed session {session_id} from pool")
    else:
        n = await sessions_db.delete_pool(platform_id)
        if n:
            log.info(f"{platform_id}: whole session pool deleted ({n})")
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

async def verify_session_item(platform_id: str, cookies: list[dict]) -> tuple[bool, str]:
    """Exercises the platform's own check_session() against ONE specific
    set of cookies -- the same live check an analysis job runs at its own
    start, just invoked here without a job attached."""
    from backend.platforms import registry
    from backend.platforms.scan_options import ScanOptions

    plat = registry.get(platform_id)
    options = ScanOptions(evidence=None, delay=0, concurrency=1, headful=False)
    try:
        scraper = plat.scraper()(options, cookies)
    except Exception as e:
        return False, f"could not construct scraper: {type(e).__name__}: {e}"
    try:
        await scraper.start()
        ok = await scraper.check_session()
        return ok, "" if ok else "session invalid or checkpointed"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            await scraper.stop()
        except Exception:
            pass


async def _record_item_result(platform_id: str, session_id: str, identifier: str, ok: bool, detail: str) -> None:
    from backend.services import incident_service as incidents_engine

    previous = await sessions_db.record_item_health(platform_id, session_id, identifier, ok, detail)
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
    from backend.services.job_service import job_manager

    return any(j.platform == platform_id and j.status == "running" for j in job_manager.jobs.values())


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


async def verify_session(platform_id: str) -> tuple[bool, str, Optional[tuple[str, str]]]:
    from backend.platforms import registry

    plat = registry.get(platform_id)
    state = await registry.session_state(plat)
    if state != "ready":
        return False, f"session {state} (no cookies/credentials to check)", None
    if plat.uses_api_key or plat.env_keys:
        return True, "", None
    picked = await _pick_batch(platform_id, limit=1)
    if not picked:
        return False, "no available sessions in the pool to check", None
    session_id, identifier, cookies = picked[0]
    ok, detail = await verify_session_item(platform_id, cookies)
    return ok, detail, (session_id, identifier)


async def check_one(platform_id: str) -> tuple[bool, str]:
    ok, detail, item = await verify_session(platform_id)
    if item is not None:
        session_id, identifier = item
        await _record_item_result(platform_id, session_id, identifier, ok, detail)
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
            ok, detail = await verify_session_item(platform_id, cookies)
            await _record_item_result(platform_id, session_id, identifier, ok, detail)
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
