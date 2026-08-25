"""Parent/child keyword groups: what gets SEARCHED vs what gets MATCHED.

THE PROBLEM THIS SOLVES
    A brand's real name ("Gautam Adani") is a poor search term on its own --
    an impersonator rarely registers under it verbatim. What actually finds
    them is an analyst's own generated permutations: "gautamadani",
    "gautam adani official", "adani gautam", "gautam.adani.hq", and so on.

    But those permutations are terrible things to MATCH against. Scoring a
    discovered profile's name against "gautam.adani.hq" says nothing useful
    about whether it is impersonating Gautam Adani, and filing the result
    under that permutation scatters one investigation across a dozen
    unrelated keyword buckets in the UI.

    So the two jobs are split:

        PARENT   the real name. Never searched. It is the match target
                 (name_score / name_exact_run) and the bucket every hit
                 found by any of its children is filed under.

        CHILDREN the analyst's permutations. Searched on every platform.
                 Never scored against, never stored as the hit's keyword.

    One parent's children all roll up into that one parent, so an analyst
    filtering the results grid by "Gautam Adani" sees everything all twelve
    permutations turned up, not twelve separate piles.

BACK-COMPATIBILITY IS THE LOAD-BEARING PART
    `name_keywords` / `domain_keywords` on the client document stay exactly
    what they always were: a flat list of strings. They now hold the
    PARENTS. Everything that already reads them keeps working untouched --
    `discovery_service._is_individual_keyword` (individual-vs-domain
    classification), `incident_publisher._category_and_asset_name`,
    `profile_repository.list_profiles`'s keyword_match_type bucket filter,
    and `scheduler_controller`'s "does this client have keywords at all"
    check.

    A client saved before groups existed has no `keyword_groups` field at
    all. `groups_from_flat` synthesises one group per existing keyword with
    NO children, and a childless parent searches ITSELF (see
    `build_plans`), which is precisely the old behaviour. Such a client
    sweeps identically before and after this feature, with nothing to
    migrate and no backfill step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

# The two keyword categories the whole pipeline is already split by (caps,
# incident category, asset-name choice). Groups are stored per category so a
# parent never has to be re-classified at read time.
INDIVIDUAL = "individual"
DOMAIN = "domain"
KEYWORD_TYPES = (INDIVIDUAL, DOMAIN)

# Which client field holds the flat parent list for each type. These are the
# ORIGINAL field names, deliberately unchanged -- see the module docstring on
# why every existing reader must keep working.
FLAT_FIELD = {INDIVIDUAL: "name_keywords", DOMAIN: "domain_keywords"}

# Which client field holds the asset-name overrides for each type. Those are
# additional MATCH targets alongside the parent (an analyst may protect
# "Gautam Adani" but report the asset as "Adani Group"), never search terms.
ASSET_FIELD = {
    INDIVIDUAL: "asset_name_individual_keywords",
    DOMAIN: "asset_name_domain_keywords",
}


@dataclass(frozen=True)
class MatchTarget:
    """One parent a hit could be filed under, and every string a hit's name
    is scored against for it (the parent plus that type's asset names).

    Asset names are included because they are the OTHER name the same
    entity is publicly known by -- a client protecting the person "Gautam
    Adani" whose asset name is "Adani Group" wants a profile calling itself
    "Adani Group Official" to score as a match, and scoring it against the
    person's name alone would rate it near zero.
    """

    parent: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class KeywordPlan:
    """One search this sweep will actually run, and what to do with what it
    finds.

    `search` goes into the platform's search box. `targets` is who the
    resulting hits may be filed under -- normally exactly one, but a
    permutation an analyst listed under two different parents produces a
    single search with two candidate targets, resolved per hit by
    `resolve_parent` below.
    """

    search: str
    kw_type: str
    targets: tuple[MatchTarget, ...]

    @property
    def parent(self) -> str:
        """The default/primary parent, for callers that only need a label
        (progress lines, pending-item previews). Hit-level filing goes
        through `resolve_parent`, which may pick a different target."""
        return self.targets[0].parent if self.targets else self.search


def _clean(value: Any) -> str:
    """One keyword string, trimmed. Non-strings (a malformed document, a
    stray None in a list) collapse to "" rather than raising -- a bad row in
    a client's config must never take the whole sweep down."""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _dedup(values: Iterable[str]) -> list[str]:
    """Order-preserving case-insensitive dedup. Order is preserved because
    it is the analyst's own priority ordering, and the UI renders these
    lists back in the order they were typed."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        cleaned = _clean(v)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def normalize_groups(raw: Any) -> dict[str, list[dict]]:
    """Whatever a caller sent -> the canonical
    `{"individual": [{"parent": str, "children": [str]}], "domain": [...]}`.

    Total, never raises: this runs on request bodies and on documents read
    back out of Mongo, and a malformed entry in either has to degrade to
    "that one entry is dropped", not "this client can no longer be loaded".

    A group with a blank parent is dropped entirely (its children have
    nothing to roll up into). A child equal to its own parent is dropped as
    a child, since a childless parent already searches itself and keeping
    both would search the same term twice.
    """
    out: dict[str, list[dict]] = {t: [] for t in KEYWORD_TYPES}
    if not isinstance(raw, dict):
        return out

    for kw_type in KEYWORD_TYPES:
        entries = raw.get(kw_type)
        if not isinstance(entries, list):
            continue
        seen_parents: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parent = _clean(entry.get("parent"))
            if not parent or parent.lower() in seen_parents:
                continue
            seen_parents.add(parent.lower())
            raw_children = entry.get("children")
            children = _dedup(raw_children if isinstance(raw_children, list) else [])
            children = [c for c in children if c.lower() != parent.lower()]
            out[kw_type].append({"parent": parent, "children": children})
    return out


def groups_from_flat(
    name_keywords: Optional[Iterable[str]],
    domain_keywords: Optional[Iterable[str]],
) -> dict[str, list[dict]]:
    """The synthesised groups for a client that predates this feature: one
    childless parent per existing keyword.

    A childless parent searches itself (`build_plans`), so this reproduces
    the pre-groups behaviour exactly -- which is what makes the whole
    feature a no-op for every client until someone actually adds children.
    """
    return {
        INDIVIDUAL: [{"parent": p, "children": []} for p in _dedup(name_keywords or [])],
        DOMAIN: [{"parent": p, "children": []} for p in _dedup(domain_keywords or [])],
    }


def groups_for_client(client: Optional[dict]) -> dict[str, list[dict]]:
    """The client's keyword groups, synthesising them from the flat lists
    when the document has none (see `groups_from_flat`).

    `keyword_groups` is authoritative whenever it is non-empty; the flat
    lists are only consulted for a document that predates it. A client
    saved through the current form always writes BOTH (the groups, and the
    parent list derived from them via `flat_keywords`), so the two can
    never drift apart.
    """
    client = client or {}
    groups = normalize_groups(client.get("keyword_groups"))
    if any(groups[t] for t in KEYWORD_TYPES):
        return groups
    return groups_from_flat(
        client.get("name_keywords"), client.get("domain_keywords")
    )


def parents_of(groups: dict[str, list[dict]], kw_type: str) -> list[str]:
    """The flat parent list for one type -- exactly what belongs in
    `name_keywords`/`domain_keywords`, which is how every pre-existing
    reader of those fields keeps working unchanged."""
    return [g["parent"] for g in groups.get(kw_type, [])]


def flat_keywords(groups: dict[str, list[dict]]) -> dict[str, list[str]]:
    """`{"name_keywords": [...parents], "domain_keywords": [...parents]}` --
    the derived fields `client_repository.upsert` persists alongside the
    groups themselves, so the two can never disagree."""
    return {FLAT_FIELD[t]: parents_of(groups, t) for t in KEYWORD_TYPES}


def search_terms(groups: dict[str, list[dict]], kw_type: str) -> list[str]:
    """Every string that will actually be typed into a platform's search
    box for one keyword type: the parent itself plus all of its child
    permutations. Used for previews/counts; the sweep itself wants
    `build_plans`, which also carries the match targets."""
    out: list[str] = []
    for group in groups.get(kw_type, []):
        parent = group.get("parent")
        children = group.get("children") or []
        if parent:
            out.append(parent)
        out.extend(children)
    return _dedup(out)


def match_terms_for(client: Optional[dict], parent: str, kw_type: str) -> tuple[str, ...]:
    """Every string a hit found under `parent` is scored against: the parent
    itself plus that type's configured asset names. See `MatchTarget`."""
    client = client or {}
    assets = client.get(ASSET_FIELD.get(kw_type, "")) or []
    return tuple(_dedup([parent, *assets]))


def classify_unknown(client: Optional[dict], keyword: str) -> str:
    """INDIVIDUAL or DOMAIN for a keyword the client's groups don't contain.

    Mirrors `discovery_service._is_individual_keyword` deliberately, and for
    the same reason that function mirrors
    `incident_publisher._category_and_asset_name`: a keyword has to
    classify identically wherever it is classified, or the same ad-hoc
    search picks one per-type cap during discovery and the opposite
    incident category at publish time. Kept as a small duplicated rule with
    an explicit cross-reference rather than an import, exactly as those two
    already are, since this module must stay free of service-layer imports.
    """
    client = client or {}
    individual = {
        _clean(k).lower()
        for k in (client.get(ASSET_FIELD[INDIVIDUAL]) or [])
        if _clean(k)
    }
    return INDIVIDUAL if _clean(keyword).lower() in individual else DOMAIN


def build_plans(
    client: Optional[dict],
    requested: Optional[Iterable[str]] = None,
) -> list[KeywordPlan]:
    """The searches one sweep should run, resolved from a client's groups.

    `requested` scopes the sweep to a subset of the client's PARENTS -- the
    keyword list a caller passed to `POST /discovery`, which is always
    parents (that is what the UI shows and what the round-robin engine
    reads out of `name_keywords`/`domain_keywords`). Omitted or empty means
    every parent the client has.

    A requested term that matches no parent is still honoured, as its own
    childless plan: an analyst running an ad-hoc one-off search for a term
    that isn't in the client's saved config must not silently sweep
    nothing. It is classified by `classify_unknown` below, which mirrors
    `discovery_service._is_individual_keyword` exactly (individual when it
    is one of the client's individual asset names, domain otherwise) so an
    ad-hoc keyword picks the same per-type cap and incident category it
    would have picked before this feature existed.

    Deduped by SEARCH TERM: the same permutation listed under two parents
    is one search, not two, since running the same query twice against the
    same platform costs a real page load and risks the session for nothing.
    When that happens the single plan carries BOTH parents as targets, and
    `resolve_parent` picks per hit.
    """
    groups = groups_for_client(client)
    wanted: Optional[set[str]] = None
    if requested is not None:
        cleaned = _dedup(requested)
        if cleaned:
            wanted = {c.lower() for c in cleaned}

    # search term (lowered) -> {"search", "kw_type", "targets": [MatchTarget]}
    by_search: dict[str, dict] = {}
    order: list[str] = []
    matched_parents: set[str] = set()

    for kw_type in KEYWORD_TYPES:
        for group in groups.get(kw_type, []):
            parent = group["parent"]
            if wanted is not None and parent.lower() not in wanted:
                continue
            matched_parents.add(parent.lower())
            target = MatchTarget(parent=parent, terms=match_terms_for(client, parent, kw_type))
            terms_to_search = _dedup([parent, *(group.get("children") or [])])
            for term in terms_to_search:
                key = term.lower()
                if key not in by_search:
                    by_search[key] = {"search": term, "kw_type": kw_type, "targets": [target]}
                    order.append(key)
                elif all(t.parent.lower() != parent.lower() for t in by_search[key]["targets"]):
                    by_search[key]["targets"].append(target)

    # An explicitly requested term the client's config doesn't know about
    # still gets swept, on its own, rather than vanishing.
    if wanted is not None:
        for term in _dedup(requested or []):
            if term.lower() in matched_parents or term.lower() in by_search:
                continue
            kw_type = classify_unknown(client, term)
            by_search[term.lower()] = {
                "search": term, "kw_type": kw_type,
                "targets": [MatchTarget(parent=term, terms=match_terms_for(client, term, kw_type))],
            }
            order.append(term.lower())

    return [
        KeywordPlan(
            search=by_search[k]["search"],
            kw_type=by_search[k]["kw_type"],
            targets=tuple(by_search[k]["targets"]),
        )
        for k in order
    ]


def resolve_parent(plan: KeywordPlan, name: str, scorer) -> tuple[str, int]:
    """`(parent to file this hit under, its name score)`.

    Ordinarily a plan has exactly one target and this just scores the name
    against that target's terms. The interesting case is a permutation an
    analyst listed under two different parents (see `build_plans`): the hit
    is filed under whichever parent's own terms it actually resembles,
    rather than arbitrarily under whichever group happened to be saved
    first.

    Within one target the BEST-scoring term wins but the PARENT is still
    what is returned -- an asset name is an alternate spelling of the same
    entity, not a separate bucket to file under, so only the score comes
    from the asset name.

    `scorer` is injected (rather than importing `shared.text.name_score`
    here) purely so this stays a pure function testable without pulling in
    the text-matching stack.
    """
    best_parent, best_score = plan.parent, -1
    for target in plan.targets or (MatchTarget(plan.search, (plan.search,)),):
        for term in target.terms or (target.parent,):
            try:
                score = int(scorer(name or "", term))
            except Exception:
                score = 0
            if score > best_score:
                best_parent, best_score = target.parent, score
    return best_parent, max(best_score, 0)


def match_any(plan: KeywordPlan, name: str, predicate) -> bool:
    """True when `name` satisfies `predicate` against ANY match term of any
    of this plan's targets -- the boolean counterpart to `resolve_parent`,
    used for `name_exact_run` (shared/text.py::contiguous_letters_match).

    Same reason for injecting `predicate` as `resolve_parent` injects
    `scorer`: keeps this module free of the text-matching stack.
    """
    for target in plan.targets or (MatchTarget(plan.search, (plan.search,)),):
        for term in target.terms or (target.parent,):
            try:
                if predicate(name or "", term):
                    return True
            except Exception:
                continue
    return False
