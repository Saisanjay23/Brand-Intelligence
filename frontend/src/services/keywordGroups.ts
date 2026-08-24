/**
 * Merging generated search variations into a client's keyword groups.
 *
 * Parents are what results are MATCHED and FILED against; children are the
 * terms actually SEARCHED (backend/shared/keywords.py holds the other half
 * of this contract). The threat-keyword generator produces permutations
 * like "official_gautam_adani" -- search terms, not names anything is
 * called -- so they belong to their parent as children. Adding them as
 * parents instead, which the generator used to do, made each permutation
 * its own filter target and filed hits under the permutation rather than
 * under the real name.
 *
 * Kept here rather than inline in HomeView.tsx so the merge rules are
 * testable without rendering the page (see keywordGroups.test.ts).
 */

import type { KeywordGroup } from "../api/types";

/** Variations to attach, keyed by the parent they were generated from. */
export type GeneratedByParent = Record<string, string[]>;

/**
 * `groups` with each parent's generated variations appended as children.
 *
 * - a variation the group already has is not added twice (case-insensitive)
 * - a variation equal to its own parent is dropped: a childless parent
 *   already searches its own name, so keeping it as a child too would
 *   sweep the same term twice
 * - duplicates within one batch collapse
 * - a parent in `byParent` that is no longer in `groups` (deleted while
 *   the modal was open) is appended rather than silently discarded
 * - groups with nothing to add keep their identity, so React sees an
 *   unchanged reference
 */
export function mergeGeneratedChildren(
  groups: KeywordGroup[],
  byParent: GeneratedByParent,
): KeywordGroup[] {
  const merged = groups.map((g) => {
    const additions = byParent[g.parent];
    if (!additions?.length) return g;
    const seen = new Set(g.children.map((c) => c.toLowerCase()));
    seen.add(g.parent.toLowerCase());
    const fresh: string[] = [];
    for (const raw of additions) {
      const child = raw.trim();
      if (!child) continue;
      const key = child.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      fresh.push(child);
    }
    return fresh.length ? { ...g, children: [...g.children, ...fresh] } : g;
  });

  const known = new Set(groups.map((g) => g.parent));
  for (const [parent, children] of Object.entries(byParent)) {
    if (known.has(parent)) continue;
    const seen = new Set<string>([parent.toLowerCase()]);
    const fresh: string[] = [];
    for (const raw of children) {
      const child = raw.trim();
      if (!child) continue;
      const key = child.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      fresh.push(child);
    }
    merged.push({ parent, children: fresh });
  }
  return merged;
}

/**
 * Parses raw multiline or comma-separated bulk keyword group text.
 * Supports:
 *   - Simple parent lists (newline or comma-separated): "Gautam Adani\nKaran Adani" or "Gautam Adani, Karan Adani"
 *   - Parent with search terms: "Gautam Adani: official_gautam, real_gautam" or "Gautam Adani -> official_gautam"
 */
export function parseBulkKeywordGroups(raw: string): KeywordGroup[] {
  const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const result: KeywordGroup[] = [];

  for (const line of lines) {
    // Check if line contains parent -> children or parent: children
    const sepMatch = line.match(/^([^:=>\-]+)(?::|->|=>)(.+)$/);
    if (sepMatch) {
      const parent = sepMatch[1].trim();
      const rawChildren = sepMatch[2];
      const children = rawChildren
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (parent) {
        result.push({ parent, children });
      }
    } else {
      // If no separator, line might contain comma-separated parent names
      const items = line
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      for (const item of items) {
        if (item) {
          result.push({ parent: item, children: [] });
        }
      }
    }
  }

  return result;
}

/**
 * Merges a list of parsed incoming KeywordGroups into an existing list of groups:
 * - Appends new parent groups
 * - Merges any new search term children into matching existing parents (case-insensitive)
 * - Returns updated list along with addedCount and dupCount
 */
export function mergeBulkKeywordGroups(
  existing: KeywordGroup[],
  incoming: KeywordGroup[],
): { next: KeywordGroup[]; addedCount: number; dupCount: number } {
  let updated = [...existing];
  let addedCount = 0;
  let dupCount = 0;

  for (const item of incoming) {
    const pTrimmed = item.parent.trim();
    if (!pTrimmed) continue;

    const existingIdx = updated.findIndex(
      (g) => g.parent.toLowerCase() === pTrimmed.toLowerCase(),
    );

    if (existingIdx !== -1) {
      // Parent already exists: if new child search terms are supplied, merge them
      if (item.children.length > 0) {
        const currentGroup = updated[existingIdx];
        const seen = new Set(currentGroup.children.map((c) => c.toLowerCase()));
        seen.add(currentGroup.parent.toLowerCase());
        const freshChildren: string[] = [];
        for (const raw of item.children) {
          const ch = raw.trim();
          if (!ch) continue;
          const key = ch.toLowerCase();
          if (seen.has(key)) continue;
          seen.add(key);
          freshChildren.push(ch);
        }
        if (freshChildren.length > 0) {
          updated[existingIdx] = {
            ...currentGroup,
            children: [...currentGroup.children, ...freshChildren],
          };
          addedCount++;
        } else {
          dupCount++;
        }
      } else {
        dupCount++;
      }
    } else {
      // New parent
      const seen = new Set<string>([pTrimmed.toLowerCase()]);
      const freshChildren: string[] = [];
      for (const raw of item.children) {
        const ch = raw.trim();
        if (!ch) continue;
        const key = ch.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        freshChildren.push(ch);
      }
      updated.push({
        parent: pTrimmed,
        children: freshChildren,
      });
      addedCount++;
    }
  }

  return { next: updated, addedCount, dupCount };
}
