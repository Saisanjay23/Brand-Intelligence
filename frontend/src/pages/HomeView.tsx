import { useCallback, useEffect, useState } from "react";
import { analysisApi } from "../api/analysisApi";
import { clientsApi } from "../api/clientsApi";
import { discoveryApi } from "../api/discoveryApi";
import { jobsApi } from "../api/jobsApi";
import type { Client, Job, PlatformHealth } from "../api/types";
import { PlatformIcon } from "../components/PlatformIcon";

type KeywordTab = "names" | "domain" | "assetNames";
type Mode = "create" | "select";

interface Props {
  clientId: string;
  clientName: string;
  platforms: PlatformHealth[];
  onClient: (clientId: string, name: string) => void;
  // removes a client from the browser's local "recently used" cache -- must
  // be called on delete, or the deleted client keeps reappearing in the
  // header's dropdown even though it's gone from the database.
  onForgetClient: (clientId: string) => void;
  busy: boolean;
  analysisBusy: boolean;
  onJobs: (jobs: Job[]) => void;
  onError: (m: string) => void;
}

// Splits on commas AND newlines (an analyst pasting a list from a
// spreadsheet or doc could use either, or both at once), trims each
// piece, and drops anything blank -- shared by the single-line input's
// paste handler and the bulk-add textarea below.
function splitKeywordList(raw: string): string[] {
  return raw
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// Facebook/Instagram/etc. search is not case-sensitive -- "adani" and
// "Adani" return identical results -- so treating them as two distinct
// keywords doubles the sweep for zero extra coverage, and shows up in the
// UI as an inexplicable "I only added 2 keywords, why are there 3"
// (exactly what this fixes: the chip lists used to dedup with an exact,
// case-sensitive `.includes(v)`, so re-typing an existing keyword with
// different casing silently added a functional duplicate instead of being
// rejected). Keeps whichever casing was added FIRST; a later duplicate in
// any other casing is dropped, not merged/renamed.
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
      onAdd(trimmed);
      setInput("");
    }
  };

  const commitBulk = () => {
    for (const kw of splitKeywordList(bulkText)) onAdd(kw);
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
            // only intercept a paste that actually looks like a list --
            // a single word/name should still land in the input normally,
            // editable before Enter, not get auto-committed
            if (/[,\n]/.test(text)) {
              e.preventDefault();
              for (const kw of splitKeywordList(text)) onAdd(kw);
            }
          }}
          onBlur={commit}
          placeholder={placeholder}
          className="chip-input"
          disabled={disabled}
        />
      </div>
      <button
        type="button"
        className="bulk-kw-toggle"
        onClick={() => setBulkOpen((v) => !v)}
        disabled={disabled}
      >
        {bulkOpen ? "▾" : "▸"} 📋 Bulk add (comma or line separated)
      </button>
      {bulkOpen && (
        <div className="bulk-kw-panel">
          <textarea
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"one per line, or comma-separated -- e.g.\ngautam adani\nkaran adani, jeet adani"}
            rows={4}
            disabled={disabled}
          />
          <button type="button" className="btn-cyber-primary" style={{ width: "auto", marginTop: "6px" }} onClick={commitBulk} disabled={disabled || !bulkText.trim()}>
            Add All
          </button>
        </div>
      )}
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
  return (
    <div style={{ marginTop: "20px" }}>
      <label className="field-label">🗂️ Config Keywords</label>
      <div className="kw-tab-row">
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
          {(assetNameIndividualKw.length + assetNameDomainKw.length) > 0 && <span className="kw-tab-count">{assetNameIndividualKw.length + assetNameDomainKw.length}</span>}
        </button>
      </div>
      {/* All panels stay mounted regardless of which tab is active --
          only hidden via CSS, not unmounted -- so switching tabs mid-edit
          never discards an in-progress bulk-paste textarea or the
          single-keyword input's partially-typed text. */}
      <div style={{ display: activeTab === "names" ? "block" : "none" }}>
        <ChipInput
          chips={nameKeywords}
          onAdd={onAddName}
          onRemove={onRemoveName}
          placeholder="type a person's name, press Enter…"
          disabled={disabled}
        />
      </div>
      <div style={{ display: activeTab === "domain" ? "block" : "none" }}>
        <ChipInput
          chips={domainKeywords}
          onAdd={onAddDomain}
          onRemove={onRemoveDomain}
          placeholder="type a brand/domain keyword, press Enter…"
          disabled={disabled}
        />
      </div>
      <div style={{ display: activeTab === "assetNames" ? "block" : "none" }}>
        <div style={{ marginTop: "12px", fontSize: "12px", color: "var(--text-dim)" }}>
          Asset Name choices for the analysis view dropdown.
        </div>
        <div style={{ display: "flex", gap: "20px", marginTop: "12px" }}>
          <div style={{ flex: 1 }}>
            <label className="field-label">Individual Names</label>
            <ChipInput
              chips={assetNameIndividualKw}
              onAdd={onAddAssetIndividual}
              onRemove={onRemoveAssetIndividual}
              placeholder="asset name for individuals…"
              disabled={disabled}
            />
          </div>
          <div style={{ flex: 1 }}>
            <label className="field-label">Domain Names</label>
            <ChipInput
              chips={assetNameDomainKw}
              onAdd={onAddAssetDomain}
              onRemove={onRemoveAssetDomain}
              placeholder="asset name for domains…"
              disabled={disabled}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function PlatformLimitsEditor({
  platforms,
  limits,
  onChange,
  facebookTabLimits,
  onFacebookTabChange,
  disabled,
}: {
  platforms: PlatformHealth[];
  limits: Record<string, string>;
  onChange: (platform: string, value: string) => void;
  facebookTabLimits: { people: string; pages: string };
  onFacebookTabChange: (tab: "people" | "pages", value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ marginTop: "20px" }}>
      <label className="field-label">🎯 Per-Platform Scrape Limits</label>
      <div style={{ fontSize: "11px", color: "var(--text-dim)", marginTop: "3px", marginBottom: "8px" }}>
        Leave a platform (or Facebook's People/Pages) blank to scrape everything found for it. Set a number to cap
        results per sweep.
      </div>
      <div className="platform-limits-grid">
        {platforms.map((p) =>
          p.platform === "facebook" ? (
            <div key={p.platform} className="platform-limit-row platform-limit-row-split">
              <div className="platform-limit-label">
                <PlatformIcon platform={p.platform} size={16} />
                <span>{p.name}</span>
              </div>
              <div className="platform-limit-split-inputs">
                <input
                  type="number"
                  min={0}
                  value={facebookTabLimits.people}
                  onChange={(e) => onFacebookTabChange("people", e.target.value)}
                  placeholder="People: All"
                  title="Cap for Facebook People results"
                  disabled={disabled}
                  className="platform-limit-input"
                />
                <input
                  type="number"
                  min={0}
                  value={facebookTabLimits.pages}
                  onChange={(e) => onFacebookTabChange("pages", e.target.value)}
                  placeholder="Pages: All"
                  title="Cap for Facebook Pages results"
                  disabled={disabled}
                  className="platform-limit-input"
                />
              </div>
            </div>
          ) : (
            <div key={p.platform} className="platform-limit-row">
              <div className="platform-limit-label">
                <PlatformIcon platform={p.platform} size={16} />
                <span>{p.name}</span>
              </div>
              <input
                type="number"
                min={0}
                value={limits[p.platform] ?? ""}
                onChange={(e) => onChange(p.platform, e.target.value)}
                placeholder="Scrape All"
                disabled={disabled}
                className="platform-limit-input"
              />
            </div>
          ),
        )}
        {!platforms.length && (
          <div style={{ fontSize: "12px", color: "var(--text-dim)" }}>No platforms registered yet.</div>
        )}
      </div>
    </div>
  );
}

const EMPTY_FORM = { id: "", name: "", domain: "", nameKw: [] as string[], domainKw: [] as string[], cron: "" };

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function parseCronSchedule(cron: string): { mode: "none" | "daily" | "weekly" | "custom"; hour: number; weekday: number } {
  const trimmed = cron.trim();
  if (!trimmed) return { mode: "none", hour: 2, weekday: 0 };
  const daily = trimmed.match(/^0 (\d{1,2}) \* \* \*$/);
  if (daily) return { mode: "daily", hour: Number(daily[1]), weekday: 0 };
  const weekly = trimmed.match(/^0 (\d{1,2}) \* \* (\d)$/);
  if (weekly) return { mode: "weekly", hour: Number(weekly[1]), weekday: Number(weekly[2]) };
  return { mode: "custom", hour: 2, weekday: 0 };
}

function buildCronSchedule(mode: "daily" | "weekly", hour: number, weekday: number): string {
  return mode === "daily" ? `0 ${hour} * * *` : `0 ${hour} * * ${weekday}`;
}

export function HomeView({ clientId, platforms, onClient, onForgetClient, busy, analysisBusy, onJobs, onError }: Props) {
  const [clients, setClients] = useState<Client[]>([]);
  const [loadingClients, setLoadingClients] = useState(true);
  const [mode, setMode] = useState<Mode>(clientId ? "select" : "create");
  const [editing, setEditing] = useState(false);

  const [activeClient, setActiveClient] = useState<Client | null>(null);

  const [idInput, setIdInput] = useState(EMPTY_FORM.id);
  const [nameInput, setNameInput] = useState(EMPTY_FORM.name);
  const [domainInput, setDomainInput] = useState(EMPTY_FORM.domain);
  const [logoUrlInput, setLogoUrlInput] = useState("");
  const [nameKeywords, setNameKeywords] = useState<string[]>(EMPTY_FORM.nameKw);
  const [domainKeywords, setDomainKeywords] = useState<string[]>(EMPTY_FORM.domainKw);
  const [assetNameIndividualKw, setAssetNameIndividualKw] = useState<string[]>([]);
  const [assetNameDomainKw, setAssetNameDomainKw] = useState<string[]>([]);
  const [platformLimits, setPlatformLimits] = useState<Record<string, string>>({});
  const [facebookTabLimits, setFacebookTabLimits] = useState<{ people: string; pages: string }>({
    people: "",
    pages: "",
  });
  const [cron, setCron] = useState(EMPTY_FORM.cron);
  const [activeTab, setActiveTab] = useState<KeywordTab>("names");

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Platform scope for the Sweep and Re-run Analysis actions -- "" is the
  // "All Platforms" choice (the previous, only, behavior: every ready
  // platform swept/analysed in one job). Set to one platform id to scope
  // that single run to just that platform; every other platform is left
  // untouched, and its own session doesn't need to be ready. Kept as two
  // separate selections since an analyst commonly wants to sweep one
  // platform right after fixing its session while leaving the others on
  // their normal "All Platforms" cadence, and re-run analysis for a
  // different one entirely.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadIntoForm = (c: Client) => {
    setIdInput(c.client_id);
    setNameInput(c.name);
    setDomainInput(c.domain || "");
    setLogoUrlInput(c.logo_url || "");
    setNameKeywords(c.name_keywords || []);
    setDomainKeywords(c.domain_keywords || []);
    setAssetNameIndividualKw(c.asset_name_individual_keywords || []);
    setAssetNameDomainKw(c.asset_name_domain_keywords || []);
    setPlatformLimits(
      Object.fromEntries(Object.entries(c.platform_limits || {}).map(([k, v]) => [k, String(v)])),
    );
    const fbTabs = c.platform_tab_limits?.facebook || {};
    setFacebookTabLimits({
      people: fbTabs.people !== undefined ? String(fbTabs.people) : "",
      pages: fbTabs.pages !== undefined ? String(fbTabs.pages) : "",
    });
    setCron(c.cron || "");
  };

  const clearForm = () => {
    setIdInput(EMPTY_FORM.id);
    setNameInput(EMPTY_FORM.name);
    setDomainInput(EMPTY_FORM.domain);
    setLogoUrlInput("");
    setNameKeywords(EMPTY_FORM.nameKw);
    setDomainKeywords(EMPTY_FORM.domainKw);
    setAssetNameIndividualKw([]);
    setAssetNameDomainKw([]);
    setPlatformLimits({});
    setFacebookTabLimits({ people: "", pages: "" });
    setCron(EMPTY_FORM.cron);
  };

  // Pick up an already-selected client (e.g. restored from the header's
  // recent-clients list) once the server-side list has loaded.
  useEffect(() => {
    if (!clientId || activeClient || !clients.length) return;
    const existing = clients.find((c) => c.client_id === clientId);
    if (existing) {
      setActiveClient(existing);
      loadIntoForm(existing);
      setMode("select");
      setEditing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, clients]);

  const switchToCreate = () => {
    setMode("create");
    setActiveClient(null);
    setEditing(false);
    clearForm();
    setSweepPlatform("");
    setAnalysisPlatform("");
  };

  const selectSavedClient = (id: string) => {
    // a platform scope picked for a different client must not silently
    // carry over -- "sweep only Telegram" meant for client A should never
    // fire against client B just because the selector still held that value
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
    setEditing(false);
    onClient(c.client_id, c.name);
  };

  const startEditing = () => {
    if (!activeClient) return;
    loadIntoForm(activeClient);
    setEditing(true);
  };

  const cancelEditing = () => {
    if (activeClient) loadIntoForm(activeClient);
    setEditing(false);
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
      const parsedLimits: Record<string, number> = {};
      for (const [platform, raw] of Object.entries(platformLimits)) {
        const n = Number(raw);
        if (raw.trim() && Number.isFinite(n) && n > 0) parsedLimits[platform] = Math.floor(n);
      }
      const fbTabLimits: Record<string, number> = {};
      for (const [tab, raw] of Object.entries(facebookTabLimits)) {
        const n = Number(raw);
        if (raw.trim() && Number.isFinite(n) && n > 0) fbTabLimits[tab] = Math.floor(n);
      }
      const client = await clientsApi.upsertClient({
        client_id: id,
        name,
        domain: domainInput.trim(),
        logo_url: logoUrlInput.trim(),
        name_keywords: nameKeywords,
        domain_keywords: domainKeywords,
        asset_name_individual_keywords: assetNameIndividualKw,
        asset_name_domain_keywords: assetNameDomainKw,
        platform_limits: parsedLimits,
        platform_tab_limits: Object.keys(fbTabLimits).length ? { facebook: fbTabLimits } : {},
        cron: cron.trim() || null,
      });
      setActiveClient(client);
      setMode("select");
      setEditing(false);
      onClient(client.client_id, client.name);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
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
      onError("This client has no keywords yet — click Edit to add individual-name or domain keywords first.");
      return;
    }
    try {
      const { job_id } = await discoveryApi.discover({
        client_id: activeClient.client_id,
        // case-insensitive dedup here too, not just on add (see
        // dedupeKeywordsCaseInsensitive above) -- this covers an already-
        // affected saved client (like this one) immediately, without
        // requiring the analyst to first go edit and remove the duplicate
        // chip by hand, AND it catches the same literal keyword existing
        // in both the name and domain lists (a real, if less common,
        // second way to end up sweeping the same term twice).
        keywords: dedupeKeywordsCaseInsensitive([
          ...(activeClient.name_keywords || []),
          ...(activeClient.domain_keywords || []),
        ]),
        // "" -> omitted -> every ready platform (unchanged default);
        // a specific id scopes the sweep to just that one platform
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
    // Always a FORCED re-run (force: true): without it, clicking this
    // button after the auto-trigger-on-approve (or the 20-minute catch-up
    // sweep) had already cleared the normal backlog to zero did nothing at
    // all -- the job would immediately report "nothing to analyse, already
    // up to date" -- which read as the button being broken. force=true
    // re-scrapes every currently-approved profile for this client
    // regardless of whether an earlier run already scored it, so an
    // explicit click here always does real work as long as anything is
    // approved. Confirmed first since it means visiting every one of them
    // again, not a free action.
    const scope = analysisPlatformName ? `on ${analysisPlatformName}` : "across every ready platform";
    if (
      !window.confirm(
        `Re-run analysis for every validated profile of "${activeClient.name || activeClient.client_id}" ${scope}, including ones already analysed? This re-scrapes each one again.`,
      )
    ) {
      return;
    }
    try {
      const { job_id } = await analysisApi.analyse({
        client_id: activeClient.client_id,
        force: true,
        // "" -> omitted -> every ready platform; a specific id scopes the
        // re-run to just that one platform
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
    if (
      !window.confirm(
        `Delete client "${activeClient.name || activeClient.client_id}"? This permanently removes ALL of its profiles and incidents from the database.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      await clientsApi.deleteClient(activeClient.client_id);
      onForgetClient(activeClient.client_id);
      setActiveClient(null);
      setEditing(false);
      clearForm();
      refreshClients();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const showForm = mode === "create" || (mode === "select" && activeClient && editing);

  return (
    <div className="home-container">
      <div className="home-card">
        <div className="mode-tab-row">
          <button className={`mode-tab-btn ${mode === "create" ? "active" : ""}`} onClick={switchToCreate}>
            ➕ Create Client
          </button>
          <button
            className={`mode-tab-btn ${mode === "select" ? "active" : ""}`}
            onClick={() => {
              if (!activeClient && clientId) {
                const existing = clients.find((c) => c.client_id === clientId);
                if (existing) {
                  setActiveClient(existing);
                  loadIntoForm(existing);
                  setEditing(false);
                }
              }
              setMode("select");
            }}
          >
            📂 Select Saved Client
          </button>
        </div>

        {mode === "select" && (
          <div style={{ marginTop: "18px" }}>
            <label className="field-label">🔎 Saved Clients</label>
            <select
              className="client-select-input"
              style={{ marginTop: "7px", width: "100%" }}
              value={activeClient?.client_id || ""}
              onChange={(e) => selectSavedClient(e.target.value)}
              disabled={loadingClients}
            >
              <option value="">
                {loadingClients ? "Loading clients…" : clients.length ? "— choose a client —" : "No saved clients yet"}
              </option>
              {clients.map((c) => (
                <option key={c.client_id} value={c.client_id}>
                  {(c.name || c.client_id) + ` (${c.client_id})`}
                </option>
              ))}
            </select>
            {!loadingClients && !clients.length && (
              <div style={{ fontSize: "12px", color: "var(--text-dim)", marginTop: "8px" }}>
                Nothing saved yet — switch to <strong>Create Client</strong> to add your first one.
              </div>
            )}
          </div>
        )}

        {/* Read-only summary + run actions -- shown the moment a client is
            selected. Editable fields only appear after an explicit "Edit". */}
        {mode === "select" && activeClient && !editing && (
          <div className="active-client-panel">
            <div className="client-summary-card">
              <div className="client-summary-head">
                <span className="client-avatar-lg">{(activeClient.name || activeClient.client_id).charAt(0).toUpperCase()}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="client-summary-name">{activeClient.name || activeClient.client_id}</div>
                  <div className="client-summary-id">🆔 {activeClient.client_id}</div>
                </div>
                <span className="status-dot-badge">
                  <span className="status-dot" /> Active
                </span>
                <button className="icon-edit-btn" onClick={startEditing} title="Edit this client's details and keywords">
                  ✏️ Edit
                </button>
              </div>

              <div className="client-summary-meta">
                <span className="meta-chip">🌐 {activeClient.domain || "no domain set"}</span>
                <span className="meta-chip">👤 {activeClient.name_keywords?.length || 0} names</span>
                <span className="meta-chip">🏷️ {activeClient.domain_keywords?.length || 0} domain kw</span>
                <span className="meta-chip">
                  🎯{" "}
                  {Object.keys(activeClient.platform_limits || {}).length
                    ? `${Object.keys(activeClient.platform_limits).length} platform cap(s)`
                    : "scrape all platforms"}
                </span>
                {activeClient.cron && <span className="meta-chip">⏱️ {activeClient.cron}</span>}
              </div>
            </div>

            {/* "All Platforms" (blank) is the default and previous-only
                behavior -- every ready platform swept in one job. Picking
                one platform here scopes JUST this run to it; every other
                platform is left untouched and doesn't need its own session
                to be ready. Independent of the Analysis selector below --
                an analyst commonly wants to fix and re-sweep one platform's
                session without touching the others' normal cadence. */}
            <div style={{ marginBottom: "8px" }}>
              <label className="field-label" style={{ fontSize: "11px" }}>
                🎯 Sweep Platform
              </label>
              <select
                className="client-select-input"
                style={{ marginTop: "5px", width: "100%" }}
                value={sweepPlatform}
                onChange={(e) => setSweepPlatform(e.target.value)}
                disabled={busy}
                title="Which platform(s) Search This Client sweeps"
              >
                <option value="">🌐 All Platforms</option>
                {platforms.map((p) => (
                  <option key={p.platform} value={p.platform}>
                    {p.name}
                    {p.session_state !== "ready" ? ` (${p.session_state})` : ""}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn-cyber-primary"
              disabled={busy || !activeKeywordCount}
              onClick={handleSearch}
              title={
                sweepPlatformName
                  ? `Sweeps ONLY ${sweepPlatformName} for this client's combined name + domain keywords`
                  : "Sweeps every ready platform for this client's combined name + domain keywords"
              }
            >
              {busy
                ? "⚡ Discovery Sweep Running…"
                : sweepPlatformName
                  ? `🔍 Search This Client (${sweepPlatformName})`
                  : "🔍 Search This Client"}
            </button>

            <div style={{ marginTop: "12px", marginBottom: "8px" }}>
              <label className="field-label" style={{ fontSize: "11px" }}>
                🎯 Analysis Platform
              </label>
              <select
                className="client-select-input"
                style={{ marginTop: "5px", width: "100%" }}
                value={analysisPlatform}
                onChange={(e) => setAnalysisPlatform(e.target.value)}
                disabled={analysisBusy}
                title="Which platform(s) Re-run Analysis re-analyses"
              >
                <option value="">🌐 All Platforms</option>
                {platforms.map((p) => (
                  <option key={p.platform} value={p.platform}>
                    {p.name}
                    {p.session_state !== "ready" ? ` (${p.session_state})` : ""}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn-secondary-action"
              disabled={analysisBusy}
              onClick={handleRunAnalysis}
              title={
                analysisPlatformName
                  ? `Re-analyses EVERY validated profile on ${analysisPlatformName} only, including ones already analysed -- always does a fresh pass`
                  : "Re-analyses EVERY validated profile for this client across every ready platform, including ones already analysed -- always does a fresh pass, not just a catch-up on what's new"
              }
            >
              {analysisBusy
                ? "🧪 Analysis Running…"
                : analysisPlatformName
                  ? `🔁 Re-run Analysis (${analysisPlatformName})`
                  : "🔁 Re-run Analysis (All Validated)"}
            </button>

            <div style={{ marginTop: "14px", textAlign: "right" }}>
              <button onClick={handleDelete} disabled={deleting} title="Permanently deletes this client and cascades to all of its profiles + incidents" className="danger-link-btn">
                {deleting ? "Deleting…" : "🗑️ Delete Client & All Its Data"}
              </button>
            </div>
          </div>
        )}

        {showForm && (
          <>
            <div style={{ marginTop: "20px", paddingTop: "18px", borderTop: "1px solid var(--border-subtle)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <label className="field-label">{editing ? `✏️ Editing "${activeClient!.name}"` : "🆕 New Client Details"}</label>
                {editing && (
                  <button className="text-link-btn" onClick={cancelEditing}>
                    ✕ Cancel
                  </button>
                )}
              </div>
              <div className="client-setup-box" style={{ flexWrap: "wrap" }}>
                <input
                  value={idInput}
                  onChange={(e) => setIdInput(e.target.value)}
                  placeholder="🆔 org id (unique, e.g. acme-corp)…"
                  disabled={editing}
                  className="client-select-input"
                  style={{ opacity: editing ? 0.6 : 1 }}
                />
                <input
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  placeholder="🏢 org / client name…"
                  className="client-select-input"
                />
                <input
                  value={domainInput}
                  onChange={(e) => setDomainInput(e.target.value)}
                  placeholder="🌐 domain, e.g. xyz.com…"
                  className="client-select-input"
                />
                <input
                  value={logoUrlInput}
                  onChange={(e) => setLogoUrlInput(e.target.value)}
                  placeholder="🖼️ real brand logo URL (optional) — shown side-by-side during analysis review…"
                  className="client-select-input"
                  title="Shown next to a discovered profile's avatar during triage, so you don't have to open a separate tab to compare"
                />
              </div>
            </div>

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

            <PlatformLimitsEditor
              platforms={platforms}
              limits={platformLimits}
              onChange={(platform, value) => setPlatformLimits((prev) => ({ ...prev, [platform]: value }))}
              facebookTabLimits={facebookTabLimits}
              onFacebookTabChange={(tab, value) => setFacebookTabLimits((prev) => ({ ...prev, [tab]: value }))}
              disabled={busy}
            />

            <div style={{ marginTop: "20px" }}>
              <label className="field-label">⏱️ Recurring Schedule (optional)</label>
              {(() => {
                const parsed = parseCronSchedule(cron);
                const selectStyle = {
                  background: "var(--bg-inner)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "10px",
                  padding: "10px 12px",
                  color: "var(--text-main)",
                  fontSize: "12px",
                  outline: "none",
                } as const;
                return (
                  <div style={{ marginTop: "7px", display: "flex", flexDirection: "column", gap: "8px" }}>
                    <select
                      value={parsed.mode}
                      onChange={(e) => {
                        const mode = e.target.value as "none" | "daily" | "weekly" | "custom";
                        if (mode === "none") setCron("");
                        else if (mode === "custom") setCron(cron.trim() || "0 2 * * *");
                        else setCron(buildCronSchedule(mode, parsed.hour, parsed.weekday));
                      }}
                      style={{ ...selectStyle, width: "100%" }}
                    >
                      <option value="none">No recurring schedule</option>
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                      <option value="custom">Custom (cron expression)</option>
                    </select>

                    {(parsed.mode === "daily" || parsed.mode === "weekly") && (
                      <div style={{ display: "flex", gap: "8px" }}>
                        {parsed.mode === "weekly" && (
                          <select
                            value={parsed.weekday}
                            onChange={(e) => setCron(buildCronSchedule("weekly", parsed.hour, Number(e.target.value)))}
                            style={{ ...selectStyle, flex: 1 }}
                          >
                            {WEEKDAYS.map((d, i) => (
                              <option key={d} value={i}>{d}</option>
                            ))}
                          </select>
                        )}
                        <select
                          value={parsed.hour}
                          onChange={(e) => setCron(buildCronSchedule(parsed.mode as "daily" | "weekly", Number(e.target.value), parsed.weekday))}
                          style={{ ...selectStyle, flex: 1 }}
                        >
                          {Array.from({ length: 24 }, (_, h) => (
                            <option key={h} value={h}>{`${String(h).padStart(2, "0")}:00`}</option>
                          ))}
                        </select>
                      </div>
                    )}

                    {parsed.mode === "custom" && (
                      <input
                        value={cron}
                        onChange={(e) => setCron(e.target.value)}
                        placeholder="cron expression, e.g. 0 2 * * * — blank disables"
                        style={{ ...selectStyle, width: "100%", fontFamily: "var(--font-mono)" }}
                      />
                    )}
                  </div>
                );
              })()}
            </div>

            <button
              onClick={saveConfig}
              disabled={saving || !idInput.trim()}
              className="btn-cyber-primary"
              style={{
                marginTop: "18px",
                background: "linear-gradient(135deg, var(--cyan), var(--purple))",
              }}
            >
              {saving ? "Saving…" : saved ? "✓ Saved" : editing ? "💾 Save Changes" : "💾 Create Client"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
