import { useCallback, useEffect, useState } from "react";
import { analysisApi } from "../api/analysisApi";
import { clientsApi } from "../api/clientsApi";
import { discoveryApi } from "../api/discoveryApi";
import { jobsApi } from "../api/jobsApi";
import type { Job } from "../api/types";

function relativeTime(iso: string): string {
  if (!iso) return "";
  const diffSec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

interface RecentRun {
  keywords: string;
  startedIso: string;
}

/** One row per discovery sweep -- unlike the old per-platform-job UI, a
 * discovery job here always covers every ready platform at once (see
 * backend/api/discovery_routes.py), so there is exactly one job per run. */
function groupRecentRuns(jobs: Job[]): RecentRun[] {
  const byKey = new Map<string, RecentRun>();
  for (const j of jobs) {
    if (j.kind !== "discovery" || !j.params?.keywords?.length) continue;
    const keywords = j.params.keywords.join(", ");
    const existing = byKey.get(keywords);
    if (!existing || j.started > existing.startedIso) {
      byKey.set(keywords, { keywords, startedIso: j.started });
    }
  }
  return [...byKey.values()]
    .sort((a, b) => b.startedIso.localeCompare(a.startedIso))
    .slice(0, 5);
}

interface Props {
  clientId: string;
  clientName: string;
  onClient: (clientId: string, name: string) => void;
  busy: boolean;
  analysisBusy: boolean;
  onJobs: (jobs: Job[]) => void;
  onError: (m: string) => void;
}

export function HomeView({
  clientId,
  clientName,
  onClient,
  busy,
  analysisBusy,
  onJobs,
  onError,
}: Props) {
  const [idInput, setIdInput] = useState(clientId);
  const [nameInput, setNameInput] = useState(clientName);
  const [chips, setChips] = useState<string[]>([]);
  const [kwInput, setKwInput] = useState("");
  const [cron, setCron] = useState("");
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [scheduleSaved, setScheduleSaved] = useState(false);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);

  useEffect(() => setIdInput(clientId), [clientId]);
  useEffect(() => setNameInput(clientName), [clientName]);

  const refreshRecentRuns = useCallback(() => {
    if (!clientId) {
      setRecentRuns([]);
      return;
    }
    jobsApi
      .jobs(clientId, 50)
      .then((res) => setRecentRuns(groupRecentRuns(res.items)))
      .catch(() => {});
  }, [clientId]);

  useEffect(() => {
    refreshRecentRuns();
  }, [refreshRecentRuns]);

  const addChip = () => {
    const trimmed = kwInput.trim();
    if (trimmed && !chips.includes(trimmed)) {
      setChips([...chips, trimmed]);
      setKwInput("");
    }
  };

  const removeChip = (index: number) => {
    setChips(chips.filter((_, i) => i !== index));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addChip();
    }
  };

  const applyClient = (): { id: string; name: string } | null => {
    const id = idInput.trim();
    if (!id) {
      onError("Enter a client id first.");
      return null;
    }
    const name = nameInput.trim() || id;
    onClient(id, name);
    return { id, name };
  };

  const handleRunDiscovery = async () => {
    const target = applyClient();
    if (!target) return;
    if (!chips.length) {
      onError("Please enter at least one keyword chip.");
      return;
    }
    try {
      const { job_id } = await discoveryApi.discover({
        client_id: target.id,
        client_name: target.name,
        keywords: chips,
      });
      const job = await jobsApi.job(job_id);
      onJobs([job]);
      refreshRecentRuns();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const handleRunAnalysis = async () => {
    const target = applyClient();
    if (!target) return;
    try {
      const { job_id } = await analysisApi.analyse({ client_id: target.id });
      const job = await jobsApi.job(job_id);
      onJobs([job]);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const handleSaveSchedule = async () => {
    const target = applyClient();
    if (!target) return;
    setSavingSchedule(true);
    setScheduleSaved(false);
    try {
      await clientsApi.upsertClient({
        client_id: target.id,
        name: target.name,
        keywords: chips,
        cron: cron.trim() || null,
      });
      setScheduleSaved(true);
      setTimeout(() => setScheduleSaved(false), 2000);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSavingSchedule(false);
    }
  };

  return (
    <div className="home-container">
      <div className="home-icon-badge">🛰️</div>
      <h1 className="home-title">Brand Intelligence Suite</h1>
      <p className="home-sub">
        Discover matching profiles across every platform with a ready session
        in one sweep.
      </p>

      <div className="home-card">
        <label className="field-label">1 · Client</label>
        <div className="client-setup-box">
          <input
            value={idInput}
            onChange={(e) => setIdInput(e.target.value)}
            onBlur={applyClient}
            placeholder="client_id (your own org/customer id)…"
            style={{
              flex: 1,
              background: "var(--bg-inner)",
              border: "1px solid var(--border-glow)",
              borderRadius: "12px",
              padding: "12px 14px",
              color: "var(--text-main)",
              fontSize: "13px",
              outline: "none",
            }}
          />
          <input
            value={nameInput}
            onChange={(e) => setNameInput(e.target.value)}
            onBlur={applyClient}
            placeholder="display name (defaults to client_id)…"
            style={{
              flex: 1,
              background: "var(--bg-inner)",
              border: "1px solid var(--border-glow)",
              borderRadius: "12px",
              padding: "12px 14px",
              color: "var(--text-main)",
              fontSize: "13px",
              outline: "none",
            }}
          />
        </div>

        <label className="field-label" style={{ marginTop: "18px" }}>
          2 · Enter Brand Keywords &amp; Search Terms
        </label>
        <div className="chips-input-container">
          {chips.map((kw, i) => (
            <span key={i} className="kw-chip">
              {kw}
              <span className="remove-chip" onClick={() => removeChip(i)}>
                ✕
              </span>
            </span>
          ))}
          <input
            value={kwInput}
            onChange={(e) => setKwInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={addChip}
            placeholder="type keyword, press Enter or comma…"
            className="chip-input"
            disabled={busy}
          />
        </div>

        <button
          className="btn-cyber-primary"
          disabled={busy || !idInput.trim() || !chips.length}
          onClick={handleRunDiscovery}
          title="Sweeps every platform with a ready session in one job -- this backend has no per-platform discovery targeting"
        >
          {busy
            ? "⚡ Discovery Sweep Running…"
            : "⚡ Run Discovery (all ready platforms)"}
        </button>

        <button
          className="btn-cyber-primary"
          style={{
            marginTop: "10px",
            background: "rgba(136,56,221,0.12)",
            color: "var(--cyan-bright)",
            border: "1px solid rgba(136,56,221,0.35)",
          }}
          disabled={analysisBusy || !idInput.trim()}
          onClick={handleRunAnalysis}
          title="Analyses every already-approved, not-yet-analysed profile for this client across every platform -- there is no per-url or per-platform manual analysis on this backend"
        >
          {analysisBusy
            ? "🔬 Analysis Running…"
            : "🔬 Analyse Approved Profiles (catch-up)"}
        </button>

        <div
          style={{
            marginTop: "18px",
            paddingTop: "16px",
            borderTop: "1px solid var(--border-subtle)",
          }}
        >
          <label className="field-label">3 · Recurring Schedule (optional)</label>
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <input
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="cron expression, e.g. 0 2 * * * — blank disables"
              style={{
                flex: 1,
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
            <button
              onClick={handleSaveSchedule}
              disabled={savingSchedule || !idInput.trim() || !chips.length}
              title="Saves this client's keywords + cron so it sweeps automatically -- see backend/services/scheduler_service.py"
              style={{
                padding: "10px 16px",
                borderRadius: "10px",
                fontSize: "12px",
                fontWeight: 700,
                cursor: "pointer",
                background: "var(--bg-inner)",
                color: "var(--text-main)",
                border: "1px solid var(--border-color)",
              }}
            >
              {savingSchedule ? "Saving…" : scheduleSaved ? "✓ Saved" : "Save Schedule"}
            </button>
          </div>
        </div>
      </div>

      <div className="recent-searches-list">
        <div
          style={{
            fontSize: "11px",
            color: "var(--text-dim)",
            textTransform: "uppercase",
            letterSpacing: "1px",
            fontWeight: 600,
            marginBottom: "6px",
          }}
        >
          Recent Discovery Runs ({clientId || "no client set"})
        </div>
        {!recentRuns.length && (
          <div
            style={{
              fontSize: "12px",
              color: "var(--text-dim)",
              padding: "10px 2px",
            }}
          >
            No sweeps yet -- launch one above and it'll show up here.
          </div>
        )}
        {recentRuns.map((r, i) => (
          <div
            key={i}
            className="recent-item-card"
            onClick={() => setChips(r.keywords.split(", ").filter(Boolean))}
          >
            <span style={{ fontSize: "16px" }}>🔎</span>
            <div style={{ flex: 1 }}>
              <div
                style={{
                  fontSize: "13px",
                  fontWeight: 600,
                  color: "var(--text-main)",
                }}
              >
                {r.keywords}
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-dim)" }}>
                {relativeTime(r.startedIso)}
              </div>
            </div>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "12px",
                color: "var(--cyan)",
              }}
            >
              Load Terms ⚡
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
