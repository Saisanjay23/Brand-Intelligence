import { useCallback, useEffect, useMemo, useState, Fragment } from "react";
import { toast } from "react-hot-toast";
import { analysisApi } from "../api/analysisApi";
import { clientsApi } from "../api/clientsApi";
import { discoveryApi } from "../api/discoveryApi";
import { jobsApi } from "../api/jobsApi";
import type { Client, Job, KeywordGroup, PlatformHealth } from "../api/types";
import {
  mergeGeneratedChildren,
  parseBulkKeywordGroups,
  mergeBulkKeywordGroups,
} from "../services/keywordGroups";
import { PlatformIcon } from "../components/PlatformIcon";
import { GlobalSearchModal } from "../components/GlobalSearchModal";
import { confirmAction } from "../utils/confirmAction";
import {
  DiscoverIcon,
  AnalyseIcon,
  CyberGlobeIcon,
  GlobeIcon,
  StopIcon,
  TargetIcon,
  UserIcon,
  TagIcon,
  SaveIcon,
  SparklesIcon,
  BuildingIcon,
  SearchIcon,
  PlusIcon,
  TrashIcon,
  EditIcon,
  CloneIcon,
  ZapIcon,
  SettingsGearIcon,
  AlertTriangleIcon,
  LayersIcon,
} from "../components/AppIcons";

type KeywordTab = "names" | "domain" | "assetNames";
type Mode = "select" | "create";
type WorkspaceTab = "overview" | "keywords" | "limits" | "settings";

type FacebookTab = "people" | "pages" | "groups";
type FacebookTabLimits = Record<FacebookTab, { individual: string; domain: string }>;

interface Props {
  clientId: string;
  clientName: string;
  platforms: PlatformHealth[];
  onClient: (clientId: string, name: string) => void;
  onForgetClient: (clientId: string) => void;
  busy: boolean;
  analysisBusy: boolean;
  onStopDiscovery?: () => void;
  onStopAnalysis?: () => void;
  stoppingDiscovery?: boolean;
  stoppingAnalysis?: boolean;
  onJobs: (jobs: Job[]) => void;
  onError: (m: string) => void;
}

function splitKeywordList(raw: string): string[] {
  return raw
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function dedupeKeywordsCaseInsensitive(keywords: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const kw of keywords) {
    const key = kw.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(kw);
  }
  return out;
}

function ChipInput({
  chips,
  onAdd,
  onRemove,
  placeholder,
  disabled,
}: {
  chips: string[];
  onAdd: (v: string) => void;
  onRemove: (i: number) => void;
  placeholder: string;
  disabled?: boolean;
}) {
  const [input, setInput] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const commit = () => {
    const trimmed = input.trim();
    if (trimmed) {
      if (chips.some((c) => c.toLowerCase() === trimmed.toLowerCase())) {
        toast(`⚠️ "${trimmed}" already exists`, { id: `dup-${trimmed.toLowerCase()}` });
      } else {
        onAdd(trimmed);
      }
      setInput("");
    }
  };

  const commitBulk = () => {
    let dupCount = 0;
    const items = splitKeywordList(bulkText);
    const seen = new Set(chips.map((c) => c.toLowerCase()));
    for (const kw of items) {
      if (seen.has(kw.toLowerCase())) {
        dupCount++;
      } else {
        seen.add(kw.toLowerCase());
        onAdd(kw);
      }
    }
    if (dupCount > 0) {
      toast(`⚠️ Skipped ${dupCount} duplicate keyword${dupCount === 1 ? "" : "s"}`);
    }
    setBulkText("");
    setBulkOpen(false);
  };

  return (
    <div>
      <div className="chips-input-container">
        {chips.map((kw, i) => (
          <span key={i} className="kw-chip">
            {kw}
            <span className="remove-chip" onClick={() => onRemove(i)}>
              ✕
            </span>
          </span>
        ))}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commit();
            }
          }}
          onPaste={(e) => {
            const text = e.clipboardData.getData("text");
            if (/[,\n]/.test(text)) {
              e.preventDefault();
              const items = splitKeywordList(text);
              const seen = new Set(chips.map((c) => c.toLowerCase()));
              let dupCount = 0;
              for (const kw of items) {
                if (seen.has(kw.toLowerCase())) {
                  dupCount++;
                } else {
                  seen.add(kw.toLowerCase());
                  onAdd(kw);
                }
              }
              if (dupCount > 0) {
                toast(`⚠️ Skipped ${dupCount} duplicate keyword${dupCount === 1 ? "" : "s"}`);
              }
            }
          }}
          onBlur={commit}
          placeholder={placeholder}
          className="chip-input"
          disabled={disabled}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "6px" }}>
        <div className="kw-count-badge" style={{ margin: 0 }}>
          <strong>{chips.length}</strong> keyword{chips.length === 1 ? "" : "s"} configured
        </div>
        <button
          type="button"
          className="bulk-kw-toggle"
          onClick={() => setBulkOpen((v) => !v)}
          disabled={disabled}
        >
          {bulkOpen ? "▾ Close bulk paste" : (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
              <CloneIcon size={12} /> Bulk import
            </span>
          )}
        </button>
      </div>
      {bulkOpen && (
        <div className="bulk-kw-panel">
          <textarea
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"one per line, or comma-separated -- e.g.\ngautam adani\nkaran adani, jeet adani"}
            rows={3}
            disabled={disabled}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "4px" }}>
            <button
              type="button"
              className="btn-cyber-primary"
              style={{ width: "auto", padding: "6px 14px", fontSize: "11.5px", marginTop: 0 }}
              onClick={commitBulk}
              disabled={disabled || !bulkText.trim()}
            >
              Add Keywords
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Domain Asset Names input: a platform picker + text input that stores
 *  entries as `"platform::AssetName"`. Chips render with a platform badge.
 *  "All Platforms" stores without a prefix (legacy-compatible). */
function DomainAssetPlatformInput({
  chips,
  onAdd,
  onRemove,
  platforms,
  disabled,
}: {
  chips: string[];
  onAdd: (v: string) => void;
  onRemove: (i: number) => void;
  platforms: PlatformHealth[];
  disabled?: boolean;
}) {
  const [input, setInput] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const platformNames: Record<string, string> = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of platforms) map[p.platform] = p.name;
    return map;
  }, [platforms]);

  const encode = (name: string): string => {
    if (!selectedPlatform) return name; // all platforms
    return `${selectedPlatform}::${name}`;
  };

  const isDuplicate = (encoded: string): boolean =>
    chips.some((c) => c.toLowerCase() === encoded.toLowerCase());

  const commit = () => {
    const trimmed = input.trim();
    if (trimmed) {
      const encoded = encode(trimmed);
      if (isDuplicate(encoded)) {
        toast(`⚠️ "${trimmed}" already exists for this platform`, { id: `dup-da-${encoded.toLowerCase()}` });
      } else {
        onAdd(encoded);
      }
      setInput("");
    }
  };

  const commitBulk = () => {
    let dupCount = 0;
    const items = splitKeywordList(bulkText);
    const seen = new Set(chips.map((c) => c.toLowerCase()));
    for (const kw of items) {
      const encoded = encode(kw);
      if (seen.has(encoded.toLowerCase())) {
        dupCount++;
      } else {
        seen.add(encoded.toLowerCase());
        onAdd(encoded);
      }
    }
    if (dupCount > 0) {
      toast(`⚠️ Skipped ${dupCount} duplicate${dupCount === 1 ? "" : "s"}`);
    }
    setBulkText("");
    setBulkOpen(false);
  };

  return (
    <div>
      <div className="chips-input-container" style={{ minHeight: "60px", alignItems: "center", alignContent: "flex-start" }}>
        {chips.map((raw, i) => {
          const { platform, name } = parseDomainAssetEntry(raw);
          return (
            <span key={i} className="kw-chip" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
              {platform && (
                <span style={{
                  background: "var(--purple)", color: "#fff",
                  borderRadius: "3px", padding: "1px 5px", fontSize: "9.5px",
                  fontWeight: 600, letterSpacing: "0.3px", textTransform: "uppercase",
                  lineHeight: "14px", flexShrink: 0,
                }}>
                  {platformNames[platform] || platform}
                </span>
              )}
              {name}
              <span className="remove-chip" onClick={() => onRemove(i)}>✕</span>
            </span>
          );
        })}
        
        <div style={{ display: "flex", flex: 1, minWidth: "250px", alignItems: "center" }}>
          <select
            value={selectedPlatform}
            onChange={(e) => setSelectedPlatform(e.target.value)}
            disabled={disabled}
            style={{
              background: "transparent", color: selectedPlatform ? "var(--purple)" : "var(--text-dim)",
              border: "none", outline: "none", fontSize: "13px", fontWeight: selectedPlatform ? 600 : 400,
              cursor: "pointer", padding: "4px 2px", marginRight: "6px", fontFamily: "inherit"
            }}
            title="Select platform to tag this asset name with"
          >
            <option style={{ background: "var(--bg-surface)", color: "var(--text-main)", fontWeight: "normal" }} value="">
              [All Platforms]
            </option>
            {platforms.map((p) => (
              <option style={{ background: "var(--bg-surface)", color: "var(--text-main)", fontWeight: "normal" }} key={p.platform} value={p.platform}>
                [{p.name}]
              </option>
            ))}
          </select>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                commit();
              }
            }}
            onPaste={(e) => {
              const text = e.clipboardData.getData("text");
              if (/[,\n]/.test(text)) {
                e.preventDefault();
                const items = splitKeywordList(text);
                const seen = new Set(chips.map((c) => c.toLowerCase()));
                let dupCount = 0;
                for (const kw of items) {
                  const encoded = encode(kw);
                  if (seen.has(encoded.toLowerCase())) {
                    dupCount++;
                  } else {
                    seen.add(encoded.toLowerCase());
                    onAdd(encoded);
                  }
                }
                if (dupCount > 0) {
                  toast(`⚠️ Skipped ${dupCount} duplicate${dupCount === 1 ? "" : "s"}`);
                }
              }
            }}
            onBlur={commit}
            placeholder="Type asset name here…"
            className="chip-input"
            disabled={disabled}
            style={{ flex: 1, minWidth: "150px" }}
          />
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "6px" }}>
        <div className="kw-count-badge" style={{ margin: 0 }}>
          <strong>{chips.length}</strong> asset name{chips.length === 1 ? "" : "s"} configured
        </div>
        <button
          type="button"
          className="bulk-kw-toggle"
          onClick={() => setBulkOpen((v) => !v)}
          disabled={disabled}
        >
          {bulkOpen ? "▾ Close bulk paste" : (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
              <CloneIcon size={12} /> Bulk import
            </span>
          )}
        </button>
      </div>
      {bulkOpen && (
        <div className="bulk-kw-panel">
          <div style={{ fontSize: "11px", color: "var(--text-dim)", marginBottom: "4px" }}>
            Asset names will be tagged with the platform selected above ({selectedPlatform ? (platformNames[selectedPlatform] || selectedPlatform) : "All Platforms"}).
          </div>
          <textarea
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"one per line, or comma-separated — e.g.\nAcme Group\nAcme Holdings, Acme Brand"}
            rows={3}
            disabled={disabled}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "4px" }}>
            <button
              type="button"
              className="btn-cyber-primary"
              style={{ width: "auto", padding: "6px 14px", fontSize: "11.5px", marginTop: 0 }}
              onClick={commitBulk}
              disabled={disabled || !bulkText.trim()}
            >
              Add Asset Names
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function KeywordGeneratorModal({
  nameKeywords,
  domainKeywords,
  onAddKeywords,
  onClose,
}: {
  nameKeywords: string[];
  domainKeywords: string[];
  // Keyed by the PARENT each variation was generated from -> the variations
  // to attach to it as children.
  onAddKeywords: (type: "names" | "domain", byParent: Record<string, string[]>) => void;
  onClose: () => void;
}) {
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set());

  const suggestions = useMemo(() => {
    // Every variation remembers the `parent` it was derived from, because
    // that is where it has to be FILED. A generated permutation is a search
    // term, never a thing to match against: "official_gautam_adani" is not
    // a name any real profile is called, so adding it as its own parent
    // (which this modal used to do) meant hits found through it were scored
    // and grouped under the permutation instead of under "Gautam Adani".
    // See backend/shared/keywords.py -- children are searched, parents are
    // matched -- and the regression documented in
    // tests_unit/test_keyword_groups.py::TestScoringUsesTheParentNotTheSearchTerm.
    const list: { type: "names" | "domain"; parent: string; kw: string; pattern: string }[] = [];
    const namePrefixes = ["official_", "real_", "the_real_"];
    const nameSuffixes = ["_official", "_real", "_vip", "_direct", "_fanpage", "_investment", "_crypto"];
    const domainPrefixes = ["official_", "support_", "help_"];
    const domainSuffixes = ["_support", "_helpdesk", "_careers", "_jobs", "_fund", "_finance", "_promo", "_giveaway", "_official", "_app", "_service"];

    nameKeywords.forEach((name) => {
      const clean = name.toLowerCase().replace(/\s+/g, "_");
      namePrefixes.forEach((pre) => list.push({ type: "names", parent: name, kw: `${pre}${clean}`, pattern: "Prefix Impersonation" }));
      nameSuffixes.forEach((suf) => list.push({ type: "names", parent: name, kw: `${clean}${suf}`, pattern: "Suffix Impersonation" }));
    });

    domainKeywords.forEach((dom) => {
      const clean = dom.toLowerCase().replace(/\s+/g, "_");
      domainPrefixes.forEach((pre) => list.push({ type: "domain", parent: dom, kw: `${pre}${clean}`, pattern: "Customer Support Lure" }));
      domainSuffixes.forEach((suf) => list.push({ type: "domain", parent: dom, kw: `${clean}${suf}`, pattern: "Scam / Giveaway / Job Lure" }));
    });

    return list;
  }, [nameKeywords, domainKeywords]);

  // Selection is keyed by type+parent+term, not by the term alone: the same
  // permutation can legitimately be generated for two different parents
  // (an individual "Adani" and a domain "Adani" both yield
  // "official_adani"), and keying on the bare term would tie those two
  // checkboxes together and file the variation under both.
  const idOf = (s: { type: string; parent: string; kw: string }) => `${s.type}:${s.parent}:${s.kw}`;

  const toggleSelect = (id: string) => {
    setSelectedSuggestions((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (selectedSuggestions.size === suggestions.length) {
      setSelectedSuggestions(new Set());
    } else {
      setSelectedSuggestions(new Set(suggestions.map(idOf)));
    }
  };

  const handleApply = () => {
    // Grouped by parent so each variation is added as a CHILD of the name
    // it was built from, which is what makes it a search term that still
    // scores and files against the real name.
    const names: Record<string, string[]> = {};
    const domains: Record<string, string[]> = {};
    suggestions.forEach((s) => {
      if (!selectedSuggestions.has(idOf(s))) return;
      const bucket = s.type === "names" ? names : domains;
      (bucket[s.parent] ||= []).push(s.kw);
    });
    if (Object.keys(names).length) onAddKeywords("names", names);
    if (Object.keys(domains).length) onAddKeywords("domain", domains);
    toast.success(`Added ${selectedSuggestions.size} search variations!`, { icon: "✨" });
    onClose();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(8,15,30,0.8)",
        backdropFilter: "blur(8px)",
        zIndex: 10000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "20px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="dashboard-card-box"
        style={{ width: "min(620px, 100%)", background: "var(--bg-card)" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "10px" }}>
          <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
            <SparklesIcon size={16} color="var(--cyan)" />
            <span>Threat Actor Keyword Generator</span>
          </div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--text-muted)", fontSize: "16px", cursor: "pointer" }}>✕</button>
        </div>

        <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "12px" }}>
          Automatically generates common impersonation, scam, fake support, and typo-squatting variations from your configured names and domains.
        </div>

        {!suggestions.length ? (
          <div style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)" }}>
            Please add at least one Individual Name or Domain Keyword first.
          </div>
        ) : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
                Generated <strong>{suggestions.length}</strong> variations
              </span>
              <button
                type="button"
                onClick={selectAll}
                style={{ background: "none", border: "none", color: "var(--cyan)", fontSize: "11px", cursor: "pointer", textDecoration: "underline" }}
              >
                {selectedSuggestions.size === suggestions.length ? "Deselect All" : "Select All"}
              </button>
            </div>
            <div style={{ maxHeight: "260px", overflowY: "auto", border: "1px solid var(--border-subtle)", borderRadius: "8px", padding: "6px" }}>
              {suggestions.map((s) => (
                <label
                  key={idOf(s)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "6px 10px",
                    borderRadius: "6px",
                    cursor: "pointer",
                    background: selectedSuggestions.has(idOf(s)) ? "rgba(0, 229, 255, 0.08)" : "transparent",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <input
                      type="checkbox"
                      checked={selectedSuggestions.has(idOf(s))}
                      onChange={() => toggleSelect(idOf(s))}
                    />
                    <span style={{ fontSize: "12px", fontFamily: "var(--font-mono)", color: "var(--text-main)" }}>{s.kw}</span>
                    {/* Which parent this will be filed under -- the whole
                        point of the change, so it should be visible before
                        the analyst commits to it. */}
                    <span style={{ fontSize: "10px", color: "var(--text-dim)" }}>→ {s.parent}</span>
                  </div>
                  <span style={{ fontSize: "10px", color: "var(--text-dim)", background: "var(--bg-inner)", padding: "2px 6px", borderRadius: "4px", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    {s.type === "names" ? <UserIcon size={12} color="var(--cyan)" /> : <TagIcon size={12} color="var(--purple)" />}
                    {s.pattern}
                  </span>
                </label>
              ))}
            </div>
          </>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "16px" }}>
          <button type="button" onClick={onClose} className="action-btn" style={{ fontSize: "12px" }}>
            Cancel
          </button>
          <button
            type="button"
            onClick={handleApply}
            disabled={!selectedSuggestions.size}
            className="btn-cyber-primary"
            style={{ width: "auto", padding: "7px 18px", fontSize: "12px", marginTop: 0, display: "inline-flex", alignItems: "center", gap: "6px" }}
          >
            <PlusIcon size={14} /> Add {selectedSuggestions.size} Selected Keywords
          </button>
        </div>
      </div>
    </div>
  );
}

// Parent/child keyword editor.
//
// The distinction it exists to express (see backend/shared/keywords.py):
//   PARENT   the real name being protected. NEVER searched. It is what
//            discovered profiles are scored against and the bucket they are
//            filed under, so filtering results by it shows everything all of
//            its children turned up.
//   CHILDREN the analyst's own permutations. These ARE what gets searched on
//            every platform, and are never scored against.
//
// A parent with no children searches itself, which is what every client did
// before this existed -- so an analyst who adds a parent and stops is in
// exactly the old behaviour, not a broken half-state. The row says so
// explicitly rather than leaving that to be guessed.
function KeywordGroupEditor({
  groups,
  onChange,
  parentPlaceholder,
  childPlaceholder,
  accent,
  disabled,
}: {
  groups: KeywordGroup[];
  onChange: (next: KeywordGroup[]) => void;
  parentPlaceholder: string;
  childPlaceholder: string;
  accent: string;
  disabled?: boolean;
}) {
  const [parentInput, setParentInput] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const applyBulkText = (raw: string) => {
    const parsed = parseBulkKeywordGroups(raw);
    if (!parsed.length) return;
    const { next, addedCount, dupCount } = mergeBulkKeywordGroups(groups, parsed);
    onChange(next);
    if (dupCount > 0) {
      toast(`⚠️ Skipped ${dupCount} duplicate item${dupCount === 1 ? "" : "s"}`);
    }
    if (addedCount > 0) {
      toast.success(`Added ${addedCount} keyword group${addedCount === 1 ? "" : "s"}`);
    }
  };

  const addParent = () => {
    const trimmed = parentInput.trim().replace(/^,+|,+$/g, "");
    if (!trimmed) return;
    if (/[,\n]/.test(trimmed) || trimmed.includes(":") || trimmed.includes("->")) {
      applyBulkText(trimmed);
      setParentInput("");
      return;
    }
    if (groups.some((g) => g.parent.toLowerCase() === trimmed.toLowerCase())) {
      toast(`⚠️ "${trimmed}" already exists`, { id: `dup-parent-${trimmed.toLowerCase()}` });
      setParentInput("");
      return;
    }
    onChange([...groups, { parent: trimmed, children: [] }]);
    setParentInput("");
  };

  const commitBulk = () => {
    applyBulkText(bulkText);
    setBulkText("");
    setBulkOpen(false);
  };

  const removeParent = (idx: number) =>
    onChange(groups.filter((_, i) => i !== idx));

  const setChildren = (idx: number, children: string[]) =>
    onChange(groups.map((g, i) => (i === idx ? { ...g, children } : g)));

  const totalSearchTerms = groups.reduce(
    (n, g) => n + (g.children.length ? g.children.length + 1 : 1), 0,
  );

  return (
    <div>
      <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "10px", lineHeight: 1.5 }}>
        The <strong style={{ color: accent }}>parent</strong> is the primary target name — it is searched directly
        and serves as the anchor results are matched against and grouped under. Its{" "}
        <strong style={{ color: accent }}>search terms</strong> are the additional permutations and variations also
        searched on every platform.
      </div>

      <div className="chips-input-container" style={{ marginBottom: "6px" }}>
        <input
          value={parentInput}
          onChange={(e) => setParentInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addParent();
            }
          }}
          onPaste={(e) => {
            const text = e.clipboardData.getData("text");
            if (/[,\n]/.test(text) || text.includes(":") || text.includes("->")) {
              e.preventDefault();
              applyBulkText(text);
              setParentInput("");
            }
          }}
          onBlur={addParent}
          placeholder={parentPlaceholder}
          disabled={disabled}
          style={{ flex: 1, minWidth: "220px", background: "transparent", border: "none", outline: "none", color: "var(--text-main)", fontSize: "13px" }}
        />
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <div className="kw-count-badge" style={{ margin: 0 }}>
          <strong>{groups.length}</strong> configured · <strong style={{ color: accent }}>{totalSearchTerms}</strong> search{totalSearchTerms === 1 ? "" : "es"} per platform
        </div>
        <button
          type="button"
          className="bulk-kw-toggle"
          onClick={() => setBulkOpen((v) => !v)}
          disabled={disabled}
        >
          {bulkOpen ? "▾ Close bulk paste" : (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
              <CloneIcon size={12} /> Bulk import
            </span>
          )}
        </button>
      </div>

      {bulkOpen && (
        <div className="bulk-kw-panel" style={{ marginBottom: "14px" }}>
          <textarea
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"one per line, or comma-separated -- e.g.\ngautam adani\nkaran adani, jeet adani\n\nOptional search terms:\nAdani Group: adani_group, official_adani"}
            rows={4}
            disabled={disabled}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "4px" }}>
            <button
              type="button"
              className="btn-cyber-primary"
              style={{ width: "auto", padding: "6px 14px", fontSize: "11.5px", marginTop: 0 }}
              onClick={commitBulk}
              disabled={disabled || !bulkText.trim()}
            >
              Add Keywords
            </button>
          </div>
        </div>
      )}

      {!groups.length ? (
        <div style={{ padding: "18px", textAlign: "center", fontSize: "12px", color: "var(--text-dim)", border: "1px dashed var(--border-subtle)", borderRadius: "10px" }}>
          No names configured yet. Add one above to get started.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {groups.map((g, idx) => (
            <div
              key={`${g.parent}-${idx}`}
              style={{
                border: "1px solid var(--border-subtle)",
                borderLeft: `3px solid ${accent}`,
                borderRadius: "10px",
                padding: "10px 12px",
                background: "var(--bg-inner, rgba(255,255,255,0.02))",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", marginBottom: "8px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
                  <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--text-main)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {g.parent}
                  </span>
                  <span style={{ fontSize: "10px", fontWeight: 600, color: accent, background: "rgba(255,255,255,0.05)", padding: "2px 7px", borderRadius: "20px", whiteSpace: "nowrap" }}>
                    {g.children.length
                      ? `${g.children.length + 1} search terms (${g.children.length} variation${g.children.length === 1 ? "" : "s"})`
                      : "searches itself"}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => removeParent(idx)}
                  disabled={disabled}
                  title="Remove this name and all its search terms"
                  style={{ background: "transparent", border: "none", color: "var(--danger, #e95053)", cursor: "pointer", fontSize: "13px", padding: "2px 4px", flexShrink: 0 }}
                >
                  ✕
                </button>
              </div>
              <ChipInput
                chips={g.children}
                onAdd={(v) => setChildren(idx, [...g.children, v])}
                onRemove={(i) => setChildren(idx, g.children.filter((_, j) => j !== i))}
                placeholder={childPlaceholder}
                disabled={disabled}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}



/** Parse a stored domain asset-name entry. Entries with a `platform::`
 *  prefix return `{ platform, name }`; legacy entries (no prefix) return
 *  `{ platform: "", name: raw }` meaning "all platforms". */
function parseDomainAssetEntry(raw: string): { platform: string; name: string } {
  const idx = raw.indexOf("::");
  if (idx >= 0) return { platform: raw.slice(0, idx).trim(), name: raw.slice(idx + 2).trim() };
  return { platform: "", name: raw.trim() };
}

/** Render a human-friendly label for a stored domain asset-name chip. */
function domainAssetChipLabel(raw: string, platformNames: Record<string, string>): string {
  const { platform, name } = parseDomainAssetEntry(raw);
  if (!platform) return name;
  return `${name} (${platformNames[platform] || platform})`;
}

function KeywordTabs({
  activeTab,
  onTab,
  nameKeywords,
  domainKeywords,
  nameGroups,
  domainGroups,
  onNameGroups,
  onDomainGroups,
  assetNameIndividualKw,
  assetNameDomainKw,
  onAddAssetIndividual,
  onRemoveAssetIndividual,
  onAddAssetDomain,
  onRemoveAssetDomain,
  platforms,
  disabled,
}: {
  activeTab: KeywordTab;
  onTab: (t: KeywordTab) => void;
  // Derived parent lists -- the tab counts, and the source the
  // threat-keyword generator builds its permutations FROM (it files each
  // one back under the parent it came from, as a child).
  nameKeywords: string[];
  domainKeywords: string[];
  nameGroups: KeywordGroup[];
  domainGroups: KeywordGroup[];
  onNameGroups: (next: KeywordGroup[]) => void;
  onDomainGroups: (next: KeywordGroup[]) => void;
  assetNameIndividualKw: string[];
  assetNameDomainKw: string[];
  onAddAssetIndividual: (kw: string) => void;
  onRemoveAssetIndividual: (i: number) => void;
  onAddAssetDomain: (kw: string) => void;
  onRemoveAssetDomain: (i: number) => void;
  platforms: PlatformHealth[];
  disabled?: boolean;
}) {
  const [genOpen, setGenOpen] = useState(false);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <div className="kw-tab-row" style={{ margin: 0 }}>
          <button className={`kw-tab-btn ${activeTab === "names" ? "active" : ""}`} onClick={() => onTab("names")}>
            <UserIcon size={14} style={{ marginRight: "6px" }} />
            Individual Names
            {nameKeywords.length > 0 && <span className="kw-tab-count">{nameKeywords.length}</span>}
          </button>
          <button className={`kw-tab-btn ${activeTab === "domain" ? "active" : ""}`} onClick={() => onTab("domain")}>
            <TagIcon size={14} style={{ marginRight: "6px" }} />
            Domain Keywords
            {domainKeywords.length > 0 && <span className="kw-tab-count">{domainKeywords.length}</span>}
          </button>
          <button className={`kw-tab-btn ${activeTab === "assetNames" ? "active" : ""}`} onClick={() => onTab("assetNames")}>
            <LayersIcon size={14} style={{ marginRight: "6px" }} />
            Asset Names
            {(assetNameIndividualKw.length + assetNameDomainKw.length) > 0 && (
              <span className="kw-tab-count">{assetNameIndividualKw.length + assetNameDomainKw.length}</span>
            )}
          </button>
        </div>

        <button
          type="button"
          onClick={() => setGenOpen(true)}
          disabled={disabled || (!nameKeywords.length && !domainKeywords.length)}
          style={{
            background: "linear-gradient(135deg, rgba(0, 229, 255, 0.15), rgba(136, 56, 221, 0.15))",
            border: "1px solid rgba(0, 229, 255, 0.4)",
            color: "var(--cyan, #00E5FF)",
            padding: "6px 12px",
            borderRadius: "8px",
            fontSize: "11.5px",
            fontWeight: 600,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: "6px",
          }}
          title="Auto-generate threat actor and fake support keyword permutations"
        >
          <SparklesIcon size={14} /> Suggest Threat Keywords
        </button>
      </div>

      <div style={{ display: activeTab === "names" ? "block" : "none" }}>
        <KeywordGroupEditor
          groups={nameGroups}
          onChange={onNameGroups}
          parentPlaceholder="Type an executive/individual name and press Enter…"
          childPlaceholder="Add a search term for this name and press Enter…"
          accent="var(--cyan, #00E5FF)"
          disabled={disabled}
        />
      </div>

      <div style={{ display: activeTab === "domain" ? "block" : "none" }}>
        <KeywordGroupEditor
          groups={domainGroups}
          onChange={onDomainGroups}
          parentPlaceholder="Type a brand/product keyword and press Enter…"
          childPlaceholder="Add a search term for this keyword and press Enter…"
          accent="var(--purple, #8838DD)"
          disabled={disabled}
        />
      </div>

      <div style={{ display: activeTab === "assetNames" ? "block" : "none" }}>
        <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "10px" }}>
          Target asset name overrides mapped for the Analysis & Incident Reporting views.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <div>
            <label className="field-label" style={{ marginBottom: "6px", display: "flex", alignItems: "center", gap: "6px" }}>
              <UserIcon size={13} color="var(--cyan)" /> Individual Asset Names
            </label>
            <ChipInput
              chips={assetNameIndividualKw}
              onAdd={onAddAssetIndividual}
              onRemove={onRemoveAssetIndividual}
              placeholder="Asset name for individuals…"
              disabled={disabled}
            />
          </div>
          <div>
            <label className="field-label" style={{ marginBottom: "6px", display: "flex", alignItems: "center", gap: "6px" }}>
              <GlobeIcon size={13} color="var(--purple)" /> Domain Asset Names
            </label>
            <DomainAssetPlatformInput
              chips={assetNameDomainKw}
              onAdd={onAddAssetDomain}
              onRemove={onRemoveAssetDomain}
              platforms={platforms}
              disabled={disabled}
            />
          </div>
        </div>
      </div>

      {genOpen && (
        <KeywordGeneratorModal
          nameKeywords={nameKeywords}
          domainKeywords={domainKeywords}
          onAddKeywords={(type, byParent) => {
            // Attach the variations as CHILDREN of the parents they were
            // generated from, never as new parents (see
            // services/keywordGroups.ts for why, and for the merge rules).
            if (type === "names") onNameGroups(mergeGeneratedChildren(nameGroups, byParent));
            else onDomainGroups(mergeGeneratedChildren(domainGroups, byParent));
          }}
          onClose={() => setGenOpen(false)}
        />
      )}
    </div>
  );
}

function PlatformLimitsEditor({
  platforms,
  individualLimits,
  domainLimits,
  onIndividualChange,
  onDomainChange,
  facebookTabLimits,
  onFacebookTabChange,
  disabled,
}: {
  platforms: PlatformHealth[];
  individualLimits: Record<string, string>;
  domainLimits: Record<string, string>;
  onIndividualChange: (platform: string, value: string) => void;
  onDomainChange: (platform: string, value: string) => void;
  facebookTabLimits: FacebookTabLimits;
  onFacebookTabChange: (tab: FacebookTab, kwType: "individual" | "domain", value: string) => void;
  disabled?: boolean;
}) {
  const [fbExpanded, setFbExpanded] = useState(false);

  return (
    <div className="platform-limits-table-card">
      <div>
        <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main)", display: "flex", alignItems: "center", gap: "8px" }}>
          <TargetIcon size={18} color="var(--cyan)" />
          <span>Per-Platform Scrape Limits</span>
        </div>
        <div style={{ fontSize: "12px", color: "var(--text-dim)", marginTop: "4px" }}>
          Individual and Domain sweeps are capped independently. Leave empty or 0 for <strong>Unlimited</strong> scraping.
        </div>
      </div>

      <table className="platform-limits-modern-table">
        <thead>
          <tr>
            <th style={{ width: "35%" }}>Platform</th>
            <th style={{ width: "30%" }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <UserIcon size={13} color="var(--cyan)" /> Individual Cap
              </span>
            </th>
            <th style={{ width: "35%" }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <TagIcon size={13} color="var(--purple)" /> Domain Cap
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {platforms.map((p) => {
            const isFacebook = p.platform === "facebook";
            return (
              <Fragment key={p.platform}>
                <tr className="limits-table-row">
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 600, fontSize: "13px" }}>
                      <PlatformIcon platform={p.platform} size={18} />
                      <span>{p.name}</span>
                      {isFacebook && (
                        <button
                          type="button"
                          onClick={() => setFbExpanded((v) => !v)}
                          style={{
                            background: "rgba(0, 229, 255, 0.12)",
                            border: "1px solid rgba(0, 229, 255, 0.3)",
                            color: "var(--cyan)",
                            fontSize: "10.5px",
                            padding: "2px 7px",
                            borderRadius: "6px",
                            cursor: "pointer",
                            marginLeft: "auto",
                          }}
                        >
                          {fbExpanded ? "▴ Tabs" : "▾ Sub-tabs"}
                        </button>
                      )}
                    </div>
                  </td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      value={individualLimits[p.platform] ?? ""}
                      onChange={(e) => onIndividualChange(p.platform, e.target.value)}
                      placeholder="∞ Unlimited"
                      disabled={disabled}
                      className="limits-num-input"
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      value={domainLimits[p.platform] ?? ""}
                      onChange={(e) => onDomainChange(p.platform, e.target.value)}
                      placeholder="∞ Unlimited"
                      disabled={disabled}
                      className="limits-num-input"
                    />
                  </td>
                </tr>
                {isFacebook && fbExpanded && (
                  (
                    [
                      ["people", "People Tab"],
                      ["pages", "Pages Tab"],
                      ["groups", "Groups Tab"],
                    ] as const
                  ).map(([tab, label]) => (
                    <tr key={tab} className="limits-table-row" style={{ background: "rgba(0,0,0,0.18)" }}>
                      <td style={{ paddingLeft: "32px", fontSize: "12px", color: "var(--text-muted)" }}>
                        ↳ {label}
                      </td>
                      <td>
                        <input
                          type="number"
                          min={0}
                          value={facebookTabLimits[tab].individual}
                          onChange={(e) => onFacebookTabChange(tab, "individual", e.target.value)}
                          placeholder="∞ Unlimited"
                          disabled={disabled}
                          className="limits-num-input"
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min={0}
                          value={facebookTabLimits[tab].domain}
                          onChange={(e) => onFacebookTabChange(tab, "domain", e.target.value)}
                          placeholder="∞ Unlimited"
                          disabled={disabled}
                          className="limits-num-input"
                        />
                      </td>
                    </tr>
                  ))
                )}
              </Fragment>
            );
          })}
          {!platforms.length && (
            <tr>
              <td colSpan={3} style={{ textAlign: "center", padding: "20px", color: "var(--text-dim)" }}>
                No platforms registered yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// A selection -> the request's platform scope. Empty means "every ready
// platform" (send nothing). One collapses to `platform`, which keeps the
// backend's tighter per-platform job locking and its coalescing; two or
// more go as `platforms`.
function platformScope(sel: Set<string>): { platform?: string; platforms?: string[] } {
  const ids = [...sel];
  if (ids.length === 0) return {};
  if (ids.length === 1) return { platform: ids[0] };
  return { platforms: ids };
}

const EMPTY_FORM = { id: "", name: "", domain: "", nameKw: [] as string[], domainKw: [] as string[], cron: "" };

// The keyword categories a discovery sweep can be narrowed to, rendered as
// toggle chips beside the platform chips. Mirrors backend/shared/keywords.py's
// own INDIVIDUAL/DOMAIN vocabulary -- the server scopes by these exact names
// (see discovery_controller._validated_keyword_type), so they are a contract,
// not display strings.
const KEYWORD_SCOPES = [
  { id: "individual", label: "Individual Names" },
  { id: "domain", label: "Domain Keywords" },
] as const;

// Narrowed rather than plain `string` so the value flowing into
// discoveryApi.discover's `keyword_type` is checked at compile time against
// the two categories the server actually accepts, instead of relying on a
// cast that would happily pass a typo through to a 400.
type KeywordScope = (typeof KEYWORD_SCOPES)[number]["id"];

// A loaded client's groups for one keyword type. The API always returns
// `keyword_groups` (synthesising childless parents from the flat lists for
// a client saved before the feature existed), but this falls back to the
// flat list anyway so the form still populates against an older API build
// or a partially-cached response rather than silently showing no keywords.
function groupsOf(c: Client | null, type: "individual" | "domain", flat: string[]): KeywordGroup[] {
  const fromApi = c?.keyword_groups?.[type];
  if (Array.isArray(fromApi) && fromApi.length) {
    return fromApi.map((g) => ({
      parent: g.parent,
      children: Array.isArray(g.children) ? [...g.children] : [],
    }));
  }
  return (flat || []).map((parent) => ({ parent, children: [] }));
}

export function HomeView({
  clientId,
  platforms,
  onClient,
  onForgetClient,
  busy,
  analysisBusy,
  onStopDiscovery,
  onStopAnalysis,
  stoppingDiscovery = false,
  stoppingAnalysis = false,
  onJobs,
  onError,
}: Props) {
  const [clients, setClients] = useState<Client[]>([]);
  const [loadingClients, setLoadingClients] = useState(true);
  const [mode, setMode] = useState<Mode>(clientId ? "select" : "create");
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState<WorkspaceTab>("overview");
  const [globalSearchOpen, setGlobalSearchOpen] = useState(false);
  const [sidebarFilter, setSidebarFilter] = useState<"all" | "active" | "empty">("all");
  const [sidebarSearch, setSidebarSearch] = useState("");

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setGlobalSearchOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const [editing, setEditing] = useState(false);
  const [activeClient, setActiveClient] = useState<Client | null>(null);

  const [idInput, setIdInput] = useState(EMPTY_FORM.id);
  const [nameInput, setNameInput] = useState(EMPTY_FORM.name);
  const [domainInput, setDomainInput] = useState(EMPTY_FORM.domain);

  // Parent/child groups are the SOURCE OF TRUTH here; the flat parent
  // lists below are derived from them, never edited independently, so the
  // form physically cannot produce a client whose groups and flat keywords
  // disagree (the server re-derives them again on save for the same
  // reason). See backend/shared/keywords.py.
  const [nameGroups, setNameGroups] = useState<KeywordGroup[]>([]);
  const [domainGroups, setDomainGroups] = useState<KeywordGroup[]>([]);
  const nameKeywords = useMemo(() => nameGroups.map((g) => g.parent), [nameGroups]);
  const domainKeywords = useMemo(() => domainGroups.map((g) => g.parent), [domainGroups]);

  const [assetNameIndividualKw, setAssetNameIndividualKw] = useState<string[]>([]);
  const [assetNameDomainKw, setAssetNameDomainKw] = useState<string[]>([]);
  const [platformLimitsIndividual, setPlatformLimitsIndividual] = useState<Record<string, string>>({});
  const [platformLimitsDomain, setPlatformLimitsDomain] = useState<Record<string, string>>({});
  const [facebookTabLimits, setFacebookTabLimits] = useState<FacebookTabLimits>({
    people: { individual: "", domain: "" },
    pages: { individual: "", domain: "" },
    groups: { individual: "", domain: "" },
  });
  const [cron, setCron] = useState(EMPTY_FORM.cron);
  const [activeTab, setActiveTab] = useState<KeywordTab>("names");

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // A SET, not a single id: the Run hub lets an analyst pick any
  // combination of platforms. Empty means "every ready platform", which
  // is what the All Platforms chip selects and what the backend does when
  // no scope is sent -- so the previous behaviour is the empty case.
  const [sweepPlatforms, setSweepPlatforms] = useState<Set<string>>(new Set());
  const [analysisPlatforms, setAnalysisPlatforms] = useState<Set<string>>(new Set());

  const refreshClients = useCallback(() => {
    setLoadingClients(true);
    clientsApi
      .listClients()
      .then((res) => setClients(res.items))
      .catch((e) => onError((e as Error).message))
      .finally(() => setLoadingClients(false));
  }, [onError]);

  useEffect(() => {
    refreshClients();
  }, [refreshClients]);

  const loadIntoForm = (c: Client) => {
    setIdInput(c.client_id);
    setNameInput(c.name);
    setDomainInput(c.domain || "");
    setNameGroups(groupsOf(c, "individual", c.name_keywords || []));
    setDomainGroups(groupsOf(c, "domain", c.domain_keywords || []));
    setAssetNameIndividualKw(c.asset_name_individual_keywords || []);
    setAssetNameDomainKw(c.asset_name_domain_keywords || []);
    setPlatformLimitsIndividual(
      Object.fromEntries(Object.entries(c.platform_limits_individual || {}).map(([k, v]) => [k, String(v)])),
    );
    setPlatformLimitsDomain(
      Object.fromEntries(Object.entries(c.platform_limits_domain || {}).map(([k, v]) => [k, String(v)])),
    );
    const fbTabs = c.platform_tab_limits?.facebook || {};
    const readTab = (v: unknown): { individual: string; domain: string } => {
      if (v && typeof v === "object") {
        const o = v as { individual?: number; domain?: number };
        return {
          individual: o.individual !== undefined ? String(o.individual) : "",
          domain: o.domain !== undefined ? String(o.domain) : "",
        };
      }
      const flat = v !== undefined && v !== null ? String(v) : "";
      return { individual: flat, domain: flat };
    };
    setFacebookTabLimits({
      people: readTab(fbTabs.people),
      pages: readTab(fbTabs.pages),
      groups: readTab(fbTabs.groups),
    });
    setCron(c.cron || "");
  };

  const clearForm = () => {
    setIdInput(EMPTY_FORM.id);
    setNameInput(EMPTY_FORM.name);
    setDomainInput(EMPTY_FORM.domain);
    setNameGroups([]);
    setDomainGroups([]);
    setAssetNameIndividualKw([]);
    setAssetNameDomainKw([]);
    setPlatformLimitsIndividual({});
    setPlatformLimitsDomain({});
    setFacebookTabLimits({
      people: { individual: "", domain: "" },
      pages: { individual: "", domain: "" },
      groups: { individual: "", domain: "" },
    });
    setCron(EMPTY_FORM.cron);
  };

  useEffect(() => {
    if (!clientId || activeClient || !clients.length) return;
    const existing = clients.find((c) => c.client_id === clientId);
    if (existing) {
      setActiveClient(existing);
      loadIntoForm(existing);
      setMode("select");
      setEditing(false);
    }
  }, [clientId, clients]);

  const switchToCreate = () => {
    setMode("create");
    setActiveClient(null);
    setEditing(false);
    clearForm();
    setSweepPlatforms(new Set());
    setAnalysisPlatforms(new Set());
    setActiveWorkspaceTab("overview");
  };

  const selectSavedClient = (id: string) => {
    setSweepPlatforms(new Set());
    setAnalysisPlatforms(new Set());
    if (!id) {
      setActiveClient(null);
      setEditing(false);
      clearForm();
      onClient("", "");
      return;
    }
    const c = clients.find((x) => x.client_id === id);
    if (!c) return;
    setActiveClient(c);
    loadIntoForm(c);
    setMode("select");
    setEditing(false);
    onClient(c.client_id, c.name);
  };

  const startEditing = () => {
    if (!activeClient) return;
    loadIntoForm(activeClient);
    setEditing(true);
    setActiveWorkspaceTab("settings");
  };

  const cancelEditing = () => {
    if (activeClient) loadIntoForm(activeClient);
    setEditing(false);
  };

  const cloneClient = (c: Client) => {
    switchToCreate();
    setIdInput(`${c.client_id}-copy`);
    setNameInput(`${c.name || c.client_id} (Copy)`);
    setDomainInput(c.domain || "");
    setNameGroups(groupsOf(c, "individual", c.name_keywords || []));
    setDomainGroups(groupsOf(c, "domain", c.domain_keywords || []));
    setAssetNameIndividualKw([...(c.asset_name_individual_keywords || [])]);
    setAssetNameDomainKw([...(c.asset_name_domain_keywords || [])]);
    setPlatformLimitsIndividual(
      Object.fromEntries(Object.entries(c.platform_limits_individual || {}).map(([k, v]) => [k, String(v)])),
    );
    setPlatformLimitsDomain(
      Object.fromEntries(Object.entries(c.platform_limits_domain || {}).map(([k, v]) => [k, String(v)])),
    );
    toast.success(`Cloned configuration from "${c.name || c.client_id}". Review and save!`, { icon: "📋" });
  };

  const activeIndividualCount = activeClient?.name_keywords?.length || 0;
  const activeDomainCount = activeClient?.domain_keywords?.length || 0;
  const activeKeywordCount = activeIndividualCount + activeDomainCount;

  // Which keyword category a discovery sweep should cover. "" is BOTH --
  // the default, and what every sweep did before this control existed.
  // The full keyword list is still sent either way; the server scopes it
  // by each keyword's own resolved category (see
  // backend/services/discovery_service.py), so this and the per-category
  // caps can never disagree about what "individual" means.
  // Which keyword categories a discovery sweep covers. A SET with the same
  // semantics as `sweepPlatforms` above: EMPTY means all of them, which is
  // the default and what every sweep did before this existed. Selecting
  // both categories is the same thing as selecting neither, so it collapses
  // back to empty (see toggleKeywordType).
  const [sweepKeywordTypes, setSweepKeywordTypes] = useState<Set<KeywordScope>>(new Set());

  // The scope persists across client switches (the platform chips do too),
  // which can strand it on a category the newly-selected client has none of
  // -- that chip is disabled, so the only way out would be noticing it and
  // clicking another. Dropping the empty category is the honest fallback.
  useEffect(() => {
    setSweepKeywordTypes((prev) => {
      const next = new Set(prev);
      if (!activeIndividualCount) next.delete("individual");
      if (!activeDomainCount) next.delete("domain");
      return next.size === prev.size ? prev : next;
    });
  }, [activeIndividualCount, activeDomainCount]);

  const saveConfig = async (): Promise<Client | null> => {
    const id = idInput.trim();
    const name = nameInput.trim() || id;
    if (!id) {
      onError("Enter an org id first.");
      return null;
    }
    setSaving(true);
    setSaved(false);
    try {
      const parseLimits = (raws: Record<string, string>): Record<string, number> => {
        const out: Record<string, number> = {};
        for (const [platform, raw] of Object.entries(raws)) {
          const n = Number(raw);
          if (raw.trim() && Number.isFinite(n) && n > 0) out[platform] = Math.floor(n);
        }
        return out;
      };
      const parsedLimitsIndividual = parseLimits(platformLimitsIndividual);
      const parsedLimitsDomain = parseLimits(platformLimitsDomain);
      const fbTabLimits: Record<string, Record<string, number>> = {};
      for (const [tab, byType] of Object.entries(facebookTabLimits)) {
        const perType: Record<string, number> = {};
        for (const [kwType, raw] of Object.entries(byType)) {
          const n = Number(raw);
          if (raw.trim() && Number.isFinite(n) && n > 0) perType[kwType] = Math.floor(n);
        }
        if (Object.keys(perType).length) fbTabLimits[tab] = perType;
      }
      const client = await clientsApi.upsertClient({
        client_id: id,
        name,
        domain: domainInput.trim(),
        // Sent for older/other consumers, but the server treats
        // `keyword_groups` as authoritative and re-derives these from its
        // parents anyway -- see backend/shared/keywords.py.
        name_keywords: nameKeywords,
        domain_keywords: domainKeywords,
        keyword_groups: { individual: nameGroups, domain: domainGroups },
        asset_name_individual_keywords: assetNameIndividualKw,
        asset_name_domain_keywords: assetNameDomainKw,
        platform_limits_individual: parsedLimitsIndividual,
        platform_limits_domain: parsedLimitsDomain,
        platform_tab_limits: Object.keys(fbTabLimits).length ? { facebook: fbTabLimits } : {},
        cron: cron.trim() || null,
      });
      setActiveClient(client);
      setMode("select");
      setEditing(false);
      onClient(client.client_id, client.name);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      toast.success(`Client "${client.name}" saved!`, { icon: "💾" });
      refreshClients();
      return client;
    } catch (e) {
      onError((e as Error).message);
      return null;
    } finally {
      setSaving(false);
    }
  };

  const handleSearch = async () => {
    if (!activeClient) return;
    if (!activeKeywordCount) {
      onError("This client has no keywords yet — head to the Keywords tab to add executive names or brand keywords.");
      return;
    }
    // The server rejects this too (discovery_service raises rather than
    // sweeping nothing and reporting success), but catching it here means
    // the analyst finds out on click instead of via a failed job.
    if (sweepKeywordScope === "individual" && !activeIndividualCount) {
      onError("This client has no individual names configured — pick All Keywords or Domain, or add some in the Keywords tab.");
      return;
    }
    if (sweepKeywordScope === "domain" && !activeDomainCount) {
      onError("This client has no domain keywords configured — pick All Keywords or Individual, or add some in the Keywords tab.");
      return;
    }
    try {
      const { job_id } = await discoveryApi.discover({
        client_id: activeClient.client_id,
        keywords: dedupeKeywordsCaseInsensitive([
          ...(activeClient.name_keywords || []),
          ...(activeClient.domain_keywords || []),
        ]),
        ...platformScope(sweepPlatforms),
        ...(sweepKeywordScope ? { keyword_type: sweepKeywordScope } : {}),
      });
      const job = await jobsApi.job(job_id);
      onJobs([job]);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const nameOf = (id: string) =>
    platforms.find((p) => p.platform === id)?.name || id;
  // "Facebook", "Facebook +2", or "" for all-platforms -- a button label
  // has to stay short, so past two the rest becomes a count.
  const scopeLabel = (sel: Set<string>) => {
    const ids = [...sel];
    if (ids.length === 0) return "";
    if (ids.length === 1) return nameOf(ids[0]);
    return `${nameOf(ids[0])} +${ids.length - 1}`;
  };
  const sweepPlatformName = scopeLabel(sweepPlatforms);
  const analysisPlatformName = scopeLabel(analysisPlatforms);

  const handleRunAnalysis = async () => {
    if (!activeClient) return;
    const scope = analysisPlatformName ? `on ${analysisPlatformName}` : "across every ready platform";
    if (
      !(await confirmAction(
        `Re-run analysis for every validated profile of "${activeClient.name || activeClient.client_id}" ${scope}, including ones already analysed? This re-scrapes each one again.`,
      ))
    ) {
      return;
    }
    try {
      const { job_id } = await analysisApi.analyse({
        client_id: activeClient.client_id,
        force: true,
        ...platformScope(analysisPlatforms),
      });
      const job = await jobsApi.job(job_id);
      onJobs([job]);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const handleDelete = async () => {
    if (!activeClient) return;
    const confirmed = await confirmAction(
      `Permanently delete client "${activeClient.name || activeClient.client_id}"? This will delete ALL associated discovery profiles, validated profiles, analyst tags, and incidents. This cannot be undone.`,
    );
    if (!confirmed) return;
    setDeleting(true);
    try {
      const deletedId = activeClient.client_id;
      await clientsApi.deleteClient(deletedId);
      onForgetClient(deletedId);
      setActiveClient(null);
      setEditing(false);
      clearForm();
      onClient("", "");
      refreshClients();
      toast.success(`Client "${deletedId}" deleted.`, { icon: "🗑️" });
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const filteredClients = useMemo(() => {
    return clients.filter((c) => {
      const matchesSearch =
        !sidebarSearch.trim() ||
        c.name.toLowerCase().includes(sidebarSearch.toLowerCase()) ||
        c.client_id.toLowerCase().includes(sidebarSearch.toLowerCase()) ||
        (c.domain && c.domain.toLowerCase().includes(sidebarSearch.toLowerCase()));

      if (!matchesSearch) return false;

      const totalKw = (c.name_keywords?.length || 0) + (c.domain_keywords?.length || 0);
      if (sidebarFilter === "active") return totalKw > 0;
      if (sidebarFilter === "empty") return totalKw === 0;
      return true;
    });
  }, [clients, sidebarSearch, sidebarFilter]);

  // The chips drive BOTH buttons: an analyst picks a scope once and then
  // chooses what to run on it, rather than setting it twice.
  const targetPlatforms = sweepPlatforms;
  const togglePlatform = (id: string) => {
    const next = new Set(targetPlatforms);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSweepPlatforms(next);
    setAnalysisPlatforms(next);
  };
  const selectAllPlatforms = () => {
    setSweepPlatforms(new Set());
    setAnalysisPlatforms(new Set());
  };

  // Keyword-category chips, same interaction as the platform chips above:
  // toggle them on, and an EMPTY selection means all of them.
  const toggleKeywordType = (id: KeywordScope) => {
    const next = new Set(sweepKeywordTypes);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    // With only two categories, "both selected" IS "all" -- collapsing it
    // back to empty keeps one canonical representation of that state, so
    // the All chip lights up instead of two chips that mean the same thing.
    setSweepKeywordTypes(next.size === KEYWORD_SCOPES.length ? new Set() : next);
  };
  const selectAllKeywordTypes = () => setSweepKeywordTypes(new Set());

  // The single category to send to the API, or "" for all. Only a
  // selection of exactly one narrows anything -- empty (and, by the
  // collapse above, both) sweeps everything, which is what omitting the
  // parameter already means server-side.
  const sweepKeywordScope: KeywordScope | "" =
    sweepKeywordTypes.size === 1 ? [...sweepKeywordTypes][0] : "";
  return (
    <div className="clients-workspace-layout">
      {globalSearchOpen && (
        <GlobalSearchModal
          clients={clients}
          onSelectClient={(id) => selectSavedClient(id)}
          onClose={() => setGlobalSearchOpen(false)}
        />
      )}

      {/* LEFT SIDEBAR: Client Directory */}
      <div className="clients-sidebar-card">
        <div className="clients-sidebar-header">
          <div className="clients-sidebar-title">
            <BuildingIcon size={16} color="var(--cyan)" />
            <span>Clients Directory</span>
            <span style={{ fontSize: "11px", color: "var(--text-dim)", fontWeight: 500 }}>
              ({clients.length})
            </span>
          </div>
          <button
            type="button"
            className="btn-new-client-pill"
            onClick={switchToCreate}
            title="Create a new client"
          >
            <PlusIcon size={13} style={{ marginRight: "3px" }} /> New
          </button>
        </div>

        <div className="client-search-box">
          <span className="client-search-icon">
            <SearchIcon size={14} color="var(--text-dim)" />
          </span>
          <input
            value={sidebarSearch}
            onChange={(e) => setSidebarSearch(e.target.value)}
            placeholder="Search clients..."
          />
          <span className="client-search-shortcut">Ctrl K</span>
        </div>

        <div className="client-filter-pills">
          <button
            type="button"
            className={`client-filter-pill-btn ${sidebarFilter === "all" ? "active" : ""}`}
            onClick={() => setSidebarFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={`client-filter-pill-btn ${sidebarFilter === "active" ? "active" : ""}`}
            onClick={() => setSidebarFilter("active")}
          >
            Active ({clients.filter((c) => (c.name_keywords?.length || 0) + (c.domain_keywords?.length || 0) > 0).length})
          </button>
          <button
            type="button"
            className={`client-filter-pill-btn ${sidebarFilter === "empty" ? "active" : ""}`}
            onClick={() => setSidebarFilter("empty")}
          >
            Needs Setup
          </button>
        </div>

        <div className="client-directory-list">
          {loadingClients ? (
            <div style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)", fontSize: "12px" }}>
              Loading clients...
            </div>
          ) : !filteredClients.length ? (
            <div style={{ textAlign: "center", padding: "24px", color: "var(--text-dim)", fontSize: "12px" }}>
              {sidebarSearch ? "No matching clients found." : "No clients configured."}
            </div>
          ) : (
            filteredClients.map((c) => {
              const isSelected = mode === "select" && activeClient?.client_id === c.client_id;
              const kwCount = (c.name_keywords?.length || 0) + (c.domain_keywords?.length || 0);
              return (
                <div
                  key={c.client_id}
                  className={`client-directory-item ${isSelected ? "active" : ""}`}
                  onClick={() => selectSavedClient(c.client_id)}
                >
                  <div className="client-dir-avatar">
                    {(c.name || c.client_id).charAt(0).toUpperCase()}
                  </div>
                  <div className="client-dir-info">
                    <div className="client-dir-name">{c.name || c.client_id}</div>
                    <div className="client-dir-meta">
                      <span>{c.domain || c.client_id}</span>
                    </div>
                  </div>
                  <span className="client-dir-badge" title={`${kwCount} total keywords`}>
                    {kwCount} kw
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* RIGHT DETAIL WORKSPACE */}
      <div className="client-workspace-pane">
        {mode === "create" ? (
          /* CREATE CLIENT WORKSPACE */
          <div className="dashboard-card-box" style={{ background: "var(--bg-card)", padding: "24px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "12px" }}>
              <div style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-main)", display: "flex", alignItems: "center", gap: "8px" }}>
                <SparklesIcon size={18} color="var(--cyan)" />
                <span>Create New Client</span>
              </div>
              <button
                type="button"
                className="text-link-btn"
                onClick={() => {
                  if (clients.length) {
                    selectSavedClient(clients[0].client_id);
                  } else {
                    setMode("select");
                  }
                }}
              >
                ✕ Cancel
              </button>
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label className="field-label">1. Organization Details</label>
              <div className="client-setup-box" style={{ flexWrap: "wrap", marginTop: "8px" }}>
                <input
                  value={idInput}
                  onChange={(e) => setIdInput(e.target.value)}
                  placeholder="org id (unique slug, e.g. acme-corp)…"
                  className="client-select-input"
                />
                <input
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  placeholder="organization / client display name…"
                  className="client-select-input"
                />
                <input
                  value={domainInput}
                  onChange={(e) => setDomainInput(e.target.value)}
                  placeholder="official website domain (e.g. acme.com)…"
                  className="client-select-input"
                />
              </div>
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label className="field-label">2. Search Keywords</label>
              <div style={{ marginTop: "8px" }}>
                <KeywordTabs
                  activeTab={activeTab}
                  onTab={setActiveTab}
                  nameKeywords={nameKeywords}
                  domainKeywords={domainKeywords}
                  nameGroups={nameGroups}
                  domainGroups={domainGroups}
                  onNameGroups={setNameGroups}
                  onDomainGroups={setDomainGroups}
                  assetNameIndividualKw={assetNameIndividualKw}
                  assetNameDomainKw={assetNameDomainKw}
                  onAddAssetIndividual={(v) => setAssetNameIndividualKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetIndividual={(i) => setAssetNameIndividualKw((prev) => prev.filter((_, idx) => idx !== i))}
                  onAddAssetDomain={(v) => setAssetNameDomainKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetDomain={(i) => setAssetNameDomainKw((prev) => prev.filter((_, idx) => idx !== i))}
                  platforms={platforms}
                  disabled={busy}
                />
              </div>
            </div>

            <div style={{ marginBottom: "20px" }}>
              <PlatformLimitsEditor
                platforms={platforms}
                individualLimits={platformLimitsIndividual}
                domainLimits={platformLimitsDomain}
                onIndividualChange={(platform, value) => setPlatformLimitsIndividual((prev) => ({ ...prev, [platform]: value }))}
                onDomainChange={(platform, value) => setPlatformLimitsDomain((prev) => ({ ...prev, [platform]: value }))}
                facebookTabLimits={facebookTabLimits}
                onFacebookTabChange={(tab, kwType, value) =>
                  setFacebookTabLimits((prev) => ({ ...prev, [tab]: { ...prev[tab], [kwType]: value } }))
                }
                disabled={busy}
              />
            </div>

            <button
              onClick={saveConfig}
              disabled={saving || !idInput.trim()}
              className="btn-cyber-primary"
              style={{ marginTop: "16px", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "8px" }}
            >
              {saving ? "Creating Client…" : (
                <>
                  <SaveIcon size={15} /> Save & Create Client
                </>
              )}
            </button>
          </div>
        ) : !activeClient ? (
          /* NO CLIENT SELECTED EMPTY STATE */
          <div
            className="dashboard-card-box"
            style={{
              background: "var(--bg-card)",
              padding: "60px 24px",
              textAlign: "center",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "12px",
            }}
          >
            <BuildingIcon size={48} color="var(--cyan)" />
            <div style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-main)" }}>
              Select or Create a Client
            </div>
            <div style={{ fontSize: "13px", color: "var(--text-dim)", maxWidth: "420px" }}>
              Choose a client from the sidebar directory on the left or click <strong>+ New</strong> to set up monitoring for a new brand.
            </div>
            <button
              type="button"
              className="btn-cyber-primary"
              style={{ width: "auto", padding: "10px 24px", marginTop: "12px", display: "inline-flex", alignItems: "center", gap: "8px" }}
              onClick={switchToCreate}
            >
              <PlusIcon size={14} /> Create New Client
            </button>
          </div>
        ) : (
          /* ACTIVE CLIENT DETAIL WORKSPACE */
          <>
            {/* HERO HEADER */}
            <div className="client-hero-header-card">
              <div className="client-hero-left">
                <div className="client-hero-avatar">
                  {(activeClient.name || activeClient.client_id).charAt(0).toUpperCase()}
                </div>
                <div className="client-hero-title-group">
                  <div className="client-hero-name">{activeClient.name || activeClient.client_id}</div>
                  <div className="client-hero-meta-row">
                    <span className="client-hero-id">{activeClient.client_id}</span>
                    {activeClient.domain && (
                      <span className="client-hero-domain" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                        <GlobeIcon size={12} /> {activeClient.domain}
                      </span>
                    )}
                    <span className="status-dot-badge">
                      <span className="status-dot" /> Active
                    </span>
                  </div>
                </div>
              </div>

              <div className="client-hero-actions">
                <button
                  type="button"
                  className="client-hero-btn"
                  onClick={startEditing}
                  title="Edit client configuration"
                  style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}
                >
                  <EditIcon size={13} /> Edit
                </button>
                <button
                  type="button"
                  className="client-hero-btn"
                  onClick={() => cloneClient(activeClient)}
                  title="Duplicate configuration"
                  style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}
                >
                  <CloneIcon size={13} /> Clone
                </button>
                <button
                  type="button"
                  className="client-hero-btn danger"
                  onClick={handleDelete}
                  disabled={deleting}
                  title="Permanently delete client"
                >
                  {deleting ? "Deleting…" : <TrashIcon size={14} />}
                </button>
              </div>
            </div>

            {/* WORKSPACE TABS NAV */}
            <div className="client-workspace-nav">
              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "overview" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("overview")}
              >
                <ZapIcon size={15} />
                <span>Run & Overview</span>
              </button>

              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "keywords" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("keywords")}
              >
                <TagIcon size={15} />
                <span>Keywords & Assets</span>
                <span className="workspace-tab-counter">{activeKeywordCount}</span>
              </button>

              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "limits" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("limits")}
              >
                <TargetIcon size={15} />
                <span>Scraping Limits</span>
              </button>

              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "settings" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("settings")}
              >
                <SettingsGearIcon size={15} />
                <span>Client Settings</span>
              </button>
            </div>

            {/* TAB CONTENT 1: RUN & OVERVIEW */}
            {activeWorkspaceTab === "overview" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                {/* UNIFIED COMMAND RUNNER */}
                <div className="unified-runner-card">
                  <div className="unified-platform-selector">
                    <button
                      type="button"
                      className={`unified-platform-btn ${targetPlatforms.size === 0 ? "active" : ""}`}
                      onClick={selectAllPlatforms}
                      title="Run on every platform with a ready session"
                    >
                      <CyberGlobeIcon size={15} color={targetPlatforms.size === 0 ? "#7C5CFF" : "#94A3B8"} />
                      <span>All Platforms</span>
                    </button>
                    {platforms.map((p) => {
                      const dotClass =
                        p.session_state === "ready"
                          ? "ready"
                          : p.session_state === "login_required"
                          ? "warn"
                          : "error";
                      return (
                        <button
                          key={p.platform}
                          type="button"
                          className={`unified-platform-btn ${targetPlatforms.has(p.platform) ? "active" : ""}`}
                          onClick={() => togglePlatform(p.platform)}
                          title={`${targetPlatforms.has(p.platform) ? "Click to remove" : "Click to add"} ${p.name} `
                            + `(Session: ${p.session_state}) -- pick as many as you like`}
                        >
                          <PlatformIcon platform={p.platform} size={15} />
                          <span>{p.name}</span>
                          <span className={`runner-session-dot ${dotClass}`} />
                        </button>
                      );
                    })}
                  </div>

                  {/* WHICH KEYWORDS to sweep -- the same interaction as the
                      platform chips above: toggle them on, and selecting
                      NONE means all of them. Scopes discovery only; analysis
                      re-reads whatever discovery already stored, so it has no
                      keyword scope of its own to set. */}
                  <div className="unified-platform-selector" style={{ marginTop: "8px" }}>
                    <button
                      type="button"
                      className={`unified-platform-btn ${sweepKeywordTypes.size === 0 ? "active" : ""}`}
                      onClick={selectAllKeywordTypes}
                      title="Search both individual names and domain keywords"
                    >
                      <LayersIcon size={15} color={sweepKeywordTypes.size === 0 ? "#7C5CFF" : "#94A3B8"} />
                      <span>All Keywords</span>
                      <span className="kw-tab-count">{activeKeywordCount}</span>
                    </button>
                    {KEYWORD_SCOPES.map((opt) => {
                      const count = opt.id === "individual" ? activeIndividualCount : activeDomainCount;
                      const on = sweepKeywordTypes.has(opt.id);
                      return (
                        <button
                          key={opt.id}
                          type="button"
                          className={`unified-platform-btn ${on ? "active" : ""}`}
                          onClick={() => toggleKeywordType(opt.id)}
                          disabled={count === 0}
                          title={count === 0
                            ? `This client has no ${opt.label.toLowerCase()} configured`
                            : `${on ? "Click to remove" : "Click to add"} ${opt.label} -- selecting none searches everything`}
                        >
                          {opt.id === "individual"
                            ? <UserIcon size={15} color={on ? "#7C5CFF" : "#94A3B8"} />
                            : <TagIcon size={15} color={on ? "#7C5CFF" : "#94A3B8"} />}
                          <span>{opt.label}</span>
                          <span className="kw-tab-count">{count}</span>
                        </button>
                      );
                    })}
                  </div>

                  {/* Discover/Analyse stay available while something is
                      already in flight -- including a sweep the round-robin
                      scheduler started for this client, which the app adopts
                      and reports as `busy`. Replacing the action button with
                      Stop (as this used to) meant an operator simply could
                      not queue a manual run for as long as the engine held
                      the client, with no way to tell the two apart. The
                      backend serialises the two safely on its per-platform
                      locks, so the new run just queues behind the running
                      one -- which is what the hint below says. */}
                  <div className="runner-actions-grid">
                    <div className="runner-action-cell">
                      <button
                        type="button"
                        className="runner-btn-primary"
                        disabled={!activeKeywordCount}
                        onClick={handleSearch}
                        title={
                          busy
                            ? "Queue another discovery sweep -- it starts when the running one finishes"
                            : "Run a discovery sweep now"
                        }
                      >
                        <DiscoverIcon size={17} color="#fff" />
                        <span>
                          {(() => {
                            const bits = [
                              sweepPlatformName,
                              sweepKeywordScope === "individual" ? "Individual"
                                : sweepKeywordScope === "domain" ? "Domain" : "",
                            ].filter(Boolean);
                            return bits.length ? `Discover (${bits.join(" · ")})` : "Discover";
                          })()}
                        </span>
                      </button>
                      {busy && onStopDiscovery && (
                        <button
                          type="button"
                          className="runner-btn-stop"
                          onClick={onStopDiscovery}
                          disabled={stoppingDiscovery}
                          title="Abort the discovery sweep that is running now"
                        >
                          <StopIcon size={17} color="#fff" />
                          <span>{stoppingDiscovery ? "Stopping..." : "Stop Discovery"}</span>
                        </button>
                      )}
                    </div>

                    <div className="runner-action-cell">
                      <button
                        type="button"
                        className="runner-btn-secondary"
                        onClick={handleRunAnalysis}
                        title={
                          analysisBusy
                            ? "Queue another analysis run -- it starts when the running one finishes"
                            : "Re-run analysis now"
                        }
                      >
                        <AnalyseIcon size={17} color="#00F0FF" />
                        <span>
                          {analysisPlatformName
                            ? `Analyse (${analysisPlatformName})`
                            : "Analyse"}
                        </span>
                      </button>
                      {analysisBusy && onStopAnalysis && (
                        <button
                          type="button"
                          className="runner-btn-stop"
                          onClick={onStopAnalysis}
                          disabled={stoppingAnalysis}
                          title="Abort the analysis run that is running now"
                        >
                          <StopIcon size={17} color="#fff" />
                          <span>{stoppingAnalysis ? "Stopping..." : "Stop Analysis"}</span>
                        </button>
                      )}
                    </div>
                  </div>

                  {(busy || analysisBusy) && (
                    <div className="runner-queue-hint">
                      A run is already in flight for this client (it may be the
                      scheduler&rsquo;s). Starting another queues it behind the
                      current one rather than running both at once.
                    </div>
                  )}
                </div>

                {/* QUICK STATS METRIC GRID */}
                <div className="client-quick-stats-grid">
                  <div className="client-quick-stat-box">
                    <span className="quick-stat-label">Executive Names</span>
                    <span className="quick-stat-value">{activeClient.name_keywords?.length || 0}</span>
                    <span className="quick-stat-sub">Individual keywords</span>
                  </div>

                  <div className="client-quick-stat-box">
                    <span className="quick-stat-label">Brand Domains</span>
                    <span className="quick-stat-value">{activeClient.domain_keywords?.length || 0}</span>
                    <span className="quick-stat-sub">Brand keywords</span>
                  </div>

                  <div className="client-quick-stat-box">
                    <span className="quick-stat-label">Active Limits</span>
                    <span className="quick-stat-value">
                      {new Set([
                        ...Object.keys(activeClient.platform_limits_individual || {}),
                        ...Object.keys(activeClient.platform_limits_domain || {}),
                      ]).size}
                    </span>
                    <span className="quick-stat-sub">Capped platforms</span>
                  </div>

                  <div className="client-quick-stat-box">
                    <span className="quick-stat-label">Monitoring</span>
                    <span className="quick-stat-value" style={{ fontSize: "15px", marginTop: "4px", color: "var(--success)" }}>
                      ● Active
                    </span>
                    <span className="quick-stat-sub">Continuous protection</span>
                  </div>
                </div>
              </div>
            )}

            {/* TAB CONTENT 2: KEYWORDS & ASSETS */}
            {activeWorkspaceTab === "keywords" && (
              <div className="dashboard-card-box" style={{ background: "var(--bg-card)", padding: "20px" }}>
                <KeywordTabs
                  activeTab={activeTab}
                  onTab={setActiveTab}
                  nameKeywords={nameKeywords}
                  domainKeywords={domainKeywords}
                  nameGroups={nameGroups}
                  domainGroups={domainGroups}
                  onNameGroups={setNameGroups}
                  onDomainGroups={setDomainGroups}
                  assetNameIndividualKw={assetNameIndividualKw}
                  assetNameDomainKw={assetNameDomainKw}
                  onAddAssetIndividual={(v) => setAssetNameIndividualKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetIndividual={(i) => setAssetNameIndividualKw((prev) => prev.filter((_, idx) => idx !== i))}
                  onAddAssetDomain={(v) => setAssetNameDomainKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetDomain={(i) => setAssetNameDomainKw((prev) => prev.filter((_, idx) => idx !== i))}
                  platforms={platforms}
                  disabled={busy}
                />

                <div style={{ marginTop: "20px", display: "flex", justifyContent: "flex-end" }}>
                  <button
                    onClick={saveConfig}
                    disabled={saving}
                    className="btn-cyber-primary"
                    style={{ width: "auto", padding: "10px 24px", margin: 0, display: "inline-flex", alignItems: "center", gap: "6px" }}
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : (
                      <>
                        <SaveIcon size={14} /> Save Keyword Changes
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}

            {/* TAB CONTENT 3: SCRAPING LIMITS */}
            {activeWorkspaceTab === "limits" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <PlatformLimitsEditor
                  platforms={platforms}
                  individualLimits={platformLimitsIndividual}
                  domainLimits={platformLimitsDomain}
                  onIndividualChange={(platform, value) => setPlatformLimitsIndividual((prev) => ({ ...prev, [platform]: value }))}
                  onDomainChange={(platform, value) => setPlatformLimitsDomain((prev) => ({ ...prev, [platform]: value }))}
                  facebookTabLimits={facebookTabLimits}
                  onFacebookTabChange={(tab, kwType, value) =>
                    setFacebookTabLimits((prev) => ({ ...prev, [tab]: { ...prev[tab], [kwType]: value } }))
                  }
                  disabled={busy}
                />

                <div style={{ display: "flex", justifyContent: "flex-end" }}>
                  <button
                    onClick={saveConfig}
                    disabled={saving}
                    className="btn-cyber-primary"
                    style={{ width: "auto", padding: "10px 24px", margin: 0, display: "inline-flex", alignItems: "center", gap: "6px" }}
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : (
                      <>
                        <SaveIcon size={14} /> Save Scrape Limits
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}

            {/* TAB CONTENT 4: SETTINGS */}
            {activeWorkspaceTab === "settings" && (
              <div className="dashboard-card-box" style={{ background: "var(--bg-card)", padding: "24px", display: "flex", flexDirection: "column", gap: "20px" }}>
                <div>
                  <div style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-main)", marginBottom: "4px", display: "flex", alignItems: "center", gap: "8px" }}>
                    <BuildingIcon size={16} color="var(--cyan)" />
                    <span>Client Information</span>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "12px" }}>
                    Update organization display name, associated domain, and identifier.
                  </div>
                  <div className="client-setup-box" style={{ flexWrap: "wrap", margin: 0 }}>
                    <input
                      value={idInput}
                      onChange={(e) => setIdInput(e.target.value)}
                      placeholder="org id…"
                      disabled={true}
                      className="client-select-input"
                      style={{ opacity: 0.6 }}
                      title="Organization ID cannot be modified after creation"
                    />
                    <input
                      value={nameInput}
                      onChange={(e) => setNameInput(e.target.value)}
                      placeholder="organization name…"
                      className="client-select-input"
                    />
                    <input
                      value={domainInput}
                      onChange={(e) => setDomainInput(e.target.value)}
                      placeholder="domain, e.g. xyz.com…"
                      className="client-select-input"
                    />
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", borderTop: "1px solid var(--border-subtle)", paddingTop: "16px" }}>
                  {editing && (
                    <button
                      type="button"
                      className="action-btn"
                      onClick={cancelEditing}
                      style={{
                        background: "rgba(255, 255, 255, 0.06)",
                        color: "var(--text-main)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: "8px",
                        padding: "8px 16px",
                      }}
                    >
                      Cancel
                    </button>
                  )}
                  <button
                    onClick={saveConfig}
                    disabled={saving}
                    className="btn-cyber-primary"
                    style={{ width: "auto", padding: "10px 24px", margin: 0, display: "inline-flex", alignItems: "center", gap: "6px" }}
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : (
                      <>
                        <SaveIcon size={14} /> Save Changes
                      </>
                    )}
                  </button>
                </div>

                <div style={{ borderTop: "1px solid rgba(239, 68, 68, 0.2)", paddingTop: "18px", marginTop: "10px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--danger)", marginBottom: "4px", display: "flex", alignItems: "center", gap: "6px" }}>
                    <AlertTriangleIcon size={16} color="var(--danger)" />
                    <span>Danger Zone</span>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "12px" }}>
                    Permanently delete this organization and cascade remove all associated discovery hits, validated profiles, and incident tickets.
                  </div>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="danger-link-btn"
                    style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                  >
                    {deleting ? "Deleting Organization…" : (
                      <>
                        <TrashIcon size={14} /> Delete Organization & All Associated Data
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
