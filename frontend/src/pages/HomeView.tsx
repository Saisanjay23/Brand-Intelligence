import { useCallback, useEffect, useMemo, useState, Fragment } from "react";
import { toast } from "react-hot-toast";
import { analysisApi } from "../api/analysisApi";
import { clientsApi } from "../api/clientsApi";
import { discoveryApi } from "../api/discoveryApi";
import { jobsApi } from "../api/jobsApi";
import type { Client, Job, PlatformHealth } from "../api/types";
import { PlatformIcon } from "../components/PlatformIcon";
import { GlobalSearchModal } from "../components/GlobalSearchModal";
import { confirmAction } from "../utils/confirmAction";

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
          {bulkOpen ? "▾ Close bulk paste" : "▸ 📋 Bulk import"}
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

function KeywordGeneratorModal({
  nameKeywords,
  domainKeywords,
  onAddKeywords,
  onClose,
}: {
  nameKeywords: string[];
  domainKeywords: string[];
  onAddKeywords: (type: "names" | "domain", list: string[]) => void;
  onClose: () => void;
}) {
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set());

  const suggestions = useMemo(() => {
    const list: { type: "names" | "domain"; kw: string; pattern: string }[] = [];
    const namePrefixes = ["official_", "real_", "the_real_"];
    const nameSuffixes = ["_official", "_real", "_vip", "_direct", "_fanpage", "_investment", "_crypto"];
    const domainPrefixes = ["official_", "support_", "help_"];
    const domainSuffixes = ["_support", "_helpdesk", "_careers", "_jobs", "_fund", "_finance", "_promo", "_giveaway", "_official", "_app", "_service"];

    nameKeywords.forEach((name) => {
      const clean = name.toLowerCase().replace(/\s+/g, "_");
      namePrefixes.forEach((pre) => list.push({ type: "names", kw: `${pre}${clean}`, pattern: "Prefix Impersonation" }));
      nameSuffixes.forEach((suf) => list.push({ type: "names", kw: `${clean}${suf}`, pattern: "Suffix Impersonation" }));
    });

    domainKeywords.forEach((dom) => {
      const clean = dom.toLowerCase().replace(/\s+/g, "_");
      domainPrefixes.forEach((pre) => list.push({ type: "domain", kw: `${pre}${clean}`, pattern: "Customer Support Lure" }));
      domainSuffixes.forEach((suf) => list.push({ type: "domain", kw: `${clean}${suf}`, pattern: "Scam / Giveaway / Job Lure" }));
    });

    return list;
  }, [nameKeywords, domainKeywords]);

  const toggleSelect = (kw: string) => {
    setSelectedSuggestions((prev) => {
      const next = new Set(prev);
      next.has(kw) ? next.delete(kw) : next.add(kw);
      return next;
    });
  };

  const selectAll = () => {
    if (selectedSuggestions.size === suggestions.length) {
      setSelectedSuggestions(new Set());
    } else {
      setSelectedSuggestions(new Set(suggestions.map((s) => s.kw)));
    }
  };

  const handleApply = () => {
    const namesToAdd: string[] = [];
    const domainToAdd: string[] = [];
    suggestions.forEach((s) => {
      if (selectedSuggestions.has(s.kw)) {
        if (s.type === "names") namesToAdd.push(s.kw);
        else domainToAdd.push(s.kw);
      }
    });
    if (namesToAdd.length) onAddKeywords("names", namesToAdd);
    if (domainToAdd.length) onAddKeywords("domain", domainToAdd);
    toast.success(`Added ${selectedSuggestions.size} threat actor keywords!`, { icon: "✨" });
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
            <span>✨ Threat Actor Keyword Generator</span>
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
                  key={s.kw}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "6px 10px",
                    borderRadius: "6px",
                    cursor: "pointer",
                    background: selectedSuggestions.has(s.kw) ? "rgba(0, 229, 255, 0.08)" : "transparent",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <input
                      type="checkbox"
                      checked={selectedSuggestions.has(s.kw)}
                      onChange={() => toggleSelect(s.kw)}
                    />
                    <span style={{ fontSize: "12px", fontFamily: "var(--font-mono)", color: "var(--text-main)" }}>{s.kw}</span>
                  </div>
                  <span style={{ fontSize: "10px", color: "var(--text-dim)", background: "var(--bg-inner)", padding: "2px 6px", borderRadius: "4px" }}>
                    {s.type === "names" ? "👤 " : "🏷️ "}{s.pattern}
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
            style={{ width: "auto", padding: "7px 18px", fontSize: "12px", marginTop: 0 }}
          >
            ➕ Add {selectedSuggestions.size} Selected Keywords
          </button>
        </div>
      </div>
    </div>
  );
}

function KeywordTabs({
  activeTab,
  onTab,
  nameKeywords,
  domainKeywords,
  onAddName,
  onRemoveName,
  onAddDomain,
  onRemoveDomain,
  assetNameIndividualKw,
  assetNameDomainKw,
  onAddAssetIndividual,
  onRemoveAssetIndividual,
  onAddAssetDomain,
  onRemoveAssetDomain,
  disabled,
}: {
  activeTab: KeywordTab;
  onTab: (t: KeywordTab) => void;
  nameKeywords: string[];
  domainKeywords: string[];
  onAddName: (v: string) => void;
  onRemoveName: (i: number) => void;
  onAddDomain: (v: string) => void;
  onRemoveDomain: (i: number) => void;
  assetNameIndividualKw: string[];
  assetNameDomainKw: string[];
  onAddAssetIndividual: (v: string) => void;
  onRemoveAssetIndividual: (i: number) => void;
  onAddAssetDomain: (v: string) => void;
  onRemoveAssetDomain: (i: number) => void;
  disabled?: boolean;
}) {
  const [genOpen, setGenOpen] = useState(false);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <div className="kw-tab-row" style={{ margin: 0 }}>
          <button className={`kw-tab-btn ${activeTab === "names" ? "active" : ""}`} onClick={() => onTab("names")}>
            👤 Individual Names
            {nameKeywords.length > 0 && <span className="kw-tab-count">{nameKeywords.length}</span>}
          </button>
          <button className={`kw-tab-btn ${activeTab === "domain" ? "active" : ""}`} onClick={() => onTab("domain")}>
            🏷️ Domain Keywords
            {domainKeywords.length > 0 && <span className="kw-tab-count">{domainKeywords.length}</span>}
          </button>
          <button className={`kw-tab-btn ${activeTab === "assetNames" ? "active" : ""}`} onClick={() => onTab("assetNames")}>
            🏷️ Asset Names
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
            gap: "5px",
          }}
          title="Auto-generate threat actor and fake support keyword permutations"
        >
          <span>✨</span> Suggest Threat Keywords
        </button>
      </div>

      <div style={{ display: activeTab === "names" ? "block" : "none" }}>
        <ChipInput
          chips={nameKeywords}
          onAdd={onAddName}
          onRemove={onRemoveName}
          placeholder="Type an executive/individual name and press Enter…"
          disabled={disabled}
        />
      </div>

      <div style={{ display: activeTab === "domain" ? "block" : "none" }}>
        <ChipInput
          chips={domainKeywords}
          onAdd={onAddDomain}
          onRemove={onRemoveDomain}
          placeholder="Type a brand/product keyword and press Enter…"
          disabled={disabled}
        />
      </div>

      <div style={{ display: activeTab === "assetNames" ? "block" : "none" }}>
        <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "10px" }}>
          Target asset name overrides mapped for the Analysis & Incident Reporting views.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <div>
            <label className="field-label" style={{ marginBottom: "6px" }}>👤 Individual Asset Names</label>
            <ChipInput
              chips={assetNameIndividualKw}
              onAdd={onAddAssetIndividual}
              onRemove={onRemoveAssetIndividual}
              placeholder="Asset name for individuals…"
              disabled={disabled}
            />
          </div>
          <div>
            <label className="field-label" style={{ marginBottom: "6px" }}>🌐 Domain Asset Names</label>
            <ChipInput
              chips={assetNameDomainKw}
              onAdd={onAddAssetDomain}
              onRemove={onRemoveAssetDomain}
              placeholder="Asset name for domains…"
              disabled={disabled}
            />
          </div>
        </div>
      </div>

      {genOpen && (
        <KeywordGeneratorModal
          nameKeywords={nameKeywords}
          domainKeywords={domainKeywords}
          onAddKeywords={(type, list) => {
            if (type === "names") {
              list.forEach(onAddName);
            } else {
              list.forEach(onAddDomain);
            }
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
          <span>🎯 Per-Platform Scrape Limits</span>
        </div>
        <div style={{ fontSize: "12px", color: "var(--text-dim)", marginTop: "4px" }}>
          Individual and Domain sweeps are capped independently. Leave empty or 0 for <strong>Unlimited</strong> scraping.
        </div>
      </div>

      <table className="platform-limits-modern-table">
        <thead>
          <tr>
            <th style={{ width: "35%" }}>Platform</th>
            <th style={{ width: "30%" }}>👤 Individual Cap</th>
            <th style={{ width: "35%" }}>🏷️ Domain Cap</th>
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

const EMPTY_FORM = { id: "", name: "", domain: "", nameKw: [] as string[], domainKw: [] as string[], cron: "" };

export function HomeView({
  clientId,
  platforms,
  onClient,
  onForgetClient,
  busy,
  analysisBusy,
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

  const [nameKeywords, setNameKeywords] = useState<string[]>(EMPTY_FORM.nameKw);
  const [domainKeywords, setDomainKeywords] = useState<string[]>(EMPTY_FORM.domainKw);
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

  const [sweepPlatform, setSweepPlatform] = useState("");
  const [analysisPlatform, setAnalysisPlatform] = useState("");

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
    setNameKeywords(c.name_keywords || []);
    setDomainKeywords(c.domain_keywords || []);
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
    setNameKeywords(EMPTY_FORM.nameKw);
    setDomainKeywords(EMPTY_FORM.domainKw);
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
    setSweepPlatform("");
    setAnalysisPlatform("");
    setActiveWorkspaceTab("overview");
  };

  const selectSavedClient = (id: string) => {
    setSweepPlatform("");
    setAnalysisPlatform("");
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
    setNameKeywords([...(c.name_keywords || [])]);
    setDomainKeywords([...(c.domain_keywords || [])]);
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

  const activeKeywordCount = (activeClient?.name_keywords?.length || 0) + (activeClient?.domain_keywords?.length || 0);

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
        name_keywords: nameKeywords,
        domain_keywords: domainKeywords,
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
    try {
      const { job_id } = await discoveryApi.discover({
        client_id: activeClient.client_id,
        keywords: dedupeKeywordsCaseInsensitive([
          ...(activeClient.name_keywords || []),
          ...(activeClient.domain_keywords || []),
        ]),
        platform: sweepPlatform || undefined,
      });
      const job = await jobsApi.job(job_id);
      onJobs([job]);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const sweepPlatformName = sweepPlatform
    ? platforms.find((p) => p.platform === sweepPlatform)?.name || sweepPlatform
    : "";
  const analysisPlatformName = analysisPlatform
    ? platforms.find((p) => p.platform === analysisPlatform)?.name || analysisPlatform
    : "";

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
        platform: analysisPlatform || undefined,
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

  const targetPlatform = sweepPlatform;
  const setTargetPlatform = (p: string) => {
    setSweepPlatform(p);
    setAnalysisPlatform(p);
  };

  return (
    <div className="bento-clients-layout">
      {globalSearchOpen && (
        <GlobalSearchModal
          clients={clients}
          onSelectClient={(id) => selectSavedClient(id)}
          onClose={() => setGlobalSearchOpen(false)}
        />
      )}

      {/* CREATE / EDIT CLIENT MODAL */}
      {(mode === "create" || editing) && (
        <div
          className="bento-modal-backdrop"
          onClick={() => {
            if (editing) cancelEditing();
            else setMode("select");
          }}
        >
          <div className="bento-modal-box" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "12px" }}>
              <div style={{ fontSize: "16px", fontWeight: 800, color: "var(--text-main)", display: "flex", alignItems: "center", gap: "8px" }}>
                <span>{editing ? "✏️ Edit Client Information" : "➕ Create New Client Organization"}</span>
              </div>
              <button
                type="button"
                onClick={() => {
                  if (editing) cancelEditing();
                  else setMode("select");
                }}
                style={{ background: "transparent", border: "none", color: "var(--text-muted)", fontSize: "16px", cursor: "pointer" }}
              >
                ✕
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div>
                <label style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--text-dim)", display: "block", marginBottom: "4px" }}>
                  Organization ID (Slug)
                </label>
                <input
                  value={idInput}
                  onChange={(e) => setIdInput(e.target.value)}
                  placeholder="e.g. adani, tesla, acme..."
                  disabled={editing}
                  className="client-select-input"
                  style={{ width: "100%", opacity: editing ? 0.6 : 1 }}
                />
              </div>

              <div>
                <label style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--text-dim)", display: "block", marginBottom: "4px" }}>
                  Organization Display Name
                </label>
                <input
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  placeholder="e.g. Adani Group, Tesla Inc..."
                  className="client-select-input"
                  style={{ width: "100%" }}
                />
              </div>

              <div>
                <label style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--text-dim)", display: "block", marginBottom: "4px" }}>
                  Primary Domain
                </label>
                <input
                  value={domainInput}
                  onChange={(e) => setDomainInput(e.target.value)}
                  placeholder="e.g. adanigroup.com..."
                  className="client-select-input"
                  style={{ width: "100%" }}
                />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "10px", borderTop: "1px solid var(--border-subtle)", paddingTop: "14px" }}>
              <button
                type="button"
                className="bento-action-btn"
                onClick={() => {
                  if (editing) cancelEditing();
                  else setMode("select");
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={async () => {
                  const savedClient = await saveConfig();
                  if (savedClient) {
                    setEditing(false);
                    setMode("select");
                  }
                }}
                disabled={saving || !idInput.trim()}
                className="bento-runner-btn-sweep"
                style={{ padding: "8px 20px", fontSize: "13px" }}
              >
                {saving ? "Saving…" : "💾 Save Organization"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ──────────────── 1. BENTO HERO COMMAND BAR ──────────────── */}
      <div className="bento-hero-bar">
        <div className="bento-hero-left">
          <div className="bento-hero-avatar">
            {activeClient
              ? (activeClient.name || activeClient.client_id).charAt(0).toUpperCase()
              : "🏢"}
          </div>

          <div className="bento-hero-info">
            {activeClient ? (
              <>
                <button
                  type="button"
                  className="bento-client-dropdown-trigger"
                  onClick={() => setGlobalSearchOpen(true)}
                  title="Click or press Ctrl+K to switch client"
                >
                  <span>{activeClient.name || activeClient.client_id}</span>
                  <span style={{ fontSize: "14px", color: "var(--text-dim)", marginTop: "2px" }}>▾</span>
                </button>

                <div className="bento-hero-meta-row">
                  <span className="bento-hero-pill">🆔 {activeClient.client_id}</span>
                  {activeClient.domain && (
                    <a
                      href={`https://${activeClient.domain.replace(/^https?:\/\//, "")}`}
                      target="_blank"
                      rel="noreferrer"
                      className="bento-hero-pill link"
                      title="Open website in new tab"
                    >
                      🌐 {activeClient.domain} ↗
                    </a>
                  )}
                  <span className="bento-hero-pill" style={{ color: "var(--success)", borderColor: "rgba(16, 185, 129, 0.3)" }}>
                    <span className="bento-session-indicator ready" /> Active Monitoring
                  </span>
                </div>
              </>
            ) : (
              <div style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-main)" }}>
                Select or Create a Client
              </div>
            )}
          </div>
        </div>

        <div className="bento-hero-actions">
          <button
            type="button"
            className="bento-action-btn"
            onClick={() => setGlobalSearchOpen(true)}
            title="Search & switch client (Ctrl+K)"
          >
            <span>🔍</span>
            <span>Switch Client</span>
            <span style={{ fontSize: "10px", opacity: 0.6, background: "rgba(255,255,255,0.08)", padding: "1px 5px", borderRadius: "4px" }}>Ctrl+K</span>
          </button>

          {activeClient && (
            <>
              <button
                type="button"
                className="bento-action-btn"
                onClick={() => setEditing(true)}
                title="Edit organization information"
              >
                <span>✏️</span>
                <span>Edit</span>
              </button>

              <button
                type="button"
                className="bento-action-btn"
                onClick={() => cloneClient(activeClient)}
                title="Duplicate configuration"
              >
                <span>📋</span>
                <span>Clone</span>
              </button>
            </>
          )}

          <button
            type="button"
            className="bento-action-btn primary"
            onClick={switchToCreate}
            title="Create a new client"
          >
            <span>➕</span>
            <span>New Client</span>
          </button>

          {activeClient && (
            <button
              type="button"
              className="bento-action-btn danger"
              onClick={handleDelete}
              disabled={deleting}
              title="Delete client organization"
            >
              <span>{deleting ? "Deleting…" : "🗑️"}</span>
            </button>
          )}
        </div>
      </div>

      {/* ──────────────── 2. BENTO GRID TILES ──────────────── */}
      {activeClient ? (
        <div className="bento-grid">
          {/* ⚡ TILE 1: MULTI-PLATFORM SCAN DECK (Span 7) */}
          <div className="bento-tile bento-span-7 bento-scan-deck">
            <div className="bento-tile-header">
              <div className="bento-tile-title-group">
                <div className="bento-tile-title">
                  <span>⚡ Multi-Platform Scan Deck</span>
                </div>
              </div>

              <div className="bento-status-subline">
                <span className="bento-session-indicator ready" />
                <span>
                  {platforms.filter((p) => p.session_state === "ready").length} of {platforms.length} platforms ready
                </span>
              </div>
            </div>

            <div className="bento-platform-chips-row">
              <button
                type="button"
                className={`bento-platform-chip ${targetPlatform === "" ? "selected" : ""}`}
                onClick={() => setTargetPlatform("")}
                disabled={busy || analysisBusy}
              >
                <span>🌐</span>
                <span>All Platforms</span>
              </button>
              {platforms.map((p) => {
                const isSelected = targetPlatform === p.platform;
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
                    className={`bento-platform-chip ${isSelected ? "selected" : ""}`}
                    onClick={() => setTargetPlatform(p.platform)}
                    disabled={busy || analysisBusy}
                    title={`${p.name} (Session: ${p.session_state})`}
                  >
                    <PlatformIcon platform={p.platform} size={15} />
                    <span>{p.name}</span>
                    <span className={`bento-session-indicator ${dotClass}`} />
                  </button>
                );
              })}
            </div>

            <div className="bento-runner-grid">
              <button
                type="button"
                className="bento-runner-btn-sweep"
                disabled={busy || !activeKeywordCount}
                onClick={handleSearch}
              >
                <span>{busy ? "⚡" : "🔍"}</span>
                <span>
                  {busy
                    ? "Discovery Sweep Running…"
                    : sweepPlatformName
                    ? `Sweep ${sweepPlatformName}`
                    : "Launch Discovery Sweep (All)"}
                </span>
              </button>

              <button
                type="button"
                className="bento-runner-btn-analysis"
                disabled={analysisBusy}
                onClick={handleRunAnalysis}
              >
                <span>{analysisBusy ? "⚙️" : "🔁"}</span>
                <span>
                  {analysisBusy
                    ? "Analysis Running…"
                    : analysisPlatformName
                    ? `Re-run Analysis (${analysisPlatformName})`
                    : "Re-run All Validated Analysis"}
                </span>
              </button>
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "11.5px", color: "var(--text-dim)", paddingTop: "4px" }}>
              <span>
                🎯 Active Target: <strong>{sweepPlatformName || "All 6 Platforms"}</strong> ({activeKeywordCount} keywords)
              </span>
              <span>⚡ High-speed parallel dispatch</span>
            </div>
          </div>

          {/* 👤 TILE 2: EXECUTIVE & INDIVIDUAL NAMES (Span 5) */}
          <div className="bento-tile bento-span-5">
            <div className="bento-tile-header">
              <div className="bento-tile-title-group">
                <div className="bento-tile-title">
                  <span>👤 Executive Names</span>
                </div>
                <span className="bento-tile-badge">{nameKeywords.length}</span>
              </div>

              <div style={{ display: "flex", gap: "6px" }}>
                <button
                  type="button"
                  onClick={saveConfig}
                  disabled={saving}
                  className="bento-action-btn primary"
                  style={{ padding: "4px 10px", fontSize: "11px" }}
                >
                  {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save"}
                </button>
              </div>
            </div>

            <ChipInput
              chips={nameKeywords}
              onAdd={(v) => setNameKeywords((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
              onRemove={(i) => setNameKeywords((prev) => prev.filter((_, idx) => idx !== i))}
              placeholder="Add executive name (e.g. Gautam Adani)..."
              disabled={busy}
            />
          </div>

          {/* 🎯 TILE 3: PLATFORM SCRAPING LIMITS (Span 7) */}
          <div className="bento-tile bento-span-7">
            <div className="bento-tile-header">
              <div className="bento-tile-title-group">
                <div className="bento-tile-title">
                  <span>🎯 Platform Scraping Guardrails & Limits</span>
                </div>
              </div>

              <button
                type="button"
                onClick={saveConfig}
                disabled={saving}
                className="bento-action-btn primary"
                style={{ padding: "4px 12px", fontSize: "11.5px" }}
              >
                {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save Limits"}
              </button>
            </div>

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

          {/* 🏷️ TILE 4: BRAND & DOMAIN KEYWORDS (Span 5) */}
          <div className="bento-tile bento-span-5">
            <div className="bento-tile-header">
              <div className="bento-tile-title-group">
                <div className="bento-tile-title">
                  <span>🏷️ Brand & Domain Terms</span>
                </div>
                <span className="bento-tile-badge">{domainKeywords.length}</span>
              </div>

              <button
                type="button"
                onClick={saveConfig}
                disabled={saving}
                className="bento-action-btn primary"
                style={{ padding: "4px 10px", fontSize: "11px" }}
              >
                {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save"}
              </button>
            </div>

            <ChipInput
              chips={domainKeywords}
              onAdd={(v) => setDomainKeywords((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
              onRemove={(i) => setDomainKeywords((prev) => prev.filter((_, idx) => idx !== i))}
              placeholder="Add brand domain keyword (e.g. adanigroup)..."
              disabled={busy}
            />

            {/* Asset Names Overrides Section */}
            <div style={{ borderTop: "1px solid rgba(255, 255, 255, 0.06)", paddingTop: "12px", marginTop: "6px" }}>
              <div style={{ fontSize: "12px", fontWeight: 700, color: "var(--text-main)", marginBottom: "6px", display: "flex", alignItems: "center", gap: "6px" }}>
                <span>🏷️ Target Asset Names</span>
                <span className="bento-tile-badge">{(assetNameIndividualKw.length + assetNameDomainKw.length)}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <ChipInput
                  chips={assetNameIndividualKw}
                  onAdd={(v) => setAssetNameIndividualKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemove={(i) => setAssetNameIndividualKw((prev) => prev.filter((_, idx) => idx !== i))}
                  placeholder="Target individual asset override..."
                  disabled={busy}
                />
                <ChipInput
                  chips={assetNameDomainKw}
                  onAdd={(v) => setAssetNameDomainKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemove={(i) => setAssetNameDomainKw((prev) => prev.filter((_, idx) => idx !== i))}
                  placeholder="Target domain asset override..."
                  disabled={busy}
                />
              </div>
            </div>
          </div>
        </div>
      ) : (
        /* NO CLIENT SELECTED STATE */
        <div className="bento-tile" style={{ textAlign: "center", padding: "60px 20px" }}>
          <div style={{ fontSize: "42px", marginBottom: "12px" }}>🏢</div>
          <div style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-main)", marginBottom: "6px" }}>
            No Client Selected
          </div>
          <div style={{ fontSize: "13px", color: "var(--text-dim)", marginBottom: "20px", maxWidth: "420px", margin: "0 auto 20px" }}>
            Select an existing organization from your workspace or create a new client to configure target keywords and launch threat sweeps.
          </div>
          <div style={{ display: "flex", justifyContent: "center", gap: "10px" }}>
            <button
              type="button"
              className="bento-action-btn"
              onClick={() => setGlobalSearchOpen(true)}
            >
              <span>🔍 Select Existing Client</span>
            </button>
            <button
              type="button"
              className="bento-action-btn primary"
              onClick={switchToCreate}
            >
              <span>➕ Create New Client</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
