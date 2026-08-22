/**
 * The threat-keyword generator produces CHILDREN, not parents.
 *
 * THE DEFECT THIS GUARDS
 *   "Suggest Threat Keywords" builds permutations from a client's real
 *   names -- "official_gautam_adani", "gautam_adani_crypto" -- and used to
 *   add each one as a new PARENT. Parents are the names results are scored
 *   and filed against, so every hit found through a permutation was filed
 *   under the permutation instead of under "Gautam Adani", and the
 *   permutation itself became a filter target that no genuine profile
 *   would ever match.
 *
 *   That is precisely the regression the parent/child split was built to
 *   prevent (backend/tests_unit/test_keyword_groups.py holds the scoring
 *   half of it).
 */

import { describe, expect, it } from "vitest";
import type { KeywordGroup } from "../api/types";
import { mergeGeneratedChildren } from "./keywordGroups";

const GROUPS: KeywordGroup[] = [
  { parent: "Gautam Adani", children: ["gautamadani"] },
  { parent: "Pranav Adani", children: [] },
];

describe("variations land under their parent", () => {
  it("appends to the parent they were generated from", () => {
    const out = mergeGeneratedChildren(GROUPS, {
      "Gautam Adani": ["official_gautam_adani", "gautam_adani_crypto"],
    });
    expect(out[0]).toEqual({
      parent: "Gautam Adani",
      children: ["gautamadani", "official_gautam_adani", "gautam_adani_crypto"],
    });
  });

  it("never creates a parent out of a variation", () => {
    const out = mergeGeneratedChildren(GROUPS, {
      "Gautam Adani": ["official_gautam_adani"],
    });
    expect(out.map((g) => g.parent)).toEqual(["Gautam Adani", "Pranav Adani"]);
  });

  it("leaves untouched parents alone", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Gautam Adani": ["x"] });
    expect(out[1]).toBe(GROUPS[1]);
  });

  it("adds to a parent that had no children yet", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Pranav Adani": ["pranav_official"] });
    expect(out[1].children).toEqual(["pranav_official"]);
  });

  it("fills several parents in one apply", () => {
    const out = mergeGeneratedChildren(GROUPS, {
      "Gautam Adani": ["a"],
      "Pranav Adani": ["b"],
    });
    expect(out[0].children).toEqual(["gautamadani", "a"]);
    expect(out[1].children).toEqual(["b"]);
  });
});

describe("nothing is swept twice", () => {
  it("skips a variation the group already has", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Gautam Adani": ["gautamadani"] });
    expect(out[0].children).toEqual(["gautamadani"]);
    expect(out[0]).toBe(GROUPS[0]);
  });

  it("matches existing children case-insensitively", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Gautam Adani": ["GautamAdani"] });
    expect(out[0].children).toEqual(["gautamadani"]);
  });

  it("drops a variation equal to its own parent", () => {
    // A childless parent already searches its own name.
    const out = mergeGeneratedChildren(GROUPS, { "Pranav Adani": ["pranav adani", "real_pranav"] });
    expect(out[1].children).toEqual(["real_pranav"]);
  });

  it("collapses duplicates inside one batch", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Pranav Adani": ["dup", "DUP", " dup "] });
    expect(out[1].children).toEqual(["dup"]);
  });
});

describe("edges", () => {
  it("an empty batch changes nothing", () => {
    expect(mergeGeneratedChildren(GROUPS, {})).toEqual(GROUPS);
  });

  it("ignores blank variations", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Pranav Adani": ["", "   ", "ok"] });
    expect(out[1].children).toEqual(["ok"]);
  });

  it("keeps a parent deleted while the modal was open rather than dropping its terms", () => {
    const out = mergeGeneratedChildren(GROUPS, { "Deleted Name": ["a", "b"] });
    expect(out).toHaveLength(3);
    expect(out[2]).toEqual({ parent: "Deleted Name", children: ["a", "b"] });
  });

  it("does not mutate the groups it was given", () => {
    const before = JSON.parse(JSON.stringify(GROUPS));
    mergeGeneratedChildren(GROUPS, { "Gautam Adani": ["new_one"] });
    expect(GROUPS).toEqual(before);
  });

  it("starting from no groups at all still files under parents", () => {
    const out = mergeGeneratedChildren([], { Acme: ["acme_support"] });
    expect(out).toEqual([{ parent: "Acme", children: ["acme_support"] }]);
  });
});
