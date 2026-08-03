import { useCallback, useEffect, useState } from "react";
import { analysisApi } from "../api/analysisApi";
import { clientsApi } from "../api/clientsApi";
import { discoveryApi } from "../api/discoveryApi";
import { jobsApi } from "../api/jobsApi";
import type { Client, Job, PlatformHealth } from "../api/types";
import { PlatformIcon } from "../components/PlatformIcon";

type KeywordTab = "names" | "domain";
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
  const commit = () => {
    const trimmed = input.trim();
    if (trimmed) {
      onAdd(trimmed);
      setInput("");
    }
  };
  return (
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
        onBlur={commit}
        placeholder={placeholder}
        className="chip-input"
        disabled={disabled}
      />
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
      </div>
      {activeTab === "names" ? (
        <ChipInput
          chips={nameKeywords}
          onAdd={onAddName}
          onRemove={onRemoveName}
          placeholder="type a person's name, press Enter…"
          disabled={disabled}
        />
      ) : (
        <ChipInput
          chips={domainKeywords}
          onAdd={onAddDomain}
          onRemove={onRemoveDomain}
          placeholder="type a brand/domain keyword, press Enter…"
          disabled={disabled}
        />
      )}
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

export function HomeView({ clientId, platforms, onClient, onForgetClient, busy, analysisBusy, onJobs, onError }: Props) {
  const [clients, setClients] = useState<Client[]>([]);
  const [loadingClients, setLoadingClients] = useState(true);
  const [mode, setMode] = useState<Mode>(clientId ? "select" : "create");
  const [editing, setEditing] = useState(false);

  const [activeClient, setActiveClient] = useState<Client | null>(null);

  const [idInput, setIdInput] = useState(EMPTY_FORM.id);
  const [nameInput, setNameInput] = useState(EMPTY_FORM.name);
  const [domainInput, setDomainInput] = useState(EMPTY_FORM.domain);
  const [nameKeywords, setNameKeywords] = useState<string[]>(EMPTY_FORM.nameKw);
  const [domainKeywords, setDomainKeywords] = useState<string[]>(EMPTY_FORM.domainKw);
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
    setNameKeywords(c.name_keywords || []);
    setDomainKeywords(c.domain_keywords || []);
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
    setNameKeywords(EMPTY_FORM.nameKw);
    setDomainKeywords(EMPTY_FORM.domainKw);
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
  };

  const selectSavedClient = (id: string) => {
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
        name_keywords: nameKeywords,
        domain_keywords: domainKeywords,
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
        keywords: [...(activeClient.name_keywords || []), ...(activeClient.domain_keywords || [])],
      });
      const job = await jobsApi.job(job_id);
      onJobs([job]);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const handleRunAnalysis = async () => {
    if (!activeClient) return;
    try {
      const { job_id } = await analysisApi.analyse({ client_id: activeClient.client_id });
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
          <button className={`mode-tab-btn ${mode === "select" ? "active" : ""}`} onClick={() => setMode("select")}>
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

            <button
              className="btn-cyber-primary"
              disabled={busy || !activeKeywordCount}
              onClick={handleSearch}
              title="Sweeps every ready platform for this client's combined name + domain keywords"
            >
              {busy ? "⚡ Discovery Sweep Running…" : "🔍 Search This Client"}
            </button>

            <button
              className="btn-secondary-action"
              disabled={analysisBusy}
              onClick={handleRunAnalysis}
              title="Analyses every already-validated, not-yet-analysed profile for this client across every platform"
            >
              {analysisBusy ? "🧪 Analysis Running…" : "🧪 Analyse Validated Profiles (catch-up)"}
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
              </div>
            </div>

            <KeywordTabs
              activeTab={activeTab}
              onTab={setActiveTab}
              nameKeywords={nameKeywords}
              domainKeywords={domainKeywords}
              onAddName={(v) => setNameKeywords((prev) => (prev.includes(v) ? prev : [...prev, v]))}
              onRemoveName={(i) => setNameKeywords((prev) => prev.filter((_, idx) => idx !== i))}
              onAddDomain={(v) => setDomainKeywords((prev) => (prev.includes(v) ? prev : [...prev, v]))}
              onRemoveDomain={(i) => setDomainKeywords((prev) => prev.filter((_, idx) => idx !== i))}
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
              <input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder="cron expression, e.g. 0 2 * * * — blank disables"
                style={{
                  marginTop: "7px",
                  width: "100%",
                  background: "var(--bg-inner)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "10px",
                  padding: "10px 12px",
                  color: "var(--text-main)",
                  fontSize: "12px",
                  fontFamily: "var(--font-mono)",
                  outline: "none",
                }}
              />
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
