"""Everything collected from one profile visit, and how to read it back.

`scoped()` is the important part: it narrows the collected payloads to the
entity that IS this profile, which is what keeps other people's names, follower
counts and post timestamps out of the record.

Embedded payloads are kept as raw text and parsed on demand. A profile page
ships ~180 of them and only a handful mention the profile at all, so scoping
substring-filters first and parses second -- the same answer for a fraction of
the work.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from backend.shared.text import iter_dicts, iter_kv


class Harvest:
    def __init__(self):
        self.gql: list[Any] = []  # parsed XHR /api/graphql lines
        self.raw: list[str] = []  # unparsed script[type=application/json]
        self.html: dict[str, str] = {}
        self.text: dict[str, str] = {}
        self.ents: list[dict] = []  # dicts that ARE this profile (id == pid)
        self.dom: dict[str, Any] = {}  # header fields read straight off the page
        self._scopes: dict[str, "Harvest"] = {}

    # ---------- collection ----------

    def add_gql(self, body: str) -> None:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    self.gql.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def add_embedded(self, texts) -> None:
        self.raw.extend(t for t in texts or [] if t)
        self._scopes.clear()  # new payloads invalidate cached views

    # ---------- lookup ----------

    def gql_raw(self) -> str:
        try:
            return json.dumps(self.gql)
        except (TypeError, ValueError):
            return ""

    def mentioning(self, needle: str) -> Iterator[Any]:
        """Parsed payloads whose text contains `needle` -- embedded, then XHR.

        The needle is the bare id, not `"id":"<id>"`. Matching the keyed form
        would depend on the payload having no space after the colon, which is
        true of Facebook's compact JSON today and would silently return
        nothing the day it stops being true.
        """
        for t in self.raw:
            if needle in t:
                try:
                    yield json.loads(t)
                except (json.JSONDecodeError, ValueError):
                    continue
        for blob in self.gql:
            try:
                if needle in json.dumps(blob):
                    yield blob
            except (TypeError, ValueError):
                continue

    def scoped(self, pid: str) -> "Harvest":
        """A view of the payloads narrowed to this profile.

        `ents` holds the dicts whose own id is the profile id -- the GraphQL
        objects that ARE this profile. Reading a field off one of those is
        unambiguous. The wider page carries the notification flyout, friend
        suggestions and sponsored payloads, all with their own name, follower
        and timestamp keys; anything read from the unfiltered pile belongs to
        whoever happened to load first.

        An unverifiable id scopes to nothing, on purpose: blank beats wrong.
        """
        if pid in self._scopes:
            return self._scopes[pid]

        v = Harvest()
        v.html, v.text, v.dom = self.html, self.text, self.dom
        if pid and pid.isdigit():
            for blob in self.mentioning(pid):
                for d in iter_dicts(blob):
                    if d.get("id") == pid and len(d) > 1:
                        v.ents.append(d)
            for blob in self.gql:
                try:
                    if pid in json.dumps(blob):
                        v.gql.append(blob)
                except (TypeError, ValueError):
                    continue
        self._scopes[pid] = v
        return v

    # ---------- entity readers ----------

    def ent_scalar(self, key: str) -> str:
        """A field read directly off the profile entity -- no nesting, no guessing."""
        for d in self.ents:
            v = d.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    def ent_path(self, *paths: str) -> str:
        for d in self.ents:
            for p in paths:
                cur: Any = d
                for part in p.split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if isinstance(cur, str) and cur.strip():
                    return cur.strip()
        return ""

    def ent_social(self) -> list[str]:
        """The header chips: '70 followers', '8 following', '328 friends'.

        Facebook ships these already rendered, so this is the only GraphQL form
        of the follower count -- there is no integer field anywhere in the
        entity. Two shapes occur: content[].text.text, and content[].text as a
        bare string under header_top_row.profile_user.
        """
        out: list[str] = []
        for _k, v in self._entity_kv({"profile_social_context"}):
            if not isinstance(v, dict):
                continue
            for item in v.get("content") or []:
                t = item.get("text") if isinstance(item, dict) else None
                if isinstance(t, dict):
                    t = t.get("text")
                if isinstance(t, str) and t.strip() and t.strip() not in out:
                    out.append(t.strip())
        return out

    def ent_ints(self, keys) -> list[int]:
        out = []
        for _k, v in self._entity_kv(set(keys)):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(int(v))
            elif isinstance(v, str) and v.isdigit():
                out.append(int(v))
        return out

    def ent_strs(self, keys) -> list[str]:
        return [
            v.strip()
            for _k, v in self._entity_kv(set(keys))
            if isinstance(v, str) and v.strip()
        ]

    def _entity_kv(self, want: set[str]) -> Iterator[tuple[str, Any]]:
        for d in self.ents:
            for k, v in iter_kv(d):
                if k in want:
                    yield k, v

    # ---------- unscoped fallbacks ----------

    def gql_ints(self, keys) -> list[int]:
        want, out = set(keys), []
        for blob in self.gql:
            for k, v in iter_kv(blob):
                if k not in want or isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    out.append(int(v))
                elif isinstance(v, str) and v.isdigit():
                    out.append(int(v))
        return out

    def gql_strs(self, keys) -> list[str]:
        want, out = set(keys), []
        for blob in self.gql:
            for k, v in iter_kv(blob):
                if k in want and isinstance(v, str) and v.strip():
                    out.append(v.strip())
        return out

    def all_html(self) -> str:
        return "\n".join(self.html.values())

    def all_text(self) -> str:
        return "\n".join(self.text.values())
