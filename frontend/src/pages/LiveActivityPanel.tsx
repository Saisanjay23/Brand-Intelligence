// Live Activity: rebuilt as a 3-tab workspace:
//   1) ⚡ In-Flight Runs  – live job cards with platform chips, progress, cyber terminal
//   2) 📜 Job History    – table of past sweeps + log viewer
//   3) 🗄 Record Manager – database profile browser with filters & bulk delete
//
// Data still comes from GET /jobs and GET /jobs/{id}/events (job_routes.py).
import { useEffect, useMemo, useRef, useState } from "react";
import { incidentsApi, type Incident } from "../api/incidentsApi";
import { clientsApi } from "../api/clientsApi";
import { jobsApi } from "../api/jobsApi";
import { profilesApi } from "../api/profilesApi";
import type { Client, Job, JobEvent, PlatformProgress, Profile } from "../api/types";
import { confirmAction } from "../utils/confirmAction";
import { download } from "../utils/download";
import { PlatformIcon } from "../components/PlatformIcon";
import {
  ZapIcon,
  DiscoverIcon,
  AnalyseIcon,
  ClockIcon,
  LockIcon,
  UnlockIcon,
  SearchIcon,
  TrashIcon,
  DatabaseIcon,
  AlertTriangleIcon,
  DownloadIcon,
  RefreshIcon,
  StopIcon,
  PlayIcon,
} from "../components/AppIcons";

const JOBS_REFRESH_MS = 4_000;
const INCIDENTS_REFRESH_MS = 15_000;
const LOG_REFRESH_MS  = 2_000;
const BROWSE_PAGE_SIZE = 50;

// ─── helpers ────────────────────────────────────────────────────────────────

function useNowTick(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return mins < 60 ? `${mins}m ${secs}s` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function exactTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.floor(hours / 24)}d ago`;
}

const PLATFORM_ICON: Record<string, string> = {
  facebook: "📘", instagram: "📸", twitter: "𝕏",
  youtube: "▶️", telegram: "✈️", tiktok: "🎵",
};

const PLATFORM_COLOR: Record<string, string> = {
  facebook: "#1877f2", instagram: "#e1306c", twitter: "#1da1f2",
  youtube: "#ff0000", telegram: "#0088cc", tiktok: "#69c9d0",
};

const PLAT_STATUS_LOOK: Record<string, { bg: string; fg: string }> = {
  pending:  { bg: "rgba(102,112,133,0.15)", fg: "var(--text-dim, #667085)" },
  running:  { bg: "rgba(124,92,255,0.15)",  fg: "var(--accent, #7c5cff)" },
  done:     { bg: "rgba(54,181,160,0.15)",  fg: "var(--success, #36b5a0)" },
  partial:  { bg: "rgba(253,183,27,0.15)",  fg: "var(--warn-yellow, #fdb71b)" },
  failed:   { bg: "rgba(233,80,83,0.15)",   fg: "var(--danger, #e95053)" },
  skipped:  { bg: "rgba(102,112,133,0.1)",  fg: "var(--text-dim, #667085)" },
};

const JOB_STATUS_COLOR: Record<string, string> = {
  queued:    "var(--text-dim, #667085)",
  running:   "var(--accent, #7c5cff)",
  done:      "var(--success, #36b5a0)",
  failed:    "var(--danger, #e95053)",
  cancelled: "var(--warn-yellow, #fdb71b)",
};

const PROFILE_STATUS_BADGE: Record<string, string> = {
  pending:  "var(--warn-yellow, #fdb71b)",
  approved: "var(--success, #36b5a0)",
  rejected: "var(--danger, #e95053)",
};

// A profile analysis has not finished with, three states:
//   eligible  -- will be revisited automatically on the next catch-up
//               sweep or round-robin pass, nothing for an analyst to do
//   exhausted -- hit the server's attempt cap (MAX_ANALYSIS_ATTEMPTS);
//               needs either Resume or to just be left as a known gap
//   stopped   -- an analyst turned retry off for this one on purpose
// See backend/services/profile_service.py::_retry_state, which computes
// exactly this enum server-side; this map only decides how it LOOKS.
const RETRY_STATE_LOOK: Record<string, { color: string; label: string }> = {
  eligible:  { color: "var(--accent, #7c5cff)",     label: "Eligible" },
  exhausted: { color: "var(--warn-yellow, #fdb71b)", label: "Exhausted" },
  stopped:   { color: "var(--danger, #e95053)",      label: "Stopped" },
};

// ─── Embedded CSS ─────────────────────────────────────────────────────────────

const PANEL_STYLES = `
@keyframes radarPulse {
  0%,100% { transform: scale(1);   opacity: 0.9; }
  50%      { transform: scale(1.5); opacity: 0.2; }
}
@keyframes progressPulse {
  0%,100% { opacity: 1; }
  50%      { opacity: 0.55; }
}
@keyframes chipGlow {
  0%,100% { box-shadow: 0 0 0 0 rgba(124,92,255,0); }
  50%      { box-shadow: 0 0 10px 2px rgba(124,92,255,0.3); }
}
.la-tab { transition: all 0.2s ease; }
.la-tab:hover { background: rgba(255,255,255,0.06) !important; }
.la-tab.active { background: var(--bg-surface,#1e2837) !important; color: var(--accent,#7c5cff) !important; }
.la-job-card { transition: border-color 0.2s ease; }
.la-job-card:hover { border-color: rgba(124,92,255,0.35) !important; }
.la-action-btn { transition: all 0.18s ease; }
.la-action-btn:hover { background: var(--bg-surface-3,#344054) !important; }
.la-platform-chip { transition: transform 0.2s ease; }
.la-platform-chip:hover { transform: translateY(-1px); }
.la-terminal-line:hover { background: rgba(255,255,255,0.03); border-radius: 3px; }
.la-records-btn { transition: all 0.2s ease; }
.la-records-btn:hover { background: rgba(54,181,160,0.18) !important; border-color: var(--success,#36b5a0) !important; color: var(--success,#36b5a0) !important; }
`;

// ─── Atoms ───────────────────────────────────────────────────────────────────

const SEVERITY_LOOK: Record<string, { color: string; label: string }> = {
  critical: { color: "#ef4444", label: "CRITICAL" },
  warning: { color: "#fdb71b", label: "WARNING" },
  info: { color: "#7c5cff", label: "INFO" },
};

/**
 * Operational incidents, live.
 *
 * Until now these existed only as email and rows in Mongo -- so the only
 * way to know the pipeline was struggling was to already be on the alert
 * list. Critical first (a dead session, a parser that stopped recognising
 * a page), because that is the set someone has to act on; warnings are
 * still shown, just not shouted.
 */
function IncidentsFeed() {
  const [items, setItems] = useState<Incident[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [severity, setSeverity] = useState("");
  const [expanded, setExpanded] = useState<string>("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      incidentsApi
        .list(40, severity)
        .then((r) => {
          if (cancelled) return;
          setItems(r.items);
          setCounts(r.counts);
          setError("");
        })
        .catch((e) => !cancelled && setError((e as Error).message));
    };
    load();
    const t = setInterval(load, INCIDENTS_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [severity]);

  const pill = (value: string, label: string, color: string) => (
    <button
      key={value || "all"}
      type="button"
      onClick={() => setSeverity(value)}
      style={{
        padding: "3px 10px",
        borderRadius: "999px",
        fontSize: "11px",
        fontWeight: 700,
        cursor: "pointer",
        border: `1px solid ${severity === value ? color : "var(--border-subtle)"}`,
        background: severity === value ? `${color}22` : "transparent",
        color: severity === value ? color : "var(--text-muted)",
      }}
    >
      {label}
      {counts[value] != null ? ` ${counts[value]}` : ""}
    </button>
  );

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "12px",
        padding: "14px 16px",
        marginBottom: "18px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "10px" }}>
        <span style={{ fontSize: "10px", letterSpacing: "1px", textTransform: "uppercase", fontWeight: 700, color: "var(--text-dim)" }}>
          Incidents
        </span>
        {pill("", "all", "var(--cyan-bright, #00e5ff)")}
        {pill("critical", "critical", SEVERITY_LOOK.critical.color)}
        {pill("warning", "warning", SEVERITY_LOOK.warning.color)}
        {pill("info", "info", SEVERITY_LOOK.info.color)}
      </div>

      {error && <div style={{ fontSize: "12px", color: "var(--danger)" }}>{error}</div>}
      {!error && items.length === 0 && (
        <div style={{ fontSize: "13px", color: "var(--text-dim)", padding: "6px 0" }}>
          Nothing recorded{severity ? ` at ${severity} severity` : ""}. The pipeline is healthy.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "340px", overflowY: "auto" }}>
        {items.map((i) => {
          const look = SEVERITY_LOOK[i.severity] ?? { color: "var(--text-dim)", label: i.severity || "?" };
          const open = expanded === i.id;
          return (
            <div
              key={i.id}
              onClick={() => setExpanded(open ? "" : i.id)}
              style={{
                padding: "8px 10px",
                borderRadius: "8px",
                background: "var(--bg-inner)",
                borderLeft: `3px solid ${look.color}`,
                cursor: "pointer",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                <span style={{ fontSize: "10px", fontWeight: 700, color: look.color }}>{look.label}</span>
                <strong style={{ fontSize: "12px", color: "var(--text-primary, #fff)" }}>
                  {i.platform}/{i.kind}
                </strong>
                <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{i.error_type}</span>
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>{relativeTime(i.ts)}</span>
              </div>
              <div
                style={{
                  fontSize: "12px",
                  color: "var(--text-muted)",
                  marginTop: "3px",
                  ...(open ? {} : { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }),
                }}
              >
                {i.message}
              </div>
              {open && (
                <div style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-dim)", lineHeight: 1.6 }}>
                  {i.cause && <div><strong>Cause:</strong> {i.cause}</div>}
                  {i.fix && <div><strong>Fix:</strong> {i.fix}</div>}
                  {i.where && (
                    <div style={{ fontFamily: "var(--font-mono)", whiteSpace: "pre-wrap", marginTop: "4px" }}>
                      {i.where}
                    </div>
                  )}
                  <div style={{ marginTop: "4px" }}>
                    {exactTime(i.ts)}{i.job_id ? ` · job ${i.job_id}` : ""}
                    {i.scope ? ` · ${i.scope}` : ""}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatusDot({ color }: { color: string }) {
  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center", justifyContent: "center", width: 10, height: 10 }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "block" }} />
      <span style={{ position: "absolute", width: 14, height: 14, borderRadius: "50%", background: color, opacity: 0.25, animation: "radarPulse 1.8s ease-in-out infinite" }} />
    </span>
  );
}

function Badge({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color, fontWeight: 700, fontSize: "11px", textTransform: "uppercase", letterSpacing: "0.4px" }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, flexShrink: 0 }} />
      {children}
    </span>
  );
}

function EmptyState({ icon, text }: { icon: string; text: string }) {
  return (
    <div style={{ padding: "40px 24px", textAlign: "center", background: "var(--bg-surface,#1e2837)", border: "1px dashed rgba(255,255,255,0.1)", borderRadius: 12 }}>
      <div style={{ fontSize: 32, marginBottom: 10 }}>{icon}</div>
      <div style={{ fontSize: 13, color: "var(--text-dim,#667085)", maxWidth: 360, margin: "0 auto" }}>{text}</div>
    </div>
  );
}

// ─── Platform Progress Chip ──────────────────────────────────────────────────

function PlatformChip({ pid, p, now }: { pid: string; p: PlatformProgress; now: number }) {
  const look = PLAT_STATUS_LOOK[p.status] ?? { bg: "rgba(102,112,133,0.1)", fg: "var(--text-dim)" };
  const pct = p.total > 0 ? Math.min(100, Math.round((p.processed / p.total) * 100)) : 0;
  const liveElapsed = p.started
    ? p.status === "running" ? Math.floor((now - p.started * 1000) / 1000) : p.elapsed_seconds
    : null;
  const accentColor = PLATFORM_COLOR[pid] ?? "#7c5cff";
  const isRunning = p.status === "running";

  return (
    <div className="la-platform-chip" style={{
      background: look.bg,
      border: `1px solid ${isRunning ? accentColor + "55" : "rgba(255,255,255,0.08)"}`,
      borderRadius: 10, padding: "10px 14px", minWidth: 165, flex: "1 1 165px",
      display: "flex", flexDirection: "column", gap: 6,
      boxShadow: isRunning ? `0 0 14px ${accentColor}22` : "none",
      animation: isRunning ? "chipGlow 2.5s ease-in-out infinite" : "none",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <PlatformIcon platform={pid} size={18} />
          <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary,#fff)", textTransform: "capitalize" }}>{pid}</span>
        </div>
        <span style={{ fontSize: 9, fontWeight: 700, padding: "2px 7px", borderRadius: 999, background: look.bg, color: look.fg, border: `1px solid ${look.fg}55`, textTransform: "uppercase", letterSpacing: "0.4px" }}>
          {p.status}
        </span>
      </div>
      {p.total > 0 && (
        <div>
          <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${pct}%`, background: isRunning ? `linear-gradient(90deg, ${accentColor}bb, ${accentColor})` : look.fg, transition: "width 0.5s ease", animation: isRunning ? "progressPulse 2s ease-in-out infinite" : "none" }} />
          </div>
          <div style={{ fontSize: 10, color: "var(--text-dim,#667085)", marginTop: 2, display: "flex", justifyContent: "space-between" }}>
            <span>{p.processed}/{p.total} ({pct}%)</span>
            {liveElapsed !== null && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                <ClockIcon size={11} /> {durationLabel(liveElapsed)}
              </span>
            )}
          </div>
        </div>
      )}
      {isRunning && p.done_items && p.done_items.length > 0 && (
        <div style={{ fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          <span style={{ color: look.fg, fontWeight: 600 }}>Just:</span>{" "}{p.done_items[p.done_items.length - 1]}
        </div>
      )}
      {isRunning && p.eta_seconds != null && (
        <div style={{ fontSize: 10, color: look.fg, fontWeight: 600 }}>ETA ~{durationLabel(p.eta_seconds)}</div>
      )}
    </div>
  );
}

// ─── Cyber Terminal ───────────────────────────────────────────────────────────

const LOG_TYPE_COLOR: Record<string, string> = {
  info:      "var(--text-dim,#667085)",
  debug:     "var(--text-dim,#667085)",
  warning:   "var(--warn-yellow,#fdb71b)",
  warn:      "var(--warn-yellow,#fdb71b)",
  error:     "var(--danger,#e95053)",
  failed:    "var(--danger,#e95053)",
  discovery: "#2ee9d6",
  analysis:  "var(--accent,#7c5cff)",
  success:   "var(--success,#36b5a0)",
  hit:       "var(--success,#36b5a0)",
};

const LOG_FILTER_PILLS = [
  { id: "",                               label: "All" },
  { id: "hit,discovery,analysis,success", label: "Hits" },
  { id: "error,failed,warning,warn",      label: "⚠ Errors" },
];

function CyberTerminal({ jobId, onClose }: { jobId: string; onClose?: () => void }) {
  const [events, setEvents]     = useState<JobEvent[]>([]);
  const [filterText, setFilterText] = useState("");
  const [filterPill, setFilterPill] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const lastSeq = useRef(0);
  const boxRef  = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    lastSeq.current = 0;
    const poll = () => {
      jobsApi.jobEvents(jobId, lastSeq.current).then((r) => {
        if (cancelled || !r.items.length) return;
        lastSeq.current = r.items[r.items.length - 1].seq;
        setEvents((prev) => [...prev, ...r.items].slice(-600));
      }).catch(() => {});
    };
    poll();
    const t = setInterval(poll, LOG_REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [jobId]);

  useEffect(() => {
    if (autoScroll && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [events, autoScroll]);

  const filteredEvents = useMemo(() => {
    let list = events;
    if (filterPill) {
      const types = new Set(filterPill.split(","));
      list = list.filter((e) => types.has(e.type.toLowerCase()));
    }
    if (filterText.trim()) {
      const q = filterText.toLowerCase();
      list = list.filter((e) => e.message.toLowerCase().includes(q) || e.type.toLowerCase().includes(q));
    }
    return list;
  }, [events, filterText, filterPill]);

  const handleDownload = () => {
    const text = events.map((e) => `[${e.ts || new Date().toISOString()}] [${e.type.toUpperCase()}] ${e.message}`).join("\n");
    download(`job-${jobId}-log.txt`, text, "text/plain");
  };

  return (
    <div style={{ background: "#080f1e", border: "1px solid rgba(124,92,255,0.3)", borderRadius: 10, overflow: "hidden", marginTop: 10 }}>
      {/* Title bar */}
      <div style={{ background: "rgba(124,92,255,0.08)", borderBottom: "1px solid rgba(124,92,255,0.2)", padding: "6px 12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ display: "flex", gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#e95053" }} />
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#fdb71b" }} />
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#36b5a0" }} />
          </div>
          <span style={{ fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--font-mono)", letterSpacing: "0.5px" }}>
            ● LIVE STREAM — job/{jobId.slice(0, 8)}…
          </span>
          {events.length > 0 && <StatusDot color="var(--success,#36b5a0)" />}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" onClick={() => setAutoScroll((v) => !v)} style={{ fontSize: 10, padding: "3px 8px", borderRadius: 5, background: autoScroll ? "rgba(54,181,160,0.15)" : "rgba(255,255,255,0.06)", border: `1px solid ${autoScroll ? "var(--success,#36b5a0)" : "rgba(255,255,255,0.1)"}`, color: autoScroll ? "var(--success,#36b5a0)" : "var(--text-dim)", cursor: "pointer", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 4 }}>
            {autoScroll ? <><LockIcon size={12} /> Scroll ON</> : <><UnlockIcon size={12} /> Scroll OFF</>}
          </button>
          <button type="button" onClick={handleDownload} disabled={!events.length} style={{ fontSize: 10, padding: "3px 8px", borderRadius: 5, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", color: "var(--text-dim)", cursor: events.length ? "pointer" : "not-allowed", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <DownloadIcon size={12} /> Export
          </button>
          {onClose && (
            <button type="button" onClick={onClose} style={{ fontSize: 12, padding: "3px 8px", borderRadius: 5, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)", color: "var(--text-muted)", cursor: "pointer", fontWeight: 700 }}>✕</button>
          )}
        </div>
      </div>
      {/* Filter bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 12px", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
        {LOG_FILTER_PILLS.map((pill) => (
          <button key={pill.id} type="button" onClick={() => setFilterPill(pill.id)} style={{ fontSize: 10, padding: "3px 9px", borderRadius: 999, cursor: "pointer", fontWeight: 600, background: filterPill === pill.id ? "rgba(124,92,255,0.2)" : "transparent", border: `1px solid ${filterPill === pill.id ? "var(--accent,#7c5cff)" : "rgba(255,255,255,0.1)"}`, color: filterPill === pill.id ? "var(--accent,#7c5cff)" : "var(--text-dim)" }}>
            {pill.label}
          </button>
        ))}
        <div style={{ position: "relative", flex: 1, display: "flex", alignItems: "center" }}>
          <SearchIcon size={12} color="var(--text-dim)" style={{ position: "absolute", left: 8 }} />
          <input type="text" value={filterText} onChange={(e) => setFilterText(e.target.value)} placeholder="Filter logs…" style={{ width: "100%", fontSize: 11, padding: "3px 8px 3px 26px", borderRadius: 5, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.1)", color: "var(--text-main)", outline: "none", fontFamily: "var(--font-mono)" }} />
        </div>
        <span style={{ fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{filteredEvents.length}/{events.length} lines</span>
      </div>
      {/* Stream */}
      <div ref={boxRef} style={{ height: 240, overflowY: "auto", padding: "8px 12px", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: "1.65" }}>
        {filteredEvents.length === 0 ? (
          <div style={{ color: "var(--text-dim)", textAlign: "center", paddingTop: 40 }}>
            {events.length === 0 ? "Waiting for activity…" : "No lines match the current filter."}
          </div>
        ) : (
          filteredEvents.map((e) => {
            const t   = e.type.toLowerCase();
            const col = LOG_TYPE_COLOR[t] ?? "var(--text-dim)";
            return (
              <div key={e.seq} className="la-terminal-line" style={{ display: "flex", gap: 8, padding: "1px 4px" }}>
                <span style={{ color: "rgba(255,255,255,0.2)", flexShrink: 0, userSelect: "none" }}>
                  {e.ts ? new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                </span>
                <span style={{ color: col, flexShrink: 0, fontWeight: 700, minWidth: 80, textTransform: "uppercase", letterSpacing: "0.3px" }}>[{e.type}]</span>
                <span style={{ color: col !== "var(--text-dim,#667085)" ? "rgba(255,255,255,0.85)" : col }}>{e.message}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ─── Job Card ────────────────────────────────────────────────────────────────

function JobCard({
  job, clientName, now, onStop, onBrowse, stopping, expanded, onToggleExpand,
}: {
  job: Job; clientName: string; now: number; onStop: (id: string) => void;
  onBrowse: (clientId: string, platform: string | null) => void;
  stopping: boolean; expanded: boolean; onToggleExpand: () => void;
}) {
  const statusColor = JOB_STATUS_COLOR[job.status] ?? "var(--text-dim)";
  const elapsed     = job.started ? Math.floor((now - new Date(job.started).getTime()) / 1000) : null;
  const platformIds = Object.keys(job.platforms || {});
  const canStop     = job.status === "queued" || job.status === "running";
  const isRunning   = job.status === "running";
  const totalDone   = platformIds.reduce((s, pid) => s + (job.platforms[pid]?.processed ?? 0), 0);
  const throughput  = elapsed && elapsed > 30 ? Math.round((totalDone / elapsed) * 60) : null;

  return (
    <div className="la-job-card" style={{ background: "var(--bg-surface,#1e2837)", border: `1px solid ${isRunning ? "rgba(124,92,255,0.25)" : "rgba(255,255,255,0.06)"}`, borderRadius: 12, padding: "14px 16px", boxShadow: isRunning ? "0 4px 20px rgba(124,92,255,0.1)" : "none" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {job.kind === "discovery" ? <DiscoverIcon size={16} color="var(--cyan)" /> : <AnalyseIcon size={16} color="#7c5cff" />}
            <strong style={{ fontSize: 14, color: "var(--text-primary,#fff)" }}>{clientName}</strong>
            <span style={{ fontSize: 11, color: "var(--text-dim)", textTransform: "capitalize", background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: 999 }}>{job.kind}</span>
            {isRunning ? <StatusDot color="var(--accent,#7c5cff)" /> : <Badge color={statusColor}>{job.status}</Badge>}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-dim,#98a2b3)", display: "flex", flexWrap: "wrap", gap: "4px 12px" }}>
            <span title={exactTime(job.started)}>Started {relativeTime(job.started)}</span>
            {elapsed !== null && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                <ClockIcon size={11} /> {durationLabel(elapsed)}
              </span>
            )}
            {throughput !== null && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                <ZapIcon size={11} color="var(--cyan)" /> ~{throughput} items/min
              </span>
            )}
            {job.blocked_by && <span style={{ color: "var(--warn-yellow,#fdb71b)" }}>· waiting on {job.blocked_by.client_id}'s {job.blocked_by.kind}</span>}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="la-action-btn" onClick={onToggleExpand} style={{ padding: "6px 12px", borderRadius: 8, background: expanded ? "rgba(124,92,255,0.15)" : "var(--bg-surface-3,#1d2939)", border: `1px solid ${expanded ? "var(--accent,#7c5cff)" : "rgba(255,255,255,0.1)"}`, color: expanded ? "var(--accent,#7c5cff)" : "var(--text-body,#fff)", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
            {expanded ? "▾ Terminal" : "▸ Terminal"}
          </button>
          <button type="button" className="la-action-btn" onClick={() => onBrowse(job.client_id, job.platform)} style={{ padding: "6px 12px", borderRadius: 8, background: "var(--bg-surface-3,#1d2939)", border: "1px solid rgba(255,255,255,0.1)", color: "var(--text-body,#fff)", fontSize: 12, fontWeight: 600, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5 }}>
            <DatabaseIcon size={13} /> Records
          </button>
          {canStop && (
            <button type="button" onClick={() => onStop(job.id)} disabled={stopping} style={{ padding: "6px 12px", borderRadius: 8, background: "rgba(233,80,83,0.12)", border: "1px solid rgba(233,80,83,0.4)", color: "var(--danger,#e95053)", fontSize: 12, fontWeight: 700, cursor: stopping ? "wait" : "pointer" }}>
              ⏹ Stop
            </button>
          )}
        </div>
      </div>
      {platformIds.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 14 }}>
          {platformIds.map((pid) => <PlatformChip key={pid} pid={pid} p={job.platforms[pid]} now={now} />)}
        </div>
      )}
      {job.message && <div style={{ marginTop: 10, fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>{job.message}</div>}
      {expanded && <CyberTerminal jobId={job.id} onClose={onToggleExpand} />}
    </div>
  );
}

// ─── Main ────────────────────────────────────────────────────────────────────

const LA_SELECT_STYLE: React.CSSProperties = {
  background: "var(--bg-inner,#0b1220)", border: "1px solid var(--border-color,#344054)",
  borderRadius: 8, padding: "8px 10px", color: "var(--text-main)", fontSize: 12, outline: "none",
};

export function LiveActivityPanel() {
  const [activeTab,   setActiveTab]   = useState<"live" | "history" | "records" | "retry">("live");
  const [jobs,        setJobs]        = useState<Job[]>([]);
  const [clients,     setClients]     = useState<Client[]>([]);
  const [error,       setError]       = useState("");
  const [stoppingId,  setStoppingId]  = useState("");
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const now = useNowTick();

  // Record Manager state
  const [browseClientId,  setBrowseClientId]  = useState("");
  const [browsePlatform,  setBrowsePlatform]  = useState("");
  const [browseStatus,    setBrowseStatus]    = useState("");
  const [browseSearch,    setBrowseSearch]    = useState("");
  const [profiles,        setProfiles]        = useState<Profile[]>([]);
  const [profilesTotal,   setProfilesTotal]   = useState(0);
  const [offset,          setOffset]          = useState(0);
  const [selected,        setSelected]        = useState<Set<string>>(new Set());
  const [browseLoading,   setBrowseLoading]   = useState(false);
  const [deleting,        setDeleting]        = useState(false);

  // Retry Queue state. Independent of Record Manager's own client/platform
  // filters above on purpose: an analyst watching "what's stuck" for one
  // client and browsing raw records for another at the same time is a
  // completely reasonable thing to want mid-triage, and coupling the two
  // filters would silently reset one when the other changes tabs.
  const [retryClientId, setRetryClientId] = useState("");
  const [retryPlatform, setRetryPlatform] = useState("");
  const [retryStateFilter, setRetryStateFilter] = useState<"" | "eligible" | "exhausted" | "stopped">("");
  const [retryItems,   setRetryItems]   = useState<Profile[]>([]);
  const [retryCounts,  setRetryCounts]  = useState({ eligible: 0, exhausted: 0, stopped: 0 });
  const [retryLoading, setRetryLoading] = useState(false);
  const [retrySelected, setRetrySelected] = useState<Set<string>>(new Set());
  const [retryActingId, setRetryActingId] = useState("");
  const [retryBulkBusy, setRetryBulkBusy] = useState(false);

  useEffect(() => {
    clientsApi.listClients().then((r) => setClients(r.items)).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      jobsApi.jobs("", 100).then((r) => { if (!cancelled) setJobs(r.items); })
        .catch((e) => !cancelled && setError((e as Error).message));
    };
    load();
    const t = setInterval(load, JOBS_REFRESH_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const clientName = useMemo(() => {
    const m = new Map(clients.map((c) => [c.client_id, c.name || c.client_id]));
    return (id: string) => m.get(id) || id;
  }, [clients]);

  const activeJobs   = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const terminalJobs = jobs.filter((j) => j.status === "done" || j.status === "failed" || j.status === "cancelled");

  // HUD metrics
  const totalProcessed   = activeJobs.reduce((s, j) => s + Object.values(j.platforms || {}).reduce((ps, p) => ps + (p.processed ?? 0), 0), 0);
  const totalElapsedSecs = activeJobs.reduce((s, j) => s + (j.started ? Math.floor((now - new Date(j.started).getTime()) / 1000) : 0), 0);
  const throughputPerMin = totalElapsedSecs > 30 ? Math.round((totalProcessed / totalElapsedSecs) * 60) : null;

  const toggleExpand = (id: string) =>
    setExpandedIds((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const stop = async (jobId: string) => {
    if (!(await confirmAction("Stop this job? Whatever hasn't been scraped/analysed yet will be left as-is."))) return;
    setStoppingId(jobId);
    try {
      await jobsApi.cancelJob(jobId);
      const r = await jobsApi.jobs("", 100);
      setJobs(r.items);
    } catch (e) { setError((e as Error).message); }
    finally { setStoppingId(""); }
  };

  const browseTo = (clientId: string, platform: string | null) => {
    setBrowseClientId(clientId);
    setBrowsePlatform(platform || "");
    setOffset(0);
    setActiveTab("records");
  };

  const loadProfiles = () => {
    if (!browseClientId) { setProfiles([]); setProfilesTotal(0); return; }
    setBrowseLoading(true);
    profilesApi.profiles({
      client_id: browseClientId, platform: browsePlatform || undefined,
      status: browseStatus || undefined, search: browseSearch || undefined,
      limit: BROWSE_PAGE_SIZE, offset,
    })
      .then((r) => { setProfiles(r.items); setProfilesTotal(r.total); setSelected(new Set()); })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBrowseLoading(false));
  };

  useEffect(loadProfiles, [browseClientId, browsePlatform, browseStatus, offset]);

  const deleteSelected = async () => {
    if (selected.size === 0) return;
    if (!(await confirmAction(`Permanently delete ${selected.size} profile record(s)? This cannot be undone.`))) return;
    setDeleting(true);
    try { await profilesApi.deleteProfiles(Array.from(selected)); loadProfiles(); }
    catch (e) { setError((e as Error).message); }
    finally { setDeleting(false); }
  };

  const toggleOne = (id: string) =>
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () =>
    setSelected((prev) => prev.size === profiles.length ? new Set() : new Set(profiles.map((p) => p.id)));

  // ── Retry Queue: load, filter, act ──────────────────────────────────────
  //
  // Polls only while this tab is the active one and a client is picked --
  // a background poll for a screen nobody is looking at would just be
  // wasted requests, the same restraint Job History/Record Manager already
  // apply by scoping their own effects to what's actually visible.
  const loadRetryQueue = () => {
    if (!retryClientId) { setRetryItems([]); setRetryCounts({ eligible: 0, exhausted: 0, stopped: 0 }); return; }
    setRetryLoading(true);
    profilesApi.retryQueue(retryClientId, retryPlatform || undefined)
      .then((r) => {
        setRetryItems(r.items);
        setRetryCounts(r.counts);
        setRetrySelected((prev) => {
          const stillPresent = new Set(r.items.map((i) => i.id));
          const next = new Set<string>();
          prev.forEach((id) => { if (stillPresent.has(id)) next.add(id); });
          return next;
        });
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setRetryLoading(false));
  };

  useEffect(() => {
    if (activeTab !== "retry" || !retryClientId) return;
    loadRetryQueue();
    const t = setInterval(loadRetryQueue, JOBS_REFRESH_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, retryClientId, retryPlatform]);

  const visibleRetryItems = retryItems.filter(
    (i) => !retryStateFilter || i.retry_state === retryStateFilter,
  );

  const stopOne = async (id: string) => {
    setRetryActingId(id);
    try { await profilesApi.stopRetry(id); loadRetryQueue(); }
    catch (e) { setError((e as Error).message); }
    finally { setRetryActingId(""); }
  };

  const resumeOne = async (id: string) => {
    setRetryActingId(id);
    try { await profilesApi.resumeRetry(id); loadRetryQueue(); }
    catch (e) { setError((e as Error).message); }
    finally { setRetryActingId(""); }
  };

  const toggleRetrySelected = (id: string) =>
    setRetrySelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleRetrySelectedAll = () =>
    setRetrySelected((prev) =>
      prev.size === visibleRetryItems.length ? new Set() : new Set(visibleRetryItems.map((i) => i.id)),
    );

  const stopSelectedRetries = async () => {
    if (retrySelected.size === 0) return;
    if (!(await confirmAction(
      `Stop automatic retry for ${retrySelected.size} profile(s)? They keep whatever they already read and can still be Resumed later, but no future sweep will revisit them until you do.`,
    ))) return;
    setRetryBulkBusy(true);
    try {
      const res = await profilesApi.bulkStopRetry(Array.from(retrySelected));
      if (res.failed.length) setError(`${res.failed.length} profile(s) could not be stopped.`);
      loadRetryQueue();
    } catch (e) { setError((e as Error).message); }
    finally { setRetryBulkBusy(false); }
  };

  const TABS: Array<{ id: "live" | "history" | "records" | "retry"; label: string; icon: React.ReactNode; badge?: number }> = [
    { id: "live",    label: "In-Flight Runs", icon: <ZapIcon size={14} />, badge: activeJobs.length || undefined },
    { id: "history", label: "Job History",   icon: <ClockIcon size={14} />, badge: terminalJobs.length || undefined },
    { id: "records", label: "Record Manager", icon: <DatabaseIcon size={14} /> },
    // Badge counts only "exhausted" + "stopped" -- the two states that
    // actually need a human's attention. "eligible" rows will resolve
    // themselves, badging on the full total would make the tab look
    // permanently alarming on a perfectly healthy pipeline that just has
    // a normal in-flight backlog.
    { id: "retry",   label: "Retry Queue",   icon: <RefreshIcon size={14} />, badge: (retryCounts.exhausted + retryCounts.stopped) || undefined },
  ];

  return (
    <div style={{ color: "var(--text-main,#f2f4f7)" }}>
      <style>{PANEL_STYLES}</style>

      {/* Page header */}
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary,#fff)", margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
          <ZapIcon size={22} color="var(--cyan)" />
          <span>Live Activity</span>
        </h2>
        <p style={{ fontSize: 13, color: "var(--text-muted,#98a2b3)", margin: "4px 0 0 0" }}>
          Monitor every in-flight scrape engine, inspect job logs, and manage the discovered profile database.
        </p>
      </div>

      {/* HUD banner */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 20, padding: "12px 16px", background: "var(--bg-surface,#1e2837)", border: "1px solid rgba(124,92,255,0.2)", borderRadius: 12, boxShadow: "0 4px 20px rgba(0,0,0,0.25)" }}>
        {([
          { label: "Active Engines",  value: activeJobs.length ? `${activeJobs.length} Running` : "Idle",  color: activeJobs.length ? "var(--accent,#7c5cff)" : "var(--text-dim)", dot: activeJobs.length > 0 },
          { label: "Queued",          value: `${activeJobs.filter((j) => j.status === "queued").length} Pending`, color: "var(--warn-yellow,#fdb71b)", dot: false },
          { label: "Throughput",      value: throughputPerMin !== null ? `~${throughputPerMin}/min` : "—", color: "var(--success,#36b5a0)", dot: false },
          { label: "Jobs Done",       value: String(terminalJobs.length), color: "var(--text-body,#fff)", dot: false },
        ] as const).map((stat) => (
          <div key={stat.label} style={{ flex: "1 1 130px", display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>{stat.label}</span>
            <span style={{ fontSize: 17, fontWeight: 800, color: stat.color, display: "flex", alignItems: "center", gap: 6 }}>
              {stat.dot && <StatusDot color={stat.color} />}
              {stat.value}
            </span>
          </div>
        ))}
      </div>

      {/* Error banner */}
      {error && (
        <div style={{ padding: "10px 16px", background: "rgba(233,80,83,0.1)", border: "1px solid rgba(233,80,83,0.25)", color: "var(--danger,#e95053)", borderRadius: 10, marginBottom: 16, fontSize: 13, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <AlertTriangleIcon size={15} color="var(--danger)" /> {error}
          </span>
          <button type="button" onClick={() => setError("")} style={{ background: "transparent", border: "none", color: "var(--danger,#e95053)", cursor: "pointer", fontWeight: 700, fontSize: 14 }}>✕</button>
        </div>
      )}

      {/* 3-Tab nav */}
      <div style={{ display: "flex", gap: 4, background: "var(--bg-app,#101828)", padding: 4, borderRadius: 10, border: "1px solid var(--border-color,#344054)", marginBottom: 20 }}>
        {TABS.map((tab) => (
          <button key={tab.id} type="button" className={`la-tab${activeTab === tab.id ? " active" : ""}`} onClick={() => setActiveTab(tab.id)} style={{ flex: 1, padding: "8px 10px", borderRadius: 8, border: "none", background: activeTab === tab.id ? "var(--bg-surface,#1e2837)" : "transparent", color: activeTab === tab.id ? "var(--accent,#7c5cff)" : "var(--text-muted,#98a2b3)", fontSize: 13, fontWeight: activeTab === tab.id ? 700 : 500, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, boxShadow: activeTab === tab.id ? "0 1px 6px rgba(0,0,0,0.25)" : "none" }}>
            {tab.icon}
            <span>{tab.label}</span>
            {tab.badge !== undefined && (
              <span style={{ padding: "1px 7px", borderRadius: 999, fontSize: 10, fontWeight: 800, background: activeTab === tab.id ? "var(--accent,#7c5cff)" : "var(--bg-surface-3,#344054)", color: activeTab === tab.id ? "#fff" : "var(--text-main)" }}>{tab.badge}</span>
            )}
          </button>
        ))}
      </div>

      {/* ══ TAB 1: IN-FLIGHT RUNS ══ */}
      {activeTab === "live" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* What the pipeline is struggling with, alongside what it is
              doing. Above the job cards on purpose: a dead session or a
              broken parser explains the jobs below it. */}
          <IncidentsFeed />
          {activeJobs.length === 0 ? (
            <EmptyState icon="🛸" text="No active scrapes right now — launch a Discovery Sweep or Re-run Analysis from the Clients tab, or wait for the round-robin engine to pick up the next job." />
          ) : (
            activeJobs.map((job) => (
              <JobCard key={job.id} job={job} clientName={clientName(job.client_id)} now={now}
                onStop={stop} onBrowse={browseTo} stopping={stoppingId === job.id}
                expanded={expandedIds.has(job.id)} onToggleExpand={() => toggleExpand(job.id)} />
            ))
          )}
        </div>
      )}

      {/* ══ TAB 2: JOB HISTORY ══ */}
      {activeTab === "history" && (
        <div>
          {terminalJobs.length === 0 ? (
            <EmptyState icon="📜" text="No completed jobs yet. Run a sweep to see history here." />
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="core_table" style={{ minWidth: 680 }}>
                <thead>
                  <tr>
                    <th>Client</th><th>Type</th><th>Status</th><th>Duration</th><th>Finished</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {terminalJobs.map((job) => {
                    const took = job.started && job.finished
                      ? Math.round((new Date(job.finished).getTime() - new Date(job.started).getTime()) / 1000) : null;
                    const isExpanded = expandedIds.has(job.id);
                    return (
                      <>
                        <tr key={job.id}>
                          <td style={{ fontWeight: 600, color: "var(--text-primary,#fff)" }}>{clientName(job.client_id)}</td>
                          <td>
                            <span style={{ fontSize: 11, background: "rgba(255,255,255,0.06)", padding: "2px 8px", borderRadius: 999, display: "inline-flex", alignItems: "center", gap: 4 }}>
                              {job.kind === "discovery" ? <DiscoverIcon size={12} color="var(--cyan)" /> : <AnalyseIcon size={12} color="#7c5cff" />}
                              {job.kind}
                            </span>
                          </td>
                          <td><Badge color={JOB_STATUS_COLOR[job.status] ?? "var(--text-dim)"}>{job.status}</Badge></td>
                          <td style={{ fontSize: 12, color: "var(--text-dim)" }}>{durationLabel(took)}</td>
                          <td style={{ fontSize: 12, color: "var(--text-dim)" }} title={exactTime(job.finished)}>{relativeTime(job.finished)}</td>
                          <td>
                            <div style={{ display: "flex", gap: 6 }}>
                              <button type="button" className="la-records-btn" onClick={() => browseTo(job.client_id, job.platform)} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6, cursor: "pointer", background: "rgba(54,181,160,0.1)", border: "1px solid rgba(54,181,160,0.3)", color: "var(--text-muted)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 4 }}>
                                <DatabaseIcon size={11} /> Records
                              </button>
                              <button type="button" className="la-action-btn" onClick={() => toggleExpand(job.id)} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6, cursor: "pointer", background: isExpanded ? "rgba(124,92,255,0.15)" : "rgba(255,255,255,0.06)", border: `1px solid ${isExpanded ? "var(--accent,#7c5cff)" : "rgba(255,255,255,0.1)"}`, color: isExpanded ? "var(--accent,#7c5cff)" : "var(--text-muted)", fontWeight: 600 }}>{isExpanded ? "▾ Logs" : "▸ Logs"}</button>
                            </div>
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr key={`${job.id}-log`}>
                            <td colSpan={6} style={{ paddingTop: 0 }}>
                              <CyberTerminal jobId={job.id} onClose={() => toggleExpand(job.id)} />
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ══ TAB 3: RECORD MANAGER ══ */}
      {activeTab === "records" && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary,#fff)", margin: "0 0 4px 0", display: "flex", alignItems: "center", gap: 8 }}>
              <DatabaseIcon size={18} color="var(--cyan)" />
              <span>Browse & Manage Discovery Records</span>
            </h3>
            <p style={{ fontSize: 12, color: "var(--text-muted,#98a2b3)", margin: 0 }}>Inspect what each job saved to the database — filter, search, and permanently delete records.</p>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
            <select value={browseClientId} onChange={(e) => { setBrowseClientId(e.target.value); setOffset(0); }} style={LA_SELECT_STYLE}>
              <option value="">Select a client…</option>
              {clients.map((c) => <option key={c.client_id} value={c.client_id}>{c.name || c.client_id}</option>)}
            </select>
            <select value={browsePlatform} onChange={(e) => { setBrowsePlatform(e.target.value); setOffset(0); }} style={LA_SELECT_STYLE}>
              <option value="">All platforms</option>
              {["facebook", "instagram", "twitter", "youtube", "telegram", "tiktok"].map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <select value={browseStatus} onChange={(e) => { setBrowseStatus(e.target.value); setOffset(0); }} style={LA_SELECT_STYLE}>
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
            </select>
            <div style={{ position: "relative", flex: "1 1 200px", display: "flex", alignItems: "center" }}>
              <SearchIcon size={12} color="var(--text-dim)" style={{ position: "absolute", left: 8 }} />
              <input value={browseSearch} onChange={(e) => setBrowseSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (setOffset(0), loadProfiles())} placeholder="Search name / URL…" style={{ ...LA_SELECT_STYLE, width: "100%", paddingLeft: 26 }} />
            </div>
            <button type="button" onClick={() => { setOffset(0); loadProfiles(); }} style={{ ...LA_SELECT_STYLE, cursor: "pointer", fontWeight: 700 }}>Search</button>
          </div>
          {!browseClientId ? (
            <EmptyState icon="🔍" text="Select a client above, or click 'Records' on any job to jump straight here." />
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  {browseLoading ? "Loading…" : `${profilesTotal} record(s)`}{selected.size > 0 && ` · ${selected.size} selected`}
                </span>
                <button type="button" onClick={deleteSelected} disabled={selected.size === 0 || deleting} style={{ padding: "6px 14px", borderRadius: 8, background: selected.size ? "rgba(233,80,83,0.15)" : "var(--bg-surface-3,#1d2939)", border: `1px solid ${selected.size ? "rgba(233,80,83,0.4)" : "rgba(255,255,255,0.1)"}`, color: selected.size ? "var(--danger,#e95053)" : "var(--text-dim)", fontSize: 12, fontWeight: 700, cursor: selected.size ? "pointer" : "not-allowed", display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <TrashIcon size={13} /> Delete selected{selected.size > 0 ? ` (${selected.size})` : ""}
                </button>
              </div>
              <div style={{ overflowX: "auto" }}>
                <table className="core_table">
                  <thead>
                    <tr>
                      <th><input type="checkbox" checked={profiles.length > 0 && selected.size === profiles.length} onChange={toggleAll} /></th>
                      <th>Platform</th><th>Name</th><th>URL</th><th>Status</th><th>Risk</th><th>Phase</th><th>Last seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profiles.length === 0 ? (
                      <tr><td colSpan={8} style={{ textAlign: "center", padding: 24, color: "var(--text-dim)" }}>{browseLoading ? "Loading…" : "No records match these filters."}</td></tr>
                    ) : profiles.map((p) => (
                      <tr key={p.id}>
                        <td><input type="checkbox" checked={selected.has(p.id)} onChange={() => toggleOne(p.id)} /></td>
                        <td style={{ textTransform: "capitalize" }}>{PLATFORM_ICON[p.platform] || ""} {p.platform}</td>
                        <td style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.profile_name || "—"}</td>
                        <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          <a href={p.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent,#7c5cff)" }}>{p.url}</a>
                        </td>
                        <td><Badge color={PROFILE_STATUS_BADGE[p.status] ?? "var(--text-dim)"}>{p.status}</Badge></td>
                        <td>{p.risk_score ?? "—"}</td>
                        <td style={{ textTransform: "capitalize" }}>{p.phase || "—"}</td>
                        <td title={exactTime(p.analysed_at)}>{relativeTime(p.analysed_at) !== "—" ? relativeTime(p.analysed_at) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ display: "flex", justifyContent: "center", gap: 12, alignItems: "center", marginTop: 12 }}>
                <button type="button" onClick={() => setOffset(Math.max(0, offset - BROWSE_PAGE_SIZE))} disabled={offset === 0} style={{ ...LA_SELECT_STYLE, cursor: offset === 0 ? "not-allowed" : "pointer" }}>← Prev</button>
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{offset + 1}–{Math.min(offset + BROWSE_PAGE_SIZE, profilesTotal)} of {profilesTotal}</span>
                <button type="button" onClick={() => setOffset(offset + BROWSE_PAGE_SIZE)} disabled={offset + BROWSE_PAGE_SIZE >= profilesTotal} style={{ ...LA_SELECT_STYLE, cursor: offset + BROWSE_PAGE_SIZE >= profilesTotal ? "not-allowed" : "pointer" }}>Next →</button>
              </div>
            </>
          )}
        </div>
      )}

      {/* == TAB 4: RETRY QUEUE == */}
      {activeTab === "retry" && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary,#fff)", margin: "0 0 4px 0", display: "flex", alignItems: "center", gap: 8 }}>
              <RefreshIcon size={18} color="var(--cyan)" />
              <span>Retry Queue</span>
            </h3>
            <p style={{ fontSize: 12, color: "var(--text-muted,#98a2b3)", margin: 0 }}>
              Every approved profile analysis has not finished with -- reached but incomplete, never reached at
              all, or manually stopped. Eligible rows are picked up automatically by the next catch-up sweep;
              Exhausted and Stopped rows need Resume, or are a known, accepted coverage gap.
            </p>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
            <select value={retryClientId} onChange={(e) => setRetryClientId(e.target.value)} style={LA_SELECT_STYLE}>
              <option value="">Select a client...</option>
              {clients.map((c) => <option key={c.client_id} value={c.client_id}>{c.name || c.client_id}</option>)}
            </select>
            <select value={retryPlatform} onChange={(e) => setRetryPlatform(e.target.value)} style={LA_SELECT_STYLE}>
              <option value="">All platforms</option>
              {["facebook", "instagram", "twitter", "youtube", "telegram", "tiktok"].map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <select value={retryStateFilter} onChange={(e) => setRetryStateFilter(e.target.value as typeof retryStateFilter)} style={LA_SELECT_STYLE}>
              <option value="">Every state</option>
              <option value="eligible">Eligible only</option>
              <option value="exhausted">Exhausted only</option>
              <option value="stopped">Stopped only</option>
            </select>
            <button type="button" onClick={loadRetryQueue} disabled={!retryClientId || retryLoading} style={{ ...LA_SELECT_STYLE, cursor: retryClientId ? "pointer" : "not-allowed", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <RefreshIcon size={12} /> Refresh
            </button>
          </div>

          {!retryClientId ? (
            <EmptyState icon="[refresh]" text="Select a client above to see which of its approved profiles analysis has not finished with." />
          ) : (
            <>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 14, padding: "12px 16px", background: "var(--bg-surface,#1e2837)", border: "1px solid rgba(124,92,255,0.2)", borderRadius: 12 }}>
                {([
                  { key: "eligible" as const,  label: "Eligible (auto-retrying)" },
                  { key: "exhausted" as const, label: "Exhausted (needs Resume)" },
                  { key: "stopped" as const,   label: "Stopped (by an analyst)" },
                ]).map((stat) => (
                  <div key={stat.key} style={{ flex: "1 1 160px", display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" }}>{stat.label}</span>
                    <span style={{ fontSize: 17, fontWeight: 800, color: RETRY_STATE_LOOK[stat.key].color }}>{retryCounts[stat.key]}</span>
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  {retryLoading ? "Loading..." : `${visibleRetryItems.length} row(s)`}{retrySelected.size > 0 && ` · ${retrySelected.size} selected`}
                </span>
                <button
                  type="button"
                  onClick={stopSelectedRetries}
                  disabled={retrySelected.size === 0 || retryBulkBusy}
                  style={{ padding: "6px 14px", borderRadius: 8, background: retrySelected.size ? "rgba(233,80,83,0.15)" : "var(--bg-surface-3,#1d2939)", border: `1px solid ${retrySelected.size ? "rgba(233,80,83,0.4)" : "rgba(255,255,255,0.1)"}`, color: retrySelected.size ? "var(--danger,#e95053)" : "var(--text-dim)", fontSize: 12, fontWeight: 700, cursor: retrySelected.size ? "pointer" : "not-allowed", display: "inline-flex", alignItems: "center", gap: 5 }}
                >
                  <StopIcon size={13} /> Stop selected{retrySelected.size > 0 ? ` (${retrySelected.size})` : ""}
                </button>
              </div>

              <div style={{ overflowX: "auto" }}>
                <table className="core_table">
                  <thead>
                    <tr>
                      <th><input type="checkbox" checked={visibleRetryItems.length > 0 && retrySelected.size === visibleRetryItems.length} onChange={toggleRetrySelectedAll} /></th>
                      <th>Platform</th><th>Name</th><th>URL</th><th>State</th><th>Why</th><th>Attempts</th><th>Last attempt</th><th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRetryItems.length === 0 ? (
                      <tr><td colSpan={9} style={{ textAlign: "center", padding: 24, color: "var(--text-dim)" }}>{retryLoading ? "Loading..." : "Nothing in the retry queue for this client -- every approved profile is either fully read or still on its first pass."}</td></tr>
                    ) : visibleRetryItems.map((p) => {
                      const look = RETRY_STATE_LOOK[p.retry_state ?? "eligible"];
                      const acting = retryActingId === p.id;
                      return (
                        <tr key={p.id}>
                          <td><input type="checkbox" checked={retrySelected.has(p.id)} onChange={() => toggleRetrySelected(p.id)} /></td>
                          <td style={{ textTransform: "capitalize" }}>{PLATFORM_ICON[p.platform] || ""} {p.platform}</td>
                          <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.profile_name || "—"}</td>
                          <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            <a href={p.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent,#7c5cff)" }}>{p.url}</a>
                          </td>
                          <td><Badge color={look.color}>{look.label}</Badge></td>
                          <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12, color: "var(--text-dim)" }} title={p.retry_reason}>{p.retry_reason || "—"}</td>
                          <td style={{ fontSize: 12, color: "var(--text-dim)" }}>{p.analysis_attempts ?? 0}</td>
                          <td title={exactTime(p.analysed_at)}>{relativeTime(p.analysed_at) !== "—" ? relativeTime(p.analysed_at) : "—"}</td>
                          <td>
                            {p.retry_state === "stopped" ? (
                              <button type="button" onClick={() => resumeOne(p.id)} disabled={acting} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6, cursor: acting ? "wait" : "pointer", background: "rgba(54,181,160,0.1)", border: "1px solid rgba(54,181,160,0.3)", color: "var(--success,#36b5a0)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 4 }}>
                                <PlayIcon size={11} /> {acting ? "..." : "Resume"}
                              </button>
                            ) : (
                              <button type="button" onClick={() => stopOne(p.id)} disabled={acting} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6, cursor: acting ? "wait" : "pointer", background: "rgba(233,80,83,0.1)", border: "1px solid rgba(233,80,83,0.3)", color: "var(--danger,#e95053)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 4 }}>
                                <StopIcon size={11} /> {acting ? "..." : "Stop"}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
