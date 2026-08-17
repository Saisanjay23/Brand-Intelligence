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
import { DiscoverIcon, AnalyseIcon, CyberGlobeIcon, StopIcon } from "../components/AppIcons";

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
  onStopDiscovery,
  onStopAnalysis,
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
            <span>🏢</span>
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
            <span>➕</span> New
          </button>
        </div>

        <div className="client-search-box">
          <span className="client-search-icon">🔍</span>
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
                <span>✨ Create New Client</span>
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
                  placeholder="🆔 org id (unique slug, e.g. acme-corp)…"
                  className="client-select-input"
                />
                <input
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  placeholder="🏢 organization / client display name…"
                  className="client-select-input"
                />
                <input
                  value={domainInput}
                  onChange={(e) => setDomainInput(e.target.value)}
                  placeholder="🌐 official website domain (e.g. acme.com)…"
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
                  onAddName={(v) => setNameKeywords((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveName={(i) => setNameKeywords((prev) => prev.filter((_, idx) => idx !== i))}
                  onAddDomain={(v) => setDomainKeywords((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveDomain={(i) => setDomainKeywords((prev) => prev.filter((_, idx) => idx !== i))}
                  assetNameIndividualKw={assetNameIndividualKw}
                  assetNameDomainKw={assetNameDomainKw}
                  onAddAssetIndividual={(v) => setAssetNameIndividualKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetIndividual={(i) => setAssetNameIndividualKw((prev) => prev.filter((_, idx) => idx !== i))}
                  onAddAssetDomain={(v) => setAssetNameDomainKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetDomain={(i) => setAssetNameDomainKw((prev) => prev.filter((_, idx) => idx !== i))}
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
              style={{ marginTop: "16px" }}
            >
              {saving ? "Creating Client…" : "💾 Save & Create Client"}
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
            <div style={{ fontSize: "42px" }}>🏢</div>
            <div style={{ fontSize: "18px", fontWeight: 800, color: "var(--text-main)" }}>
              Select or Create a Client
            </div>
            <div style={{ fontSize: "13px", color: "var(--text-dim)", maxWidth: "420px" }}>
              Choose a client from the sidebar directory on the left or click <strong>+ New</strong> to set up monitoring for a new brand.
            </div>
            <button
              type="button"
              className="btn-cyber-primary"
              style={{ width: "auto", padding: "10px 24px", marginTop: "12px" }}
              onClick={switchToCreate}
            >
              ➕ Create New Client
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
                    <span className="client-hero-id">🆔 {activeClient.client_id}</span>
                    {activeClient.domain && (
                      <span className="client-hero-domain">🌐 {activeClient.domain}</span>
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
                >
                  ✏️ Edit
                </button>
                <button
                  type="button"
                  className="client-hero-btn"
                  onClick={() => cloneClient(activeClient)}
                  title="Duplicate configuration"
                >
                  📋 Clone
                </button>
                <button
                  type="button"
                  className="client-hero-btn danger"
                  onClick={handleDelete}
                  disabled={deleting}
                  title="Permanently delete client"
                >
                  {deleting ? "Deleting…" : "🗑️"}
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
                <span>⚡</span>
                <span>Run & Overview</span>
              </button>

              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "keywords" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("keywords")}
              >
                <span>🏷️</span>
                <span>Keywords & Assets</span>
                <span className="workspace-tab-counter">{activeKeywordCount}</span>
              </button>

              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "limits" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("limits")}
              >
                <span>🎯</span>
                <span>Scraping Limits</span>
              </button>

              <button
                type="button"
                className={`client-workspace-tab-btn ${activeWorkspaceTab === "settings" ? "active" : ""}`}
                onClick={() => setActiveWorkspaceTab("settings")}
              >
                <span>⚙️</span>
                <span>Client Settings</span>
              </button>
            </div>

            {/* TAB CONTENT 1: RUN & OVERVIEW */}
            {activeWorkspaceTab === "overview" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                {/* UNIFIED COMMAND RUNNER */}
                <div className="unified-runner-card">
                  <div className="runner-header">
                    <div>
                      <div className="runner-title">
                        <span>⚡ Platform Target & Run Hub</span>
                      </div>
                      <div className="runner-subtitle">
                        Select target platform to execute discovery sweep or re-run deep analysis.
                      </div>
                    </div>
                  </div>

                  <div className="unified-platform-selector">
                    <button
                      type="button"
                      className={`unified-platform-btn ${targetPlatform === "" ? "active" : ""}`}
                      onClick={() => setTargetPlatform("")}
                      disabled={busy || analysisBusy}
                    >
                      <CyberGlobeIcon size={15} color={targetPlatform === "" ? "#7C5CFF" : "#94A3B8"} />
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
                          className={`unified-platform-btn ${targetPlatform === p.platform ? "active" : ""}`}
                          onClick={() => setTargetPlatform(p.platform)}
                          disabled={busy || analysisBusy}
                          title={`Platform: ${p.name} (Session: ${p.session_state})`}
                        >
                          <PlatformIcon platform={p.platform} size={15} />
                          <span>{p.name}</span>
                          <span className={`runner-session-dot ${dotClass}`} />
                        </button>
                      );
                    })}
                  </div>

                  <div className="runner-actions-grid">
                    {busy ? (
                      <button
                        type="button"
                        className="runner-btn-stop"
                        onClick={onStopDiscovery}
                        title="Stop running discovery sweep"
                      >
                        <StopIcon size={17} color="#fff" />
                        <span>Stop Discovery</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="runner-btn-primary"
                        disabled={!activeKeywordCount}
                        onClick={handleSearch}
                      >
                        <DiscoverIcon size={17} color="#fff" />
                        <span>
                          {sweepPlatformName
                            ? `Discover (${sweepPlatformName})`
                            : "Discover"}
                        </span>
                      </button>
                    )}

                    {analysisBusy ? (
                      <button
                        type="button"
                        className="runner-btn-stop"
                        onClick={onStopAnalysis}
                        title="Stop running analysis"
                      >
                        <StopIcon size={17} color="#fff" />
                        <span>Stop Analysis</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="runner-btn-secondary"
                        onClick={handleRunAnalysis}
                      >
                        <AnalyseIcon size={17} color="#00F0FF" />
                        <span>
                          {analysisPlatformName
                            ? `Analyse (${analysisPlatformName})`
                            : "Analyse"}
                        </span>
                      </button>
                    )}
                  </div>
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
                  onAddName={(v) => setNameKeywords((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveName={(i) => setNameKeywords((prev) => prev.filter((_, idx) => idx !== i))}
                  onAddDomain={(v) => setDomainKeywords((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveDomain={(i) => setDomainKeywords((prev) => prev.filter((_, idx) => idx !== i))}
                  assetNameIndividualKw={assetNameIndividualKw}
                  assetNameDomainKw={assetNameDomainKw}
                  onAddAssetIndividual={(v) => setAssetNameIndividualKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetIndividual={(i) => setAssetNameIndividualKw((prev) => prev.filter((_, idx) => idx !== i))}
                  onAddAssetDomain={(v) => setAssetNameDomainKw((prev) => (prev.some((k) => k.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]))}
                  onRemoveAssetDomain={(i) => setAssetNameDomainKw((prev) => prev.filter((_, idx) => idx !== i))}
                  disabled={busy}
                />

                <div style={{ marginTop: "20px", display: "flex", justifyContent: "flex-end" }}>
                  <button
                    onClick={saveConfig}
                    disabled={saving}
                    className="btn-cyber-primary"
                    style={{ width: "auto", padding: "10px 24px", margin: 0 }}
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save Keyword Changes"}
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
                    style={{ width: "auto", padding: "10px 24px", margin: 0 }}
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save Scrape Limits"}
                  </button>
                </div>
              </div>
            )}

            {/* TAB CONTENT 4: SETTINGS */}
            {activeWorkspaceTab === "settings" && (
              <div className="dashboard-card-box" style={{ background: "var(--bg-card)", padding: "24px", display: "flex", flexDirection: "column", gap: "20px" }}>
                <div>
                  <div style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-main)", marginBottom: "4px" }}>
                    🏢 Client Information
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "12px" }}>
                    Update organization display name, associated domain, and identifier.
                  </div>
                  <div className="client-setup-box" style={{ flexWrap: "wrap", margin: 0 }}>
                    <input
                      value={idInput}
                      onChange={(e) => setIdInput(e.target.value)}
                      placeholder="🆔 org id…"
                      disabled={true}
                      className="client-select-input"
                      style={{ opacity: 0.6 }}
                      title="Organization ID cannot be modified after creation"
                    />
                    <input
                      value={nameInput}
                      onChange={(e) => setNameInput(e.target.value)}
                      placeholder="🏢 organization name…"
                      className="client-select-input"
                    />
                    <input
                      value={domainInput}
                      onChange={(e) => setDomainInput(e.target.value)}
                      placeholder="🌐 domain, e.g. xyz.com…"
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
                    style={{ width: "auto", padding: "10px 24px", margin: 0 }}
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save Changes"}
                  </button>
                </div>

                <div style={{ borderTop: "1px solid rgba(239, 68, 68, 0.2)", paddingTop: "18px", marginTop: "10px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--danger)", marginBottom: "4px" }}>
                    ⚠️ Danger Zone
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "12px" }}>
                    Permanently delete this organization and cascade remove all associated discovery hits, validated profiles, and incident tickets.
                  </div>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="danger-link-btn"
                  >
                    {deleting ? "Deleting Organization…" : "🗑️ Delete Organization & All Associated Data"}
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
