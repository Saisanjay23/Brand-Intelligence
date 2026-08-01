"""Drives a logged-in browser over Facebook profiles and reads their fields.

This module owns the visit sequence. The browser session itself is
facebook/browser.py; turning what a visit collected into report fields is
facebook/analysis/readers.py; holding and scoping the payloads is
facebook/analysis/harvest.py.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Optional

from backend.engine.row import Row
from backend.shared.text import fmt_created, iter_dicts
from backend.platforms.facebook.analysis import readers
from backend.platforms.facebook.analysis.harvest import Harvest
from backend.platforms.facebook.constants import (RE_CHECKPOINT, RE_GONE,
                                                  RE_LOGIN)
from backend.platforms.facebook.session import FacebookSession
from backend.platforms.facebook.urls import normalize_url, profile_id, tab_url


class Scraper:
    """One logged-in browser session, driven over a list of profiles."""

    # callers normalise URLs without knowing which platform they are holding
    normalize_url = staticmethod(normalize_url)

    def __init__(
        self,
        args,
        cookies: list[dict],
        session_id: str = "",
        proxy: Optional[dict] = None,
    ):
        self.a = args
        self.evidence = Path(args.evidence) if args.evidence else None
        if self.evidence:
            self.evidence.mkdir(parents=True, exist_ok=True)
        # evidence screenshots need images, so the session must not block them
        self.session = FacebookSession(
            args,
            cookies,
            load_images=bool(self.evidence),
            session_id=session_id,
            proxy=proxy,
        )

    # ───────────────────────────── browser ────────────────────────────── #

    @property
    def ctx(self):
        return self.session.ctx

    async def start(self):
        await self.session.start()

    async def stop(self):
        await self.session.stop()

    async def pause(self, mult=1.0):
        await self.session.pause(mult)

    async def check_session(self) -> bool:
        return await self.session.check_session()

    # ─────────────────────────── page scripts ─────────────────────────── #

    # Ready when the profile's own payload has landed: the social-context block
    # plus, once we know it, the entity id. Everything we extract is present at
    # that point -- typically under 2s -- so waiting a fixed 3.5s is dead time.
    JS_READY = """
    (needle) => {
      let ctx = false, id = !needle;
      for (const el of document.querySelectorAll('script[type="application/json"]')) {
        const t = el.textContent || "";
        if (!ctx && t.includes('profile_social_context')) ctx = true;
        if (!id && t.includes('"id":"' + needle + '"')) id = true;
        if (ctx && id) return true;
      }
      return false;
    }
    """

    # The server render ships far more GraphQL in <script type="application/json">
    # than the XHR traffic does -- that is where the profile entity lives.
    JS_EMBEDDED = (
        "() => Array.from(document.querySelectorAll("
        "'script[type=\"application/json\"]')).map(s => s.textContent)"
        ".filter(t => t && t.length > 40)"
    )

    # Reads the profile header itself: the name is the line directly above the
    # counter chip, the avatar is the largest <image> on the page, and
    # "set=pb.<id>." photo links give us the owner's numeric id even when the
    # URL is a vanity slug.
    JS_HEADER = """
    () => {
      const lines = (document.body.innerText || "").split("\\n").map(s => s.trim());
      let name = "", followers = "", counter = "";
      for (let i = 0; i < lines.length; i++) {
        // the header counter line: "70 followers - 8 following" on creators,
        // "53 friends" on personal profiles, "154M followers" on pages
        const m = /^([\\d][\\d.,\\s]{0,15}[KMB]?)\\s*(followers?|friends?|likes)\\b/i.exec(lines[i]);
        if (!m) continue;
        counter = lines[i];
        const fm = /([\\d][\\d.,\\s]{0,15}[KMB]?)\\s*followers?\\b/i.exec(lines[i]);
        if (fm) followers = fm[1];
        for (let j = i - 1; j >= 0 && j >= i - 4; j--) {
          const c = lines[j];
          if (c && c.length < 80 && !/^[\\d,.]+$/.test(c) &&
              !/notification|^search$|^facebook$|^add friend$|^follow$/i.test(c)) {
            name = c; break;
          }
        }
        break;
      }
      let postAuthor = "";
      for (const e of document.querySelectorAll('[aria-label]')) {
        const l = e.getAttribute('aria-label') || "";
        if (/^Actions for this post by /i.test(l)) {
          postAuthor = l.replace(/^Actions for this post by /i, "").trim();
          break;
        }
      }
      let avatar = "", best = -1;
      for (const im of document.querySelectorAll('svg image')) {
        const href = im.getAttribute('xlink:href') || im.getAttribute('href') || "";
        const w = im.getBoundingClientRect().width;
        if (href && w > best) { best = w; avatar = href; }
      }
      const pbIds = Array.from(document.querySelectorAll('a[href*="set=pb."]'))
        .map(a => ((a.getAttribute('href') || "").match(/set=pb\\.(\\d+)\\./) || [])[1])
        .filter(Boolean);
      return {name, followers, counter, postAuthor, avatar, pbIds};
    }
    """

    # ──────────────────────────── collection ──────────────────────────── #

    async def visit(
        self, page, url, h: Harvest, tag, scrolls=0, needle: Optional[str] = None
    ) -> bool:
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.a.timeout * 1000
            )
        except Exception:
            return False
        try:
            if needle is not None:
                try:
                    await page.wait_for_function(
                        self.JS_READY, arg=needle, timeout=self.a.settle * 1000
                    )
                except Exception:
                    # gone, login-walled or an unusual layout -- fall through and
                    # let the field readers and status checks report what they see
                    await page.wait_for_timeout(1500)
            else:
                await page.wait_for_timeout(1500)
            for _ in range(scrolls):
                await page.mouse.wheel(0, 2600)
                await page.wait_for_timeout(1200)
        except Exception:
            pass
        try:
            h.html[tag] = await page.content()
        except Exception:
            h.html[tag] = ""
        try:
            h.text[tag] = await page.inner_text("body")
        except Exception:
            h.text[tag] = re.sub(r"<[^>]+>", " ", h.html.get(tag, ""))
        try:
            h.add_embedded(await page.evaluate(self.JS_EMBEDDED))
        except Exception:
            pass
        return bool(h.html[tag])

    async def read_dom(self, page, h: Harvest, scrolled: bool = False) -> None:
        try:
            if scrolled:
                # scrolling can unmount the intro block -- go back up first
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(700)
            h.dom = await page.evaluate(self.JS_HEADER) or {}
        except Exception:
            h.dom = {}

    async def screenshot(self, page, row: Row) -> None:
        if not self.evidence:
            return
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", row.profile_id or "entity")[:60]
        shot = self.evidence / f"{stem}_{int(time.time())}.png"
        try:
            await page.screenshot(path=str(shot))
            row.screenshot = str(shot)
        except Exception:
            pass

    # ───────────────────────────── identity ───────────────────────────── #

    @staticmethod
    def entity_id_for(h: Harvest, url: str) -> str:
        """Numeric id for a vanity URL, taken from the payloads.

        The entity that owns the page carries its own canonical url/vanity, so
        matching the slug against those gives the id without trusting the DOM.
        """
        slug = profile_id(url).lower()
        if not slug or slug.isdigit():
            return ""
        for blob in h.mentioning(slug):
            for d in iter_dicts(blob):
                i = d.get("id")
                if not (isinstance(i, str) and i.isdigit()):
                    continue
                for k in ("url", "profile_url"):
                    v = d.get(k)
                    if isinstance(v, str) and v.lower().rstrip("/").endswith(
                        "/" + slug
                    ):
                        return i
                for k in ("vanity", "userVanity", "username"):
                    v = d.get(k)
                    if isinstance(v, str) and v.lower() == slug:
                        return i
        return ""

    @staticmethod
    def owner_id(h: Harvest) -> str:
        """Most frequent id in the profile's own photo-album links."""
        ids = h.dom.get("pbIds") or []
        return max(set(ids), key=ids.count) if ids else ""

    def resolve_id(self, row: Row, h: Harvest, url: str) -> str:
        """Vanity URLs carry no numeric id -- ask the payloads, then the DOM."""
        if row.profile_id.isdigit():
            return row.profile_id
        pid = self.entity_id_for(h, url) or self.owner_id(h)
        if not pid:
            row.note("id unresolved -- fields not scope-verified")
        else:
            row.mark("id", pid)
            # adopt the numeric id as this row's identity. Discovery stores the
            # numeric id, so leaving the vanity slug here would file the same
            # profile twice -- once per URL shape.
            row.profile_id = pid
        return pid

    @staticmethod
    def blocked_status(row: Row, page_url: str, txt: str) -> bool:
        """Session or availability problems that make field reading pointless."""
        if "/checkpoint" in page_url or RE_CHECKPOINT.search(txt):
            row.status, msg = "CHECKPOINT", "session checkpointed"
        elif "/login" in page_url or RE_LOGIN.search(txt):
            row.status, msg = "LOGIN_REQUIRED", "cookies rejected/expired"
        elif RE_GONE.search(txt):
            row.status = "GONE"
            msg = "removed or unavailable -- may already be taken down"
        else:
            return False
        row.note(msg)
        return True

    # ───────────────────────────── per URL ────────────────────────────── #

    async def process(self, raw_url: str, target: str, feed: str) -> Row:
        url = normalize_url(raw_url)
        row = Row(url=url, target=target, original_feed=feed)
        row.profile_id = profile_id(url)

        page = await self.ctx.new_page()
        h = Harvest()

        async def on_response(resp):
            try:
                if "/api/graphql" in resp.url and resp.request.resource_type in (
                    "xhr",
                    "fetch",
                ):
                    h.add_gql(await resp.text())
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        try:
            needle = row.profile_id if row.profile_id.isdigit() else ""
            if not await self.visit(
                page, url, h, "main", scrolls=self.a.scrolls, needle=needle
            ):
                row.status = "ERROR"
                row.note("main nav failed")
                return row

            txt = h.text.get("main", "")
            if self.blocked_status(row, page.url, txt):
                if row.status == "GONE":
                    readers.read_name(row, h)
                return row

            await self.read_dom(page, h, scrolled=self.a.scrolls > 0)
            pid = self.resolve_id(row, h, url)
            hs = h.scoped(pid)

            if (hs.ent_scalar("__typename") or "").lower() == "page" or (
                not hs.ents
                and re.search(
                    r'"__typename"\s*:\s*"Page"|Page transparency', h.all_html()
                )
            ):
                row.entity_type = "page"

            readers.read_profile(row, hs)
            await self.screenshot(page, row)

            # The main profile page rarely carries a location -- Facebook Pages
            # put their city/country on the About tab instead, so this visit
            # happens unconditionally: accuracy on a field the report actually
            # promises beats saving one page load. Join date is a different
            # story -- Facebook does not expose it to an ordinary session at
            # all, not in the rendered tab and not in any payload, so that
            # attempt alone stays opt-in via --about since it essentially
            # never succeeds and isn't worth chasing by default.
            await self.pause(0.4)
            for sk in ("about_profile_transparency", "about"):
                await self.visit(page, tab_url(url, sk), h, sk)
                if self.a.about:
                    readers.read_created(row, h.scoped(pid))
                    if row.created_iso:
                        break
                await self.pause(0.3)
            if self.a.about and not row.created_iso:
                row.note("join date not visible on About this account")
            elif not self.a.about:
                row.note("join date not attempted (pass --about)")

            readers.read_location(row, h.scoped(pid))

            row.status = "OK" if row.profile_name else "PARTIAL"
            return row
        finally:
            try:
                await page.close()
            except Exception:
                pass

    # ─────────────────────────── orchestration ────────────────────────── #

    async def one(self, u: str, tgt: str, feed: str) -> Row:
        """process() with a failed profile turned into a reportable row."""
        try:
            return await self.process(u, tgt, feed)
        except Exception as e:
            row = Row(url=normalize_url(u), target=tgt, original_feed=feed)
            row.profile_id = profile_id(row.url)
            row.status = "ERROR"
            row.note(f"{type(e).__name__}: {e}")
            return row

    @staticmethod
    def report(i: int, total: int, u: str, row: Row) -> None:
        print(f"[{i}/{total}] {u}", file=sys.stderr)
        print(
            f"    {row.status:<14} name={row.profile_name[:22]:<22} "
            f"created={fmt_created(row.created_iso) or '-':<10} "
            f"followers={row.followers if row.followers is not None else '-':<7} "
            f"friends={row.friends if row.friends is not None else '-':<6} "
            f"active={row.active_yes or '-':<3} "
            f"risk={row.risk} {row.priority}",
            file=sys.stderr,
        )

    async def run(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        if getattr(self.a, "concurrency", 1) > 1:
            return await self.run_parallel(jobs)
        rows: list[Row] = []
        for i, (u, tgt, feed) in enumerate(jobs, 1):
            row = await self.one(u, tgt, feed)
            rows.append(row)
            self.report(i, len(jobs), u, row)
            if row.status == "CHECKPOINT" and not self.a.keep_going:
                print(
                    "\nCHECKPOINT -- aborting to avoid burning the session.",
                    file=sys.stderr,
                )
                break
            if i < len(jobs):
                await self.pause()
        return rows

    async def run_parallel(self, jobs: list[tuple[str, str, str]]) -> list[Row]:
        """Several tabs at once, same session. Faster, and more conspicuous."""
        sem = asyncio.Semaphore(self.a.concurrency)
        done = 0

        async def worker(idx: int, job: tuple[str, str, str]) -> tuple[int, Row]:
            nonlocal done
            async with sem:
                await asyncio.sleep(idx % self.a.concurrency * 1.5)  # stagger
                row = await self.one(*job)
                done += 1
                self.report(done, len(jobs), job[0], row)
                await self.pause(0.5)
                return idx, row

        pairs = await asyncio.gather(*(worker(i, j) for i, j in enumerate(jobs)))
        return [r for _, r in sorted(pairs, key=lambda p: p[0])]
