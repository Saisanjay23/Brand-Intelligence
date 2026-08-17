"""Where credentials come from when there is no Mongo.

The API path resolves a platform's login through the pooled `sessions`
collection (`sessions/manager.py::session_for_job`). With no database
there is no pool, so this reads the same three credential KINDS off the
filesystem and the process environment instead, and hands back the exact
same `session_item` dict shape the pool would have, `{id, identifier,
cookies, proxy, api_key}`, so `runner.py` constructs adapters
identically either way.

    cookies   facebook / twitter / instagram / tiktok
    api key   youtube
    mtproto   telegram (env credentials + Telethon's own .session file)

LAYOUT, everything is optional; a platform with nothing here is reported
`missing` and skipped, never fatal:

    <creds-dir>/
        facebook.json           one account
        twitter/                a pool: every *.json|*.txt in here
            main.json
            backup.json
        instagram.txt           Netscape cookies.txt is fine too
        youtube.key             or just set YOUTUBE_API_KEY
        proxies.json            {"facebook": {"server": "...", ...}}

Cookie parsing is `sessions/cookies.py::load_cookies` unchanged. JSON
exports, Playwright storage_state, Netscape cookies.txt and raw `Cookie:`
header strings all already work, and re-implementing that here would be
one more thing to keep in sync for no gain.

The one shape `load_cookies` does NOT understand is this project's own
legacy pool file, `{"version": 2, "sessions": [...]}`, which is what the
repo's `session/*.json` files actually are, several accounts in one
file, each with its own identifier, proxy and status. Those are read here
(same reader as `database/migrations/migrate_sessions_to_mongo.py`, which
is where that format is authoritatively described) because pointing
`--creds session` at the directory already on disk is the obvious first
thing anyone will try, and one file yielding a whole pool is exactly the
multi-credential rotation this module wants anyway. Entries marked
expired/checkpointed/unreadable are skipped: the file already records that
verdict, and re-trying a known-dead account just to rediscover it wastes a
browser launch.

ROTATION is deliberately weaker than the pooled path's. The pool picks
least-recently-used from durable per-session counters; with no database
there is nowhere to keep those across runs, so this rotates in file order
and moves to the next credential when one is rejected mid-run. Within a
single run that is the behaviour that matters (a dead session must not
abort the job); across runs, ordering simply restarts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from backend.config.settings import settings
from backend.platforms import registry
from backend.sessions.cookies import load_cookies, normalize_cookies
from backend.shared.logging import get_logger

log = get_logger("engine.credentials")

COOKIE_SUFFIXES = (".json", ".txt", ".cookies")

# Restated from `sessions/manager.py` rather than imported: that module
# imports `session_repository`, and pulling Motor into this package would
# defeat its entire purpose. Three string literals are a cheap copy; the
# import would not be.
DEAD_STATES = {"expired", "checkpointed", "unreadable"}

# ready    -> a job started right now could actually run
# incomplete -> a credential exists but is not usable (missing required
#               cookies, half-configured MTProto), a different fix from
#               "missing", exactly as `sessions/manager.py::state_for` means it
# missing  -> nothing configured for this platform at all
READY, INCOMPLETE, MISSING = "ready", "incomplete", "missing"


def default_dir() -> Path:
    """Where credentials live unless a caller says otherwise.

    `BI_CREDENTIALS_DIR` first so a container can mount them anywhere,
    then a `credentials/` directory beside the repo root, next to
    `session/` and `evidence/`, which are the other two things this engine
    keeps on disk.
    """
    env = os.environ.get("BI_CREDENTIALS_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(settings.session_blob_path).parent / "credentials"


class CredentialStore:
    """Reads credentials once, then answers `state_for` / `sessions_for`.

    Deliberately eager: a run that is going to fail for want of a cookie
    file should say so before it opens a browser, not four keywords in.
    """

    def __init__(self, cookie_dir: Optional[Path] = None) -> None:
        self.dir = Path(cookie_dir) if cookie_dir else default_dir()
        self._proxies = self._read_proxies()
        self._cache: dict[str, list[dict]] = {}
        # Sessions rejected during THIS run (expired cookies, a checkpoint).
        # Kept in memory only, with no database there is nothing to write
        # a quarantine to, and a fresh process should retry a credential
        # that may since have been fixed rather than inherit a stale verdict.
        self._burned: set[str] = set()

    # ---------- discovery of credential files ----------

    def _read_proxies(self) -> dict[str, dict]:
        path = self.dir / "proxies.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"ignoring unreadable {path.name}: {type(e).__name__}: {e}")
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _proxy_for(self, platform_id: str) -> Optional[dict]:
        return self._proxies.get(platform_id) or self._proxies.get("_default") or None

    def _cookie_files(self, platform_id: str) -> list[Path]:
        """Every cookie file belonging to one platform, in a stable order.

        Sorted so two runs on the same directory pick the same credential
        first, with no durable last-used counter, reproducibility is the
        only ordering guarantee worth having.
        """
        found: list[Path] = []
        sub = self.dir / platform_id
        if sub.is_dir():
            found += [p for p in sub.iterdir() if p.is_file() and p.suffix.lower() in COOKIE_SUFFIXES]
        if self.dir.is_dir():
            for p in self.dir.iterdir():
                if not p.is_file() or p.suffix.lower() not in COOKIE_SUFFIXES:
                    continue
                # `facebook.json` and `facebook-backup.json`, but never
                # `proxies.json` and never another platform's file
                stem = p.stem.lower()
                if stem == platform_id or stem.startswith(f"{platform_id}-") or stem.startswith(f"{platform_id}_"):
                    found.append(p)
        return sorted(set(found), key=lambda p: str(p).lower())

    # ---------- the session_item shape runner.py consumes ----------

    def sessions_for(self, platform_id: str) -> list[dict]:
        """Usable credentials for one platform, best first, minus anything
        this run has already burned."""
        if platform_id not in self._cache:
            self._cache[platform_id] = self._build(platform_id)
        return [s for s in self._cache[platform_id] if s["id"] not in self._burned]

    def _build(self, platform_id: str) -> list[dict]:
        try:
            plat = registry.get(platform_id)
        except KeyError:
            return []
        if plat.uses_api_key:
            return self._api_key_sessions(plat)
        if plat.env_keys:
            return self._mtproto_sessions(plat)
        return self._cookie_sessions(plat)

    def _from_pool_file(self, path: Path, plat) -> Optional[list[dict]]:
        """This project's own `{"version": 2, "sessions": [...]}` pool file
        -> one entry per live account in it, or None when `path` is not one
        (in which case the caller falls back to `load_cookies`).

        Returning None rather than [] matters: an empty pool file is a real
        answer ("this file holds no usable accounts"), while "not a pool
        file" means some other parser should get a turn at it.
        """
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("sessions"), list):
            return None

        out: list[dict] = []
        for entry in obj["sessions"]:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status", "")) in DEAD_STATES:
                log.info(f"[{plat.id}] {path.name}: skipping {entry.get('identifier')!r} "
                         f"(recorded {entry.get('status')})")
                continue
            cookies = normalize_cookies(entry.get("cookies") or [], plat.cookie_domain)
            if not cookies:
                continue
            out.append({
                "id": f"{path}#{entry.get('id') or entry.get('identifier') or len(out)}",
                "identifier": str(entry.get("identifier") or path.stem),
                "cookies": cookies,
                "proxy": entry.get("proxy") or self._proxy_for(plat.id),
                "source": f"{path} (pool entry)",
            })
        return out

    def _cookie_sessions(self, plat) -> list[dict]:
        out: list[dict] = []
        proxy = self._proxy_for(plat.id)
        for path in self._cookie_files(plat.id):
            pooled = self._from_pool_file(path, plat)
            if pooled is not None:
                out += pooled
                continue
            try:
                cookies = load_cookies(str(path), plat.cookie_domain)
            except (OSError, ValueError) as e:
                log.warning(f"[{plat.id}] skipping {path.name}: {type(e).__name__}: {e}")
                continue
            out.append({
                "id": str(path), "identifier": path.stem, "cookies": cookies,
                "proxy": proxy, "source": str(path),
            })
        # A cookie blob straight from the environment, for a deployment that
        # injects secrets rather than mounting files. `load_cookies` takes a
        # literal string as readily as a path, so both work unchanged.
        raw = os.environ.get(f"BI_{plat.id.upper()}_COOKIES", "").strip()
        if raw:
            try:
                out.append({
                    "id": f"env:{plat.id}", "identifier": f"{plat.id} (env)",
                    "cookies": load_cookies(raw, plat.cookie_domain),
                    "proxy": proxy, "source": f"BI_{plat.id.upper()}_COOKIES",
                })
            except ValueError as e:
                log.warning(f"[{plat.id}] BI_{plat.id.upper()}_COOKIES unparseable: {e}")
        return out

    def _api_key_sessions(self, plat) -> list[dict]:
        key = os.environ.get(plat.api_key_env, "").strip()
        source = plat.api_key_env
        if not key:
            for name in (f"{plat.id}.key", f"{plat.id}.txt"):
                path = self.dir / name
                if path.exists():
                    key, source = path.read_text(encoding="utf-8").strip(), str(path)
                    break
        if not key:
            return []
        # The adapters read this out of the environment themselves (see
        # `platforms/youtube/*`), exactly as the pooled path does in
        # `sessions/manager.py::state_for`, setting it here is what makes
        # a key from a FILE work without touching those adapters.
        os.environ[plat.api_key_env] = key
        return [{"id": f"key:{plat.id}", "identifier": f"{plat.id} api key",
                 "cookies": [], "proxy": None, "api_key": key, "source": source}]

    def _mtproto_sessions(self, plat) -> list[dict]:
        """Telegram: env credentials plus Telethon's own .session file.

        Both halves are required. api_id/api_hash alone cannot log in:
        the interactive phone-code login that produces the .session file
        has to have been done once already (the API path does it through
        `services/telegram_login_service.py`; standalone, copy the file in).
        """
        missing = [k for k in plat.env_keys if not os.environ.get(k)]
        blob = Path(settings.session_blob_path) / (plat.session_blob or f"{plat.id}.session")
        if missing or not blob.exists():
            why = []
            if missing:
                why.append(f"unset: {', '.join(missing)}")
            if not blob.exists():
                why.append(f"no session file at {blob}")
            return [{"id": f"mtproto:{plat.id}", "identifier": f"{plat.id} mtproto",
                     "cookies": [], "proxy": None, "incomplete": "; ".join(why), "source": "env"}]
        return [{"id": f"mtproto:{plat.id}", "identifier": f"{plat.id} mtproto",
                 "cookies": [], "proxy": None, "source": "env + " + str(blob)}]

    # ---------- readiness ----------

    def state_for(self, platform_id: str) -> str:
        """ready | incomplete | missing, the standalone counterpart of
        `registry.session_state`, resolved against files and env instead of
        against Mongo. Same three words on purpose: an operator moving
        between the two modes should not have to learn a second vocabulary.
        """
        try:
            plat = registry.get(platform_id)
        except KeyError:
            return MISSING
        items = self.sessions_for(platform_id)
        if not items:
            return MISSING
        if plat.uses_api_key:
            return READY
        if plat.env_keys:
            return INCOMPLETE if any(i.get("incomplete") for i in items) else READY
        # A cookie export is only proof of a login if it actually carries the
        # cookies that constitute one. Two half-broken exports must not add
        # up to a working session, so this is per-item, never a union across
        # the pool, the same correctness point `sessions/manager.py`
        # documents at length for the Mongo-backed pool.
        required = set(plat.required_cookies)
        for item in items:
            if required <= {c["name"] for c in item["cookies"] if c.get("name")}:
                return READY
        return INCOMPLETE

    def why_not(self, platform_id: str) -> str:
        """A sentence an operator can act on, for a platform that is not
        ready. `state_for` alone says a run was skipped; this says what to
        go and fix."""
        try:
            plat = registry.get(platform_id)
        except KeyError:
            return f"unknown platform {platform_id!r}"
        state = self.state_for(platform_id)
        if state == READY:
            return ""
        if plat.uses_api_key:
            return f"set {plat.api_key_env}, or put the key in {self.dir / (plat.id + '.key')}"
        if plat.env_keys:
            items = self.sessions_for(platform_id)
            detail = next((i.get("incomplete", "") for i in items if i.get("incomplete")), "")
            return detail or f"set {', '.join(plat.env_keys)} and provide {plat.session_blob}"
        files = self._cookie_files(plat.id)
        if state == MISSING:
            if files:
                # The distinction matters: "re-export while logged in" is
                # useless advice when the real problem is that every account
                # in a pool file is already marked dead, or the file is not
                # a cookie export at all.
                return (f"{len(files)} file(s) found ({', '.join(f.name for f in files)}) but no live "
                        f"cookies in them -- every entry is expired/checkpointed, or the file is not a "
                        f"cookie export")
            return f"no cookie file -- add {self.dir / (plat.id + '.json')} (JSON export or cookies.txt)"
        return (f"cookie file(s) present but missing required cookie(s): "
                f"{', '.join(plat.required_cookies)} -- re-export while logged in")

    def burn(self, session_id: str, why: str = "") -> None:
        """Take one credential out of rotation for the rest of this run."""
        self._burned.add(session_id)
        log.warning(f"credential {session_id} out of rotation{': ' + why if why else ''}")

    def report(self) -> list[dict]:
        """Every platform's readiness, what `python -m backend.engine
        platforms` prints, and the first thing to check when a run finds
        nothing."""
        out = []
        for platform_id, plat in registry.PLATFORMS.items():
            if not plat.enabled:
                continue
            state = self.state_for(platform_id)
            out.append({
                "platform": platform_id, "name": plat.name, "state": state,
                "kind": "api-key" if plat.uses_api_key else "mtproto" if plat.env_keys else "cookies",
                "can_discover": plat.can_discover,
                "analysis_stub": plat.analysis_stub,
                "sessions": len([s for s in self.sessions_for(platform_id) if not s.get("incomplete")]),
                "fix": self.why_not(platform_id),
                "stability_note": plat.stability_note,
            })
        return out
