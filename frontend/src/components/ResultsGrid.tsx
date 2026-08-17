import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import toast from "react-hot-toast";
import { analysisApi } from "../api/analysisApi";
import { clientsApi } from "../api/clientsApi";
import { discoveryApi } from "../api/discoveryApi";
import { jobsApi } from "../api/jobsApi";
import { profilesApi } from "../api/profilesApi";
import type { Coverage, JobEvent, PlatformHealth, PlatformProgress, Profile, Status } from "../api/types";
import { PlatformIcon } from "./PlatformIcon";
import {
  ageLabel,
  analysisWasBlocked,
  computeIncidentRiskScorePreview,
  emptyLabel,
  filterResults,
  logoMatchOf,
  riskLabel,
  sortResults,
  usernameMatchOf,
  type ExtraFilters,
  type ResultFilters,
} from "../services/resultsFilter";
import { toIncidentExportRows } from "../services/incidentExport";
import { toLegacyExportRows } from "../services/legacyExport";
import { download, downloadBlob, rowsToCsv, rowsToTsv } from "../utils/download";
import { confirmAction } from "../utils/confirmAction";

interface Props {
  clientId: string;
  platforms: PlatformHealth[];
  discoveryRunning: boolean;
  discoveryLog: JobEvent[];
  discoveryProgress: Record<string, PlatformProgress>;
  analysisRunning: boolean;
  analysisLog: JobEvent[];
  analysisProgress: Record<string, PlatformProgress>;
  onStopDiscovery?: () => void;
  onStopAnalysis?: () => void;
  onError?: (msg: string) => void;
}

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;
const EXPORT_LIMIT = 1000;
// how long an approve/reject/validate stays undo-able before the toast
// disappears, long enough to catch a misclick, short enough that "undo"
// never becomes a second, confusing source of truth for a profile's status
const UNDO_WINDOW_MS = 8000;

// "5s" / "2m 30s" / "1h 5m", never both units at zero, never blank.
function formatEta(seconds: number | null): string {
  if (seconds === null || seconds < 0) return "";
  if (seconds < 5) return "almost done";
  if (seconds < 60) return `~${Math.round(seconds)}s left`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins < 60) return `~${mins}m${secs ? ` ${secs}s` : ""} left`;
  const hrs = Math.floor(mins / 60);
  return `~${hrs}h ${mins % 60}m left`;
}

const PLATFORM_STATUS_LOOK: Record<PlatformProgress["status"], { icon: string; color: string }> = {
  pending: { icon: "⏳", color: "var(--text-dim)" },
  running: { icon: "⚙️", color: "var(--cyan)" },
  done: { icon: "✅", color: "var(--success)" },
  // covered only part of what was asked: a result cap fired, the session
  // pool ran dry mid-run, or a sweep stalled. The job's `message` says
  // which. This used to be reported as "done" with processed forced to the
  // full total, so a run that visited 12 of 200 profiles showed 200/200 ✅
  // and nothing anywhere contradicted it.
  partial: { icon: "🟡", color: "var(--warn-yellow)" },
  failed: { icon: "⚠️", color: "var(--danger)" },
  // never attempted at all (session wasn't ready when the sweep started),
  // distinct from "failed" so the fix is obvious: check Sessions, not retry
  // and hope. Previously a skipped platform had no progress entry
  // whatsoever, so it just silently vanished from the sweep with nothing
  // in the UI explaining why.
  skipped: { icon: "🚫", color: "var(--warn-yellow)" },
};

function PlatformProgressRow({ label, progress }: { label: string; progress: PlatformProgress }) {
  const look = PLATFORM_STATUS_LOOK[progress.status];
  const pct = progress.total > 0 ? Math.min(100, Math.round((progress.processed / progress.total) * 100)) : 0;
  return (
    <div style={{ marginTop: "4px" }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: "5px",
          fontSize: "10px", fontFamily: "var(--font-mono)", color: look.color,
        }}
      >
        <span>{look.icon}</span>
        <span>{label}</span>
        <span style={{ flex: 1 }} />
        <span>
          {progress.processed}/{progress.total || "?"}
        </span>
      </div>
      {progress.status === "running" && (
        <>
          <div
            style={{
              height: "3px", background: "var(--bg-inner)", borderRadius: "999px",
              overflow: "hidden", marginTop: "3px",
            }}
          >
            <div
              style={{
                height: "100%", width: `${pct || 4}%`,
                background: "linear-gradient(90deg, var(--cyan), var(--purple))",
                transition: "width 0.4s ease",
              }}
            />
          </div>
          {progress.eta_seconds !== null && (
            <div style={{ fontSize: "9px", color: "var(--text-dim)", marginTop: "2px" }}>
              {formatEta(progress.eta_seconds)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LiveFeed({ title, log }: { title: string; log: JobEvent[] }) {
  return (
    <details className="dashboard-card-box" style={{ marginTop: "12px" }} open>
      <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: "12px" }}>
        {title} ({log.length})
      </summary>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          marginTop: "8px",
          maxHeight: "160px",
          overflowY: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
        }}
      >
        {log.slice(-30).map((e, i) => (
          <li key={i} style={{ padding: "3px 0", display: "flex", gap: "8px" }}>
            <span style={{ color: "var(--cyan)" }}>[{e.type}]</span>
            <span style={{ color: "var(--text-main)" }}>{e.message}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}


// A profile only ever reappears in "pending" after being rejected if a
// rediscovery actually observed a real change (display name and/or logo,
// see backend's RECONSIDER_FIELDS), this turns that raw {field: {old,
// new}} diff into a readable one-liner, so the analyst sees WHY it's back
// instead of having to trust the queue blindly.
const CHANGE_FIELD_LABELS: Record<string, string> = { display_name: "name", has_logo: "logo" };
function changeSummary(changes?: Record<string, { old: unknown; new: unknown }> | null): string {
  if (!changes || !Object.keys(changes).length) return "";
  return Object.entries(changes)
    .map(([f, { old, new: next }]) => `${CHANGE_FIELD_LABELS[f] ?? f}: ${old ?? "—"} → ${next ?? "—"}`)
    .join("; ");
}

function VisualDiffModal({ profile, onClose }: { profile: Profile; onClose: () => void }) {
  if (!profile.changes) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(8,15,30,0.8)",
        backdropFilter: "blur(8px)", zIndex: 10000, display: "flex",
        alignItems: "center", justifyContent: "center", padding: "20px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="dashboard-card-box"
        style={{ width: "min(560px, 100%)", background: "var(--bg-card)" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "10px" }}>
          <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
            <span>🔄 Change History</span>
            <span style={{ fontSize: "12px", color: "var(--text-dim)", fontWeight: 400 }}>({profile.profile_name || profile.username || "Profile"})</span>
          </div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--text-muted)", fontSize: "16px", cursor: "pointer" }}>✕</button>
        </div>

        <div style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "12px" }}>
          This profile was previously rejected. A rediscovery detected the following updates:
        </div>

        <table className="diff-table">
          <thead>
            <tr>
              <th>Field</th>
              <th>Previous Value</th>
              <th>New Detected Value</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(profile.changes).map(([field, { old, new: next }]) => (
              <tr key={field}>
                <td style={{ fontWeight: 600, color: "var(--text-main)", textTransform: "capitalize" }}>
                  {CHANGE_FIELD_LABELS[field] ?? field.replace(/_/g, " ")}
                </td>
                <td>
                  <span className="diff-old-val">
                    {old === true ? "Yes" : old === false ? "No" : String(old ?? "None")}
                  </span>
                </td>
                <td>
                  <span className="diff-new-val">
                    {next === true ? "Yes" : next === false ? "No" : String(next ?? "None")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "16px" }}>
          <button type="button" onClick={onClose} className="btn-cyber-primary" style={{ width: "auto", padding: "7px 18px", fontSize: "12px" }}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function ShortcutsModal({ onClose }: { onClose: () => void }) {
  const shortcuts = [
    { key: "J / ↓", desc: "Move focus to next row/card" },
    { key: "K / ↑", desc: "Move focus to previous row/card" },
    { key: "Space", desc: "Toggle selection checkbox for focused row" },
    { key: "V", desc: "Validate focused profile (Discovery)" },
    { key: "X", desc: "Reject focused profile" },
    { key: "E", desc: "Open incident Edit Drawer (Analysis)" },
    { key: "P", desc: "Publish focused finding (Analysis)" },
    { key: "I", desc: "Toggle side-by-side Live Inspection pane" },
    { key: "Ctrl + Z", desc: "Undo last triage decision" },
    { key: "Esc", desc: "Close modals / clear selection" },
    { key: "?", desc: "Toggle this shortcuts guide" },
  ];
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(8,15,30,0.8)",
        backdropFilter: "blur(8px)", zIndex: 10000, display: "flex",
        alignItems: "center", justifyContent: "center", padding: "20px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="dashboard-card-box"
        style={{ width: "min(480px, 100%)", background: "var(--bg-card)" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "10px" }}>
          <div style={{ fontSize: "15px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
            <span>⌨️ Keyboard Shortcuts</span>
          </div>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: "var(--text-muted)", fontSize: "16px", cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px", margin: "10px 0 16px" }}>
          {shortcuts.map((s) => (
            <div key={s.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "12px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "6px" }}>
              <span style={{ color: "var(--text-muted)" }}>{s.desc}</span>
              <span className="kbd-badge">{s.key}</span>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button type="button" onClick={onClose} className="btn-cyber-primary" style={{ width: "auto", padding: "7px 18px", fontSize: "12px" }}>
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}

function LiveInspectionPane({
  profile,
  isAnalysisView,
  onValidate,
  onReject,
  onEdit,
  onClose,
}: {
  profile: Profile;
  isAnalysisView: boolean;
  onValidate: (id: string) => void;
  onReject: (id: string) => void;
  onEdit: (id: string) => void;
  onClose: () => void;
}) {
  const inc = profile.incident;
  const name = isAnalysisView && inc ? inc.title : profile.profile_name || profile.username || profile.url;
  const linkUrl = isAnalysisView && inc ? inc.source : profile.url;

  return (
    <div className="live-inspection-pane">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
        <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--cyan)" }}>
          🖥️ Live Inspection
        </div>
        <button
          onClick={onClose}
          style={{ background: "transparent", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: "14px" }}
          title="Close split inspection pane"
        >
          ✕
        </button>
      </div>

      <div className="inspection-header">
        <div style={{ width: "40px", height: "40px", flexShrink: 0 }}>
          <ProfileAvatar r={profile} size={40} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--text-main)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {name}
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: "6px", marginTop: "2px" }}>
            <span style={{ textTransform: "capitalize" }}>{profile.platform}</span>
            {profile.username && <span>@{profile.username}</span>}
          </div>
        </div>
        <a
          href={linkUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            background: "rgba(0, 229, 255, 0.1)",
            border: "1px solid rgba(0, 229, 255, 0.3)",
            color: "var(--cyan)",
            padding: "4px 8px",
            borderRadius: "6px",
            fontSize: "11px",
            fontWeight: 600,
            textDecoration: "none",
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            whiteSpace: "nowrap",
          }}
          title="Open in new browser tab"
        >
          🔗 Open ↗
        </a>
      </div>

      <div className="inspection-stat-grid">
        <div className="inspection-stat-box">
          <div className="inspection-stat-label">Followers</div>
          <div className="inspection-stat-val">
            {profile.followers !== null && profile.followers !== undefined ? Number(profile.followers).toLocaleString() : "—"}
          </div>
        </div>
        <div className="inspection-stat-box">
          <div className="inspection-stat-label">{isAnalysisView ? "Risk Score" : "Match Score"}</div>
          <div className="inspection-stat-val" style={{ color: isAnalysisView ? (Number(inc?.riskRating || 0) >= 8 ? "var(--alert-red)" : "var(--warn-yellow)") : "var(--cyan)" }}>
            {isAnalysisView ? (inc?.riskRating || "—") : (profile.name_score !== null && profile.name_score !== undefined ? `${profile.name_score}%` : "—")}
          </div>
        </div>
        <div className="inspection-stat-box">
          <div className="inspection-stat-label">Status</div>
          <div className="inspection-stat-val" style={{ fontSize: "11px", textTransform: "uppercase" }}>
            {profile.status}
          </div>
        </div>
      </div>

      <div>
        <div className="inspection-stat-label" style={{ marginBottom: "4px" }}>Bio / Description</div>
        <div className="inspection-bio-box">
          {((profile as unknown as Record<string, unknown>).bio as string) || profile.comments || profile.incident?.description || "(No bio or description extracted)"}
        </div>
      </div>

      <div style={{ fontSize: "11px", color: "var(--text-dim)", display: "flex", flexDirection: "column", gap: "4px", padding: "4px 2px" }}>
        <div><strong>Keyword:</strong> {profile.keyword || "—"}</div>
        {profile.location && <div><strong>Location:</strong> {profile.location}</div>}
        {profile.last_post_date && <div><strong>Last Post:</strong> {profile.last_post_date}</div>}
        {profile.analysed_at && <div><strong>Analysed:</strong> {new Date(profile.analysed_at).toLocaleString()}</div>}
      </div>

      <div className="inspection-actions-row">
        {!isAnalysisView ? (
          <>
            <button
              className="btn-accept"
              onClick={() => onValidate(profile.id)}
              style={{ fontSize: "12px", padding: "6px 0" }}
            >
              ✓ Validate (V)
            </button>
            <button
              className="btn-reject"
              onClick={() => onReject(profile.id)}
              style={{ fontSize: "12px", padding: "6px 0" }}
            >
              ✕ Reject (X)
            </button>
          </>
        ) : (
          <button
            className="action-btn"
            onClick={() => onEdit(profile.id)}
            style={{ flex: 1, fontSize: "12px", padding: "6px 0" }}
          >
            ✏️ Edit Finding (E)
          </button>
        )}
      </div>
    </div>
  );
}

function ProfileAvatar({ r, size, style }: { r: Profile; size?: number; style?: React.CSSProperties }) {
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
  }, [r.profile_image_url]);

  if (!r.profile_image_url || error) {
    return (
      <span
        className="profile-avatar-circle"
        style={size ? { width: size, height: size, fontSize: size * 0.45, borderRadius: "50%", flexShrink: 0, ...style } : style}
      >
        {(r.profile_name || r.username || "?").charAt(0).toUpperCase()}
      </span>
    );
  }
  const src = r.profile_image_url.startsWith("http")
    ? `/profiles/media-proxy?url=${encodeURIComponent(r.profile_image_url)}`
    : r.profile_image_url;
  return (
    <img
      src={src}
      alt=""
      referrerPolicy="no-referrer"
      loading="lazy"
      style={size ? { width: size, height: size, borderRadius: "50%", objectFit: "cover", flexShrink: 0, ...style } : { width: "100%", height: "100%", objectFit: "cover", ...style }}
      onError={() => setError(true)}
    />
  );
}

// Direct "jump to page N" input. Prev/Next alone means clicking dozens of
// times to cross a 1000-profile, 40-page listing. Commits on Enter/blur
// (not on every keystroke) so a half-typed number never jumps mid-edit.
function PageJumpInput({ currentPage, pageCount, onJump }: { currentPage: number; pageCount: number; onJump: (page: number) => void }) {
  const [value, setValue] = useState(String(currentPage));

  useEffect(() => {
    setValue(String(currentPage));
  }, [currentPage]);

  const commit = () => {
    const n = Math.round(Number(value));
    if (Number.isFinite(n) && n > 0) {
      onJump(Math.min(pageCount, Math.max(1, n)));
    } else {
      setValue(String(currentPage));
    }
  };

  return (
    <input
      type="number"
      min={1}
      max={pageCount}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        }
      }}
      onBlur={commit}
      className="page-jump-input"
      title="Jump to page"
    />
  );
}

// One inline-editable text/number field bound to one dotted path in a
// profile's incident_overrides (see backend/services/incident_publisher.py
//, build_incident_doc merges these onto the computed preview, and
// Publish writes the merged result). Uncontrolled + onBlur, same pattern
// as the table view's saveField inputs, so a keystroke doesn't PATCH.
function IncidentField({
  label, value, path, onSave, type = "text",
}: {
  label: string; value: string | number | null | undefined; path: string;
  onSave: (path: string, value: string) => void; type?: "text" | "number";
}) {
  return (
    <label className="incident-field">
      <span className="incident-field-label">{label}</span>
      <input
        type={type}
        defaultValue={value ?? ""}
        className="input-filter"
        onBlur={(e) => {
          if (e.target.value !== String(value ?? "")) onSave(path, e.target.value);
        }}
      />
    </label>
  );
}

// Asset Name field, sourced from the client's standalone `drk_keywords`
// list (see HomeView.tsx's "Asset Names" tab) so an analyst picks a
// pre-approved name instead of retyping one, but always falls back to
// free text, both when the client has no drk_keywords configured yet and
// via the explicit "Custom…" option, so nothing already saved is ever
// clobbered by an empty options list.
function IncidentAssetNameField({
  value, path, onSave, options,
}: {
  value: string | number | null | undefined; path: string;
  onSave: (path: string, value: string) => void; options: string[];
}) {
  const CUSTOM = "__custom__";
  const current = String(value ?? "");
  const [customMode, setCustomMode] = useState(options.length === 0 || (!!current && !options.includes(current)));

  useEffect(() => {
    setCustomMode(options.length === 0 || (!!current && !options.includes(current)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  if (customMode) {
    return (
      <label className="incident-field">
        <span className="incident-field-label">Asset Name</span>
        <input
          type="text"
          defaultValue={current}
          className="input-filter"
          onBlur={(e) => {
            if (e.target.value !== current) onSave(path, e.target.value);
          }}
        />
        {options.length > 0 && (
          <button
            type="button"
            className="bulk-kw-toggle"
            style={{ marginTop: "4px" }}
            onClick={() => setCustomMode(false)}
          >
            ▾ Pick from Asset Names list instead
          </button>
        )}
      </label>
    );
  }

  return (
    <label className="incident-field">
      <span className="incident-field-label">Asset Name</span>
      <select
        className="input-filter"
        value={options.includes(current) ? current : ""}
        onChange={(e) => {
          if (e.target.value === CUSTOM) {
            setCustomMode(true);
            return;
          }
          onSave(path, e.target.value);
        }}
      >
        <option value="" disabled>
          Select an asset name…
        </option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
        <option value={CUSTOM}>Custom…</option>
      </select>
    </label>
  );
}

function IncidentCheckField({
  label, value, path, onSave,
}: {
  label: string; value: boolean | null | undefined; path: string;
  onSave: (path: string, value: string) => void;
}) {
  const [checked, setChecked] = useState(!!value);
  useEffect(() => setChecked(!!value), [value]);
  return (
    <label className="incident-field-check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => {
          setChecked(e.target.checked);
          onSave(path, String(e.target.checked));
        }}
      />
      {label}
    </label>
  );
}

// The evidence capture taken while analysis was reading this profile.
//
// This is the deliverable, not decoration: an impersonating account is
// routinely suspended or deleted before a takedown request is even read, so
// this screenshot is frequently the only surviving proof it existed and
// looked the way the incident says it did. Click to open full size; the
// download link produces a file suitable for attaching to a report.
function EvidenceShot({ r }: { r: Profile }) {
  const [open, setOpen] = useState(false);
  const [broken, setBroken] = useState(false);
  const src = profilesApi.screenshotUrl(r);

  // no capture: say WHY, because "missing evidence" and "evidence not
  // applicable to this platform" are very different for an analyst deciding
  // whether a finding is report-ready
  if (!src) {
    const reason =
      r.platform === "youtube" || r.platform === "telegram"
        ? "No screenshot — this platform is read through its API, not a browser page."
        : analysisWasBlocked(r)
          ? `No screenshot — analysis could not open this profile (${r.analysis_status}).`
          : r.phase !== "analysis"
            ? "No screenshot — this profile hasn't been analysed yet."
            : "No screenshot captured for this profile.";
    return <div className="evidence-empty">{reason}</div>;
  }

  if (broken) {
    return (
      <div className="evidence-empty">
        Screenshot is recorded for this profile but the image file is missing from the evidence store.
      </div>
    );
  }

  return (
    <div className="evidence-block">
      <div className="evidence-head">
        <span className="evidence-label">📸 Evidence capture</span>
        {r.screenshot_at && (
          <span className="evidence-when" title={r.screenshot_at}>
            {ageLabel(r.screenshot_at)}
          </span>
        )}
        <a
          className="evidence-download"
          href={profilesApi.screenshotUrl(r, { download: true })}
          download
          onClick={(e) => e.stopPropagation()}
          title="Download the full-size PNG to attach to a takedown request"
        >
          ⬇ Download
        </a>
      </div>
      <img
        src={src}
        alt={`Screenshot of ${r.profile_name || r.url} captured during analysis`}
        className="evidence-thumb"
        // Deliberately NOT loading="lazy": this panel is only rendered once
        // an analyst opens a specific profile, so there is nothing to defer,
        // and deferring inside a freshly-mounted panel just risks the image
        // never being requested at all.
        decoding="async"
        onError={() => setBroken(true)}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      />
      {open && (
        <div
          className="evidence-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="Evidence screenshot, full size"
          onClick={() => setOpen(false)}
        >
          <img src={src} alt={`Full-size screenshot of ${r.profile_name || r.url}`} />
          <button className="evidence-close" onClick={() => setOpen(false)} aria-label="Close">
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

// Platforms this engine can actually capture a screenshot on. YouTube and
// Telegram are read through an API, never a browser page, so there is never
// anything to show there (see EvidenceShot's own empty-state reasoning
// above). A dedicated, always-visible popup for the analysis table, distinct
// from EvidenceShot's copy inside the full incident-edit drawer, an
// analyst shouldn't have to open Edit just to glance at the evidence.
const SCREENSHOT_POPUP_PLATFORMS = new Set(["facebook", "twitter", "instagram", "tiktok"]);

function ScreenshotCell({ r }: { r: Profile }) {
  const [open, setOpen] = useState(false);
  const [broken, setBroken] = useState(false);

  if (!SCREENSHOT_POPUP_PLATFORMS.has(r.platform)) {
    return <span style={{ color: "var(--text-dim)" }}>—</span>;
  }

  const src = profilesApi.screenshotUrl(r);
  if (!src || broken) {
    const reason = broken
      ? "Screenshot is recorded for this profile but the image file is missing from the evidence store."
      : analysisWasBlocked(r)
        ? `No screenshot — analysis could not open this profile (${r.analysis_status}).`
        : r.phase !== "analysis"
          ? "No screenshot — this profile hasn't been analysed yet."
          : "No screenshot captured for this profile.";
    return (
      <span style={{ color: "var(--text-dim)", fontSize: "11px" }} title={reason}>
        —
      </span>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        title="View the evidence screenshot captured during analysis"
        style={{
          padding: 0, border: "1px solid var(--border-color)", borderRadius: "6px",
          overflow: "hidden", cursor: "pointer", background: "none", width: "44px", height: "44px",
          display: "block", flexShrink: 0,
        }}
      >
        <img
          src={src}
          alt={`Screenshot of ${r.profile_name || r.url}`}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
          decoding="async"
          onError={() => setBroken(true)}
        />
      </button>
      {open && (
        <div
          className="evidence-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="Evidence screenshot, full size"
          onClick={(e) => {
            e.stopPropagation();
            setOpen(false);
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <img src={src} alt={`Full-size screenshot of ${r.profile_name || r.url}`} />
          <button
            className="evidence-close"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
            }}
            onMouseDown={(e) => e.stopPropagation()}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
      )}
    </>
  );
}

// The full client-facing published-incident record, this IS the analysis
// view's field set now (the old profile_name/username/followers/location/
// last_post_date/risk_score/priority/comments fields are gone from this
// view entirely, replaced by this exact shape). Always expanded: these
// aren't extra detail, they're the primary content of an analysis card.
function IncidentEditPanel({
  r, onSave, drkOptions = [], onToggleMatch,
}: {
  r: Profile; onSave: (path: string, value: string) => void; drkOptions?: string[];
  onToggleMatch?: (field: "username_match" | "logo_match", value: boolean) => void;
}) {
  const inc = r.incident;
  if (!inc) return null;
  return (
    <div className="incident-panel">
      <EvidenceShot r={r} />
      <div className="incident-panel-body">
        <IncidentField label="Title" value={inc.title} path="title" onSave={onSave} />
        <IncidentField label="Description" value={inc.description} path="description" onSave={onSave} />
        <IncidentField label="Category" value={inc.category} path="category" onSave={onSave} />
        <IncidentField label="Sub-Category" value={inc.subCategory} path="subCategory" onSave={onSave} />
        <IncidentField label="Asset Type" value={inc.assetType} path="assetType" onSave={onSave} />
        <IncidentField label="Asset Category" value={inc.assetCategory} path="assetCategory" onSave={onSave} />
        <IncidentAssetNameField value={inc.assetName} path="assetName" onSave={onSave} options={drkOptions} />
        <IncidentField label="Domain" value={inc.domain} path="domain" onSave={onSave} />
        <IncidentField label="Org ID" value={inc.orgId} path="orgId" onSave={onSave} />
        <IncidentField label="Risk Score" value={inc.riskRating} path="riskRating" onSave={onSave} />
        <IncidentField label="Date" value={inc.date} path="date" onSave={onSave} />
        <IncidentField label="Source" value={inc.source} path="source" onSave={onSave} />
        <IncidentField
          label="Followers" type="number" value={inc.socialProfileInfo.numberOfFollowers}
          path="socialProfileInfo.numberOfFollowers" onSave={onSave}
        />
        <IncidentField
          label="Location" value={inc.socialProfileInfo.location}
          path="socialProfileInfo.location" onSave={onSave}
        />
        <IncidentField
          label="Last Post Date" value={inc.socialProfileInfo.lastPostDate}
          path="socialProfileInfo.lastPostDate" onSave={onSave}
        />
        <IncidentField
          label="Profile Name" value={inc.socialProfileInfo.profileName}
          path="socialProfileInfo.profileName" onSave={onSave}
        />
        <IncidentField
          label="Profile Image URL" value={inc.socialProfileInfo.profileImage}
          path="socialProfileInfo.profileImage" onSave={onSave}
        />
        <div className="incident-field-checks">
          <IncidentCheckField
            label="Active" value={inc.socialProfileInfo.isActive}
            path="socialProfileInfo.isActive" onSave={onSave}
          />
          {/* Both default to matched on a validated profile and are undone
              here, which is the only place they can be changed now, see
              usernameMatchOf/logoMatchOf for how the shown value resolves. */}
          <IncidentCheckField
            label="Username Match" value={usernameMatchOf(r)}
            path="socialProfileInfo.isSimilarName" onSave={(_, val) => onToggleMatch ? onToggleMatch("username_match", val === "true") : onSave("socialProfileInfo.isSimilarName", val)}
          />
          <IncidentCheckField
            label="Logo Match" value={logoMatchOf(r)}
            path="socialProfileInfo.isSimilarLogo" onSave={(_, val) => onToggleMatch ? onToggleMatch("logo_match", val === "true") : onSave("socialProfileInfo.isSimilarLogo", val)}
          />
          <IncidentCheckField label="Third Party" value={inc.thirdParty} path="thirdParty" onSave={onSave} />
        </div>
      </div>
    </div>
  );
}

interface CardProps {
  r: Profile;
  isAnalysisView: boolean;
  savingId: string | null;
  onDecide: (id: string, next: Status) => void;
  onValidate: (id: string) => void;
  onSaveIncidentField: (id: string, path: string, value: string) => void;
  drkOptions?: string[];
  // bulk-triage selection, discovery cards only (see the bulk action bar
  // in the main component); undefined/no-op for an analysis card.
  selected?: boolean;
  onToggleSelected?: (id: string) => void;
  // drag-to-select: mousedown+drag across cards adds each one to the
  // selection, see dragSelectHandlers() in the main component.
  dragHandlers?: { onMouseDown: (e: ReactMouseEvent) => void; onMouseEnter: () => void };
  onOpenDiff?: (p: Profile) => void;
}

// Mirrors backend shared/models/scoring.py::NAME_THRESHOLD (80), this used
// to be 100, which only a byte-perfect name match could ever reach, so the
// "High Match" badge/filter silently excluded every genuinely strong fuzzy
// match (confirmed live: profiles scoring 80-99 against their keyword).
const MATCH_EXACT_THRESHOLD = 80;
const MATCH_MEDIUM_THRESHOLD = 50;

// Risk-tier colour bands for the analysis card's Risk badge, two tiers
// only (High/Low), keyed off the numeric riskRating
// (backend/shared/models/incident_scoring.py) rather than the tool's own
// internal priority field.
// Colors keyed by the shared riskLabel() so the badge's color and text
// can never drift apart the way the label logic itself used to drift from
// exports before riskLabel() became the one place that owns the thresholds.
const RISK_BADGE_COLORS: Record<string, { color: string; bg: string }> = {
  High: { color: "#FF8000", bg: "rgba(255, 128, 0, 0.25)" },
  Low: { color: "#12B76A", bg: "rgba(18, 183, 106, 0.25)" },
  "—": { color: "#667085", bg: "rgba(102, 112, 133, 0.2)" },
};

function getRiskBadgeDetails(riskRating?: string | number | null) {
  const label = riskLabel(riskRating);
  const { color, bg } = RISK_BADGE_COLORS[label];
  if (label === "—") return { score: "—", label, color, bg };
  const num = parseFloat(String(riskRating).trim());
  const score = !isNaN(num) ? Math.round(num) : label === "High" ? 8 : 3;
  return { score, label, color, bg };
}

function riskBadgeColor(riskRating: string): string {
  return getRiskBadgeDetails(riskRating).color;
}

function ProfileCard({
  r, isAnalysisView, savingId, onDecide, onValidate, onSaveIncidentField, drkOptions, selected, onToggleSelected, dragHandlers, onOpenDiff,
}: CardProps) {
  const inc = r.incident;
  const name = isAnalysisView && inc ? inc.title : r.profile_name || r.username || r.url;
  const linkUrl = isAnalysisView && inc ? inc.source : r.url;
  const isHeld = isAnalysisView && r.published === false;
  const isDiscovery = !isAnalysisView;

  return (
    <div
      className="profile-card"
      {...(isDiscovery ? dragHandlers : undefined)}
      style={selected ? { outline: "2px solid var(--cyan)", outlineOffset: "-2px" } : undefined}
    >
      <div className="profile-card-header">
        {isDiscovery && onToggleSelected && (
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelected(r.id)}
            onClick={(e) => e.stopPropagation()}
            title="Select for bulk approve/reject"
            style={{ position: "absolute", top: "8px", left: "8px", zIndex: 2, width: "16px", height: "16px", cursor: "pointer" }}
          />
        )}
        <ProfileAvatar r={r} />
        <span
          className="card-badge-top-left"
          style={{
            background: r.status === "approved" ? "rgba(136,56,221,0.85)" : r.status === "rejected" ? "rgba(119,39,205,0.85)" : "rgba(154,80,233,0.85)",
            color: "#fff",
            left: isDiscovery && onToggleSelected ? "32px" : undefined,
          }}
        >
          {r.status}
        </span>
        {isAnalysisView && inc && (
          <span
            className="card-badge-top-right"
            style={{ background: riskBadgeColor(inc.riskRating), color: "#fff" }}
          >
            Risk {inc.riskRating}
          </span>
        )}
        {isDiscovery && r.name_score !== null && r.name_score !== undefined && (
          <span
            className="card-badge-top-right"
            title={`Name-to-keyword match score: ${r.name_score}/100`}
            style={{
              background: r.name_score >= MATCH_EXACT_THRESHOLD 
                ? "rgba(54,181,160,0.85)" 
                : r.name_score >= MATCH_MEDIUM_THRESHOLD 
                  ? "rgba(255,165,0,0.85)" 
                  : "rgba(255,80,80,0.85)",
              color: "#fff",
            }}
          >
            {r.name_score >= MATCH_EXACT_THRESHOLD 
              ? "🎯 High Match" 
              : r.name_score >= MATCH_MEDIUM_THRESHOLD 
                ? "🎯 Medium Match" 
                : "🎯 Low Match"}
          </span>
        )}
        <span className="card-badge-platform">
          <PlatformIcon platform={r.platform} size={14} />
          {r.platform}
        </span>
      </div>

      <div className="profile-card-body">
        <a href={linkUrl} target="_blank" rel="noreferrer" className="profile-display-name" style={{ color: "var(--text-main)" }} title={name}>
          {name}
        </a>
        {isAnalysisView && inc && <div className="profile-handle">{inc.category} · {inc.subCategory}</div>}

        {isDiscovery && !!r.keywords?.length && (
          <div className="card-keyword-tags">
            {r.keywords.map((kw) => (
              <span key={kw} className="card-keyword-tag">
                🔑 {kw}
              </span>
            ))}
          </div>
        )}

        {r.status === "pending" && changeSummary(r.changes) && (
          <div
            style={{
              fontSize: "11px", color: "var(--warn-yellow, #FDB71B)", background: "rgba(255,193,7,0.1)",
              border: "1px solid rgba(255,193,7,0.3)", borderRadius: "6px",
              padding: "4px 8px", marginTop: "2px",
            }}
            title="This profile was previously rejected -- a rediscovery found a real change, so it's back for another look"
          >
            🔄 Back for review — {changeSummary(r.changes)}
          </div>
        )}

        {isHeld && (
          <div
            style={{
              fontSize: "11px", color: "var(--purple)", background: "rgba(136,56,221,0.1)",
              border: "1px solid rgba(136,56,221,0.3)", borderRadius: "6px",
              padding: "4px 8px", marginTop: "2px",
            }}
            title="Not yet published — only visible inside this tool until explicitly published"
          >
            ⚠️ Not published
          </div>
        )}

        {isAnalysisView && (
          <IncidentEditPanel r={r} onSave={(path, value) => onSaveIncidentField(r.id, path, value)} drkOptions={drkOptions} />
        )}

        <div className="card-actions-row">
          {r.status !== "approved" && (
            <button
              className="btn-accept"
              disabled={savingId === r.id}
              onClick={() => onValidate(r.id)}
              title="Confirms this profile is impersonating the client and sends it to analysis, where logo and username both count as matched unless you undo them"
            >
              ✅ Validate
            </button>
          )}
          {r.status !== "rejected" && (
            <button className="btn-reject" disabled={savingId === r.id} onClick={() => onDecide(r.id, "rejected")}>
              ✕ Reject
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function ResultsGrid({
  clientId,
  platforms,
  discoveryRunning,
  discoveryLog,
  discoveryProgress,
  analysisRunning,
  analysisLog,
  analysisProgress,
  onStopDiscovery,
  onStopAnalysis,
  onError,
}: Props) {
  const [platform, setPlatform] = useState(platforms[0]?.platform ?? "");
  const [phase, setPhase] = useState<"discovery" | "analysis">("discovery");
  const [status, setStatus] = useState("pending");
  const [priority, setPriority] = useState("");
  const [sortOrder, setSortOrder] = useState<"recent" | "past">("recent");
  const [keywordFilter, setKeywordFilter] = useState("");
  const [matchLevel, setMatchLevel] = useState<"" | "high" | "medium" | "low">("");
  const [entityType, setEntityType] = useState<"" | "profile" | "page" | "group">("");
  const [keywordMatchType, setKeywordMatchType] = useState<"" | "individual" | "domain">("");
  // Which column layout the Export/Copy buttons use, analysis view only,
  // "incident" is the Platform Format shape (OrgId, Domain, AssetType, ...);
  // "legacy" is the tool's original raw-field layout (Original Name, IMPERSONATED, Profile name, ...)
  const [exportFormat, setExportFormat] = useState<"incident" | "legacy">("incident");
  const [copyMenuOpen, setCopyMenuOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);
  const [shortcutsHelpOpen, setShortcutsHelpOpen] = useState(false);
  const [diffProfile, setDiffProfile] = useState<Profile | null>(null);
  const copyMenuRef = useRef<HTMLDivElement>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);

  // Close menus on click outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (copyMenuRef.current && !copyMenuRef.current.contains(event.target as Node)) {
        setCopyMenuOpen(false);
      }
      if (exportMenuRef.current && !exportMenuRef.current.contains(event.target as Node)) {
        setExportMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);
  // Analysis-only Published/Unpublished tab, always exactly one, same
  // as the Discovery/Analysis phase tabs. Defaults to Unpublished: that's
  // the queue an analyst actually needs to work (still on hold, or awaiting
  // an explicit Publish), not the findings already out the door.
  const [publishedFilter, setPublishedFilter] = useState<"published" | "unpublished">("unpublished");
  const [searchQuery, setSearchQuery] = useState("");
  // Search is now a server query (it has to be, or it only ever searches
  // the page you happen to be on), so the raw keystroke value must not
  // drive it directly, or every character fires a request. `searchQuery`
  // stays the controlled input value; `debouncedSearch` is what load() uses.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchQuery.trim()), 300);
    return () => clearTimeout(t);
  }, [searchQuery]);
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZE_OPTIONS[0]);
  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");

  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<{ platforms: Record<string, number>; statuses: Record<string, number>; keywords: Record<string, number> }>({
    platforms: {},
    statuses: {},
    keywords: {},
  });
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  // Analysis-phase row currently open in the full-field edit drawer, see
  // the modal near the bottom of this component's JSX. Replaces having all
  // 18 incident fields permanently inline-editable in the table (a wall of
  // 50-160px-wide <input>s the analyst had to horizontal-scroll through);
  // the table now shows only the handful of fields worth scanning at a
  // glance, and editing the rest happens in one focused place.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [copyUrlState, setCopyUrlState] = useState<"idle" | "copied" | "failed">("idle");
  const [copyDataCache, setCopyDataCache] = useState<string | null>(null);

  // Manual URL entry, an analyst who already has a specific profile
  // link (a tip, a report, something an earlier sweep never turned up)
  // shouldn't have to invent a keyword just to get it into the pipeline.
  // See profilesApi.addManualUrls: each URL goes straight to "approved"
  // and analysis is auto-queued, same as any other approved card.
  //
  // Two separate boxes, not one, an analyst says up front whether a URL
  // is an executive/individual impersonation or a brand/domain one, so
  // incident_publisher's person-vs-brand classification doesn't depend on
  // the URL text happening to fuzzy-match a keyword the client has
  // configured (see profile_service.py::add_manual_urls's docstring for
  // the bug this closes: an executive not yet in the client's own
  // name_keywords had no way to land in the individual bucket at all).
  const [manualUrlsOpen, setManualUrlsOpen] = useState(false);
  const [manualUrlTab, setManualUrlTab] = useState<"individual" | "domain">("individual");
  const [manualIndividualUrlsText, setManualIndividualUrlsText] = useState("");
  const [manualDomainUrlsText, setManualDomainUrlsText] = useState("");
  const [manualUrlsBusy, setManualUrlsBusy] = useState(false);


  // The client's own configured keyword lists + standalone DRK asset-name
  // options, fetched once per client, used for the individual/domain
  // match filter (resultsFilter.ts's keywordMatchType) and the Asset Name
  // dropdown (see IncidentEditPanel), not re-fetched per profile.
  const [clientNameKeywords, setClientNameKeywords] = useState<string[]>([]);
  const [clientDomainKeywords, setClientDomainKeywords] = useState<string[]>([]);
  const [drkOptions, setDrkOptions] = useState<string[]>([]);
  useEffect(() => {
    if (!clientId) {

      setClientNameKeywords([]);
      setClientDomainKeywords([]);
      setDrkOptions([]);
      return;
    }
    let cancelled = false;
    clientsApi
      .getClient(clientId)
      .then((c) => {
        if (cancelled) return;

        setClientNameKeywords(c.name_keywords || []);
        setClientDomainKeywords(c.domain_keywords || []);
        setDrkOptions([
          ...(c.asset_name_individual_keywords || []),
          ...(c.asset_name_domain_keywords || []),
        ]);
      })
      .catch(() => {
        if (cancelled) return;

        setClientNameKeywords([]);
        setClientDomainKeywords([]);
        setDrkOptions([]);
      });
    return () => { cancelled = true; };
  }, [clientId]);
  const clientKeywordSets = useMemo(
    () => ({ nameKeywords: new Set(clientNameKeywords), domainKeywords: new Set(clientDomainKeywords) }),
    [clientNameKeywords, clientDomainKeywords],
  );

  // discovery-only multi-select for bulk approve/reject, keyed by profile
  // id so it survives a re-render/re-sort of the same underlying rows.
  // Cleared on any filter/page/client change so a selection never silently
  // carries over onto a different set of rows than the analyst was looking
  // at when they made it.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [undoStack, setUndoStack] = useState<Array<{ description: string; items: Array<{ id: string; prevStatus: Status }> }>>([]);
  const [splitViewOpen, setSplitViewOpen] = useState<boolean>(() => {
    try {
      return typeof window !== "undefined" && typeof localStorage !== "undefined" && typeof localStorage.getItem === "function"
        ? localStorage.getItem("brand_intel_split_view") === "true"
        : false;
    } catch {
      return false;
    }
  });

  const toggleSplitView = () => {
    setSplitViewOpen((v) => {
      const next = !v;
      if (next && focusedIndex === -1 && displayed.length > 0) {
        setFocusedIndex(0);
      }
      try {
        if (typeof window !== "undefined" && typeof localStorage !== "undefined" && typeof localStorage.setItem === "function") {
          localStorage.setItem("brand_intel_split_view", String(next));
        }
      } catch {}
      if (next) {
        toast.success("Split view enabled — live inspection active", { id: "split-toggle", icon: "🖥️" });
      } else {
        toast("Split view closed", { id: "split-toggle", icon: "✖️" });
      }
      return next;
    });
  };

  const pushUndo = (description: string, items: Array<{ id: string; prevStatus: Status }>) => {
    setUndoStack((prev) => [...prev.slice(-14), { description, items }]);
  };

  const handleUndo = async () => {
    if (!undoStack.length) {
      toast("No recent actions to undo", { icon: "ℹ️" });
      return;
    }
    const last = undoStack[undoStack.length - 1];
    setUndoStack((prev) => prev.slice(0, -1));

    const statusMap = new Map(last.items.map((i) => [i.id, i.prevStatus]));
    setProfiles((rows) =>
      rows.map((r) => (statusMap.has(r.id) ? { ...r, status: statusMap.get(r.id)! } : r))
    );

    try {
      await Promise.all(
        last.items.map((item) => profilesApi.patchProfile(item.id, { status: item.prevStatus }))
      );
      toast.success(`Undid: ${last.description}`, { icon: "🔄" });
      await load(false);
    } catch (e) {
      onError?.((e as Error).message);
    }
  };
  // Re-resolving name/photo for the current selection (Facebook only),
  // separate from bulkBusy since it can run alongside a still-open
  // selection (unlike approve/reject, it doesn't clear it) and takes much
  // longer (a real page visit per profile, not a single PATCH).
  const [resweepBusy, setResweepBusy] = useState(false);

  // Drag-to-select: mousedown on a card/row (outside its buttons/links)
  // starts a paint gesture, every card/row the cursor then passes over
  // while the button stays down joins the selection, so an analyst can
  // sweep the cursor down a page of discovery results and then hit
  // Validate/Reject once for the whole swath, instead of clicking each
  // checkbox individually.
  const dragSelectActive = useRef(false);

  const isAnalysisView = phase === "analysis";

  // load() is called from a lot of places that can overlap in time: the
  // 3s live-poll while a job runs, and a fresh reload fired right after
  // every approve/reject/validate/publish. Those requests can resolve out
  // of order (a fast small query can land after a slower earlier one), and
  // without guarding against that, whichever response happens to arrive
  // LAST wins, even if it's the stale one, silently reverting a card
  // that had just been acted on and making the rest of the grid look like
  // it never moved. This ref tracks the most recently ISSUED request; a
  // response only gets applied to state if it's still the latest one by
  // the time it comes back, so a slow stale response is simply discarded.
  const requestSeq = useRef(0);

  const load = useCallback(
    async (showLoading = true) => {
      const seq = ++requestSeq.current;
      if (!clientId) {
        setProfiles([]);
        setTotal(0);
        setCounts({ platforms: {}, statuses: {}, keywords: {} });
        return;
      }
      if (showLoading) setLoading(true);
      try {
        const res = await profilesApi.profiles({
          client_id: clientId,
          platform: platform || undefined,
          status: !isAnalysisView && status ? status : undefined,
          phase,
          // this backend's `keywords` field is the same underlying array
          // regardless of phase (analysis just also joins it into a display
          // string), the server-side filter works for analysis rows too,
          // it just wasn't being sent there before.
          keyword: keywordFilter || undefined,
          // entity_type persists from discovery onto the same doc through
          // analysis (profile_repository.py never blanks a field a later
          // phase doesn't mention), so this filter is just as meaningful
          // against analysis-phase rows, not discovery-only.
          entity_type: platform === "facebook" && entityType ? entityType : undefined,
          // These four used to be applied only in the browser, over
          // whatever page had been fetched, while `total` and the pager
          // still came from the unfiltered query. Filtering 500 analysis
          // rows to "High" therefore showed the High rows inside page 1 and
          // still claimed 500 results, which reads as the tool having
          // lost data. They are now real query parameters.
          priority: isAnalysisView && priority ? priority : undefined,
          match_level: !isAnalysisView && matchLevel ? matchLevel : undefined,
          keyword_match_type: keywordMatchType || undefined,
          search: debouncedSearch || undefined,
          published: isAnalysisView ? publishedFilter === "published" : undefined,
          limit: pageSize,
          offset,
        });
        if (seq !== requestSeq.current) return; // a newer load() has since been issued -- drop this stale response
        if (res.items.length === 0 && offset > 0 && res.total > 0) {
          setOffset(0);
          return;
        }
        setProfiles(res.items);
        setTotal(res.total);
        if (res.counts) {
          setCounts({
            platforms: res.counts.platforms || {},
            statuses: res.counts.statuses || {},
            keywords: res.counts.keywords || {},
          });
        }
      } catch (e) {
        if (seq === requestSeq.current) onError?.((e as Error).message);
      } finally {
        if (showLoading && seq === requestSeq.current) setLoading(false);
      }
    },
    // priority / matchLevel / keywordMatchType / debouncedSearch / published
    // are query parameters now, so load() must re-run when any of them changes
    [clientId, platform, status, phase, keywordFilter, entityType, isAnalysisView,
     priority, matchLevel, keywordMatchType, publishedFilter, debouncedSearch, offset, pageSize, onError],
  );

  // Any filter change invalidates the current page number: page 4 of the
  // old result set is meaningless against the new one, and leaving offset
  // where it was lands on an empty page.
  useEffect(() => {
    setOffset(0);
  }, [clientId, platform, status, phase, keywordFilter, entityType, keywordMatchType,
      priority, matchLevel, publishedFilter, debouncedSearch, pageSize]);

  // Client-scoped filters must reset on a client switch, a leftover
  // Individual/Domain match selection from a different client's keyword
  // lists would silently misclassify (or blank out) results here.
  useEffect(() => {
    setKeywordMatchType("");
    setPublishedFilter("unpublished");
  }, [clientId]);

  // A selection only ever makes sense against the rows the analyst was
  // looking at when they made it, clear it whenever the underlying set
  // changes so a stale selection can't silently bulk-act on different rows.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [clientId, platform, status, phase, keywordFilter, entityType, keywordMatchType,
      priority, matchLevel, publishedFilter, debouncedSearch, offset, pageSize]);

  // Neither Discovery nor Analysis has an "All Platforms" tab, whenever we
  // end up with no platform selected (e.g., landing here fresh before platforms
  // finish loading), fall back to the first platform rather than showing an
  // unfiltered grid with no tab highlighted as active.
  useEffect(() => {
    if (!platform && platforms.length > 0) {
      setPlatform(platforms[0].platform);
    }
  }, [platform, platforms]);

  useEffect(() => {
    load(true);
  }, [load]);

  // Live preview polling while either engine runs, same cadence the old
  // WebSocket-driven view refreshed at, this backend polls for progress
  // too now (see docs/adr/0002), so results polling matches that rhythm.
  useEffect(() => {
    if (!discoveryRunning && !analysisRunning) return;
    const interval = setInterval(() => load(false), 3000);
    return () => clearInterval(interval);
  }, [discoveryRunning, analysisRunning, load]);

  // ...and one more load the moment a run ENDS. The interval above is torn
  // down as soon as `running` flips false, so anything the job wrote in the
  // last few seconds of its life, which for analysis is typically the
  // final profile's scores and its published-incident preview, would sit
  // unshown until the next thing to touch the grid. The ref guard means
  // this fires only on the true→false transition, not on every filter
  // change that gives `load` a new identity.
  const wasRunning = useRef(false);
  useEffect(() => {
    const running = discoveryRunning || analysisRunning;
    if (wasRunning.current && !running) load(false);
    wasRunning.current = running;
  }, [discoveryRunning, analysisRunning, load]);

  // discovery-only: the id-backfill re-sweep button (line ~2146) reruns a
  // discovery-phase operation, so it stays gated to discovery specifically
  const isFacebook = !isAnalysisView && platform === "facebook";
  // the People/Pages/Groups filter, by contrast, applies to BOTH views,
  // entity_type persists on a profile's doc from discovery straight
  // through analysis (nothing blanks it), so filtering by it is just as
  // meaningful once a profile has been analysed
  const isFacebookPlatform = platform === "facebook";
  // status and priority each only have a picker UI in one view (status:
  // Discovery, priority: Analysis), both must be blanked in the other
  // view, or a value picked before switching tabs silently keeps filtering
  // the tab that has no control to see or clear it.
  const filters: ResultFilters = {
    status: !isAnalysisView ? status : "",
    priority: isAnalysisView ? priority : "",
    phase,
  };
  const extra: ExtraFilters = {
    keywordFilter,
    // the debounced value, matching what the server was actually queried
    // with, using the raw input here would blank the grid mid-keystroke
    // while the request for those characters is still in flight
    searchQuery: debouncedSearch,
    matchLevel: !isAnalysisView ? matchLevel : "",
    entityType: isFacebookPlatform ? entityType : "",
    keywordMatchType,
  };
  const prevRowOrderRef = useRef<string[]>([]);

  // Reset stable row order sequence whenever user explicitly changes filters or sort order
  useEffect(() => {
    prevRowOrderRef.current = [];
  }, [sortOrder, phase, debouncedSearch, platform, keywordFilter, status, matchLevel, entityType]);

  // The server has already applied all of these before pagination (see
  // load()); this pass only reconciles rows whose local state is ahead of
  // the server, an optimistic status change not yet PATCHed, or rows
  // still in memory from a live-poll refresh.
  const displayed = useMemo(
    () => {
      const sorted = sortResults(
        filterResults(profiles, filters, extra, isAnalysisView ? "" : platform, clientKeywordSets),
        sortOrder,
        phase,
        keywordFilter,
        status,
      );

      const currentIds = sorted.map((r) => r.id);
      const prevIds = prevRowOrderRef.current;

      // If we have an existing row order for this exact set of profiles, preserve it
      // so toggling Username Match or Logo Match never causes rows to jump up and down.
      if (
        prevIds.length > 0 &&
        prevIds.length === currentIds.length &&
        prevIds.every((id) => currentIds.includes(id))
      ) {
        const rowMap = new Map(sorted.map((r) => [r.id, r]));
        return prevIds.map((id) => rowMap.get(id)!).filter(Boolean);
      }

      prevRowOrderRef.current = currentIds;
      return sorted;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [profiles, status, priority, phase, keywordFilter, matchLevel, entityType, keywordMatchType, clientKeywordSets, debouncedSearch, sortOrder, platform, isAnalysisView],
  );

  const decide = async (id: string, next: Status) => {
    const prev = profiles.find((r) => r.id === id);
    if (prev) {
      pushUndo(`${next === "approved" ? "Validate" : "Reject"} "${prev.profile_name || prev.username || "profile"}"`, [
        { id: prev.id, prevStatus: prev.status },
      ]);
    }
    setProfiles((rows) => {
      const updated = rows.map((r) => (r.id === id ? { ...r, status: next } : r));
      return !isAnalysisView && status ? updated.filter((r) => r.status === status) : updated;
    });
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: next });
      if (next === "approved" && !isAnalysisView) {
        toast.custom(
          (t) => (
            <div
              className="undo-toast-box"
              style={{
                background: "var(--bg-card, #151d2a)",
                color: "#fff",
                border: "1px solid var(--cyan, #00E5FF)",
                padding: "10px 14px",
                borderRadius: "8px",
                boxShadow: "0 4px 14px rgba(0, 0, 0, 0.4)",
                display: "flex",
                alignItems: "center",
                gap: "10px",
                fontSize: "13px",
              }}
            >
              <span>✅ Validated — queued for analysis</span>
              <button
                className="toast-action-link"
                onClick={() => {
                  toast.dismiss(t.id);
                  setPhase("analysis");
                }}
              >
                View in Analysis →
              </button>
              <button
                className="undo-toast-btn"
                onClick={() => {
                  toast.dismiss(t.id);
                  handleUndo();
                }}
              >
                🔄 Undo
              </button>
            </div>
          ),
          { duration: 5000, id: `validate-${id}` }
        );
      } else if (next === "rejected" && !isAnalysisView) {
        toast.custom(
          (t) => (
            <div
              className="undo-toast-box"
              style={{
                background: "var(--bg-card, #151d2a)",
                color: "#fff",
                border: "1px solid var(--border-subtle)",
                padding: "10px 14px",
                borderRadius: "8px",
                boxShadow: "0 4px 14px rgba(0, 0, 0, 0.4)",
                display: "flex",
                alignItems: "center",
                gap: "10px",
                fontSize: "13px",
              }}
            >
              <span>✕ Rejected profile</span>
              <button
                className="undo-toast-btn"
                onClick={() => {
                  toast.dismiss(t.id);
                  handleUndo();
                }}
              >
                🔄 Undo
              </button>
            </div>
          ),
          { duration: 4000, id: `reject-${id}` }
        );
      }
      await load(false);
    } catch (e) {
      if (prev) setProfiles((rows) => [...rows.filter((r) => r.id !== prev.id), prev]);
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  const validate = async (id: string) => {
    const prev = profiles.find((r) => r.id === id);
    if (prev) {
      pushUndo(`Validate "${prev.profile_name || prev.username || "profile"}"`, [
        { id: prev.id, prevStatus: prev.status },
      ]);
    }
    setProfiles((rows) => {
      const updated = rows.map((r) =>
        r.id === id ? { ...r, status: "approved" as Status } : r,
      );
      return !isAnalysisView && status ? updated.filter((r) => r.status === status) : updated;
    });
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: "approved" });
      if (!isAnalysisView) {
        toast.custom(
          (t) => (
            <div
              className="undo-toast-box"
              style={{
                background: "var(--bg-card, #151d2a)",
                color: "#fff",
                border: "1px solid var(--cyan, #00E5FF)",
                padding: "10px 14px",
                borderRadius: "8px",
                boxShadow: "0 4px 14px rgba(0, 0, 0, 0.4)",
                display: "flex",
                alignItems: "center",
                gap: "10px",
                fontSize: "13px",
              }}
            >
              <span>✅ Validated — queued for analysis</span>
              <button
                className="toast-action-link"
                onClick={() => {
                  toast.dismiss(t.id);
                  setPhase("analysis");
                }}
              >
                View in Analysis →
              </button>
              <button
                className="undo-toast-btn"
                onClick={() => {
                  toast.dismiss(t.id);
                  handleUndo();
                }}
              >
                🔄 Undo
              </button>
            </div>
          ),
          { duration: 5000, id: `validate-${id}` }
        );
      }
      await load(false);
    } catch (e) {
      if (prev) setProfiles((rows) => [...rows.filter((r) => r.id !== prev.id), prev]);
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  const publish = async (id: string) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => rows.map((r) => (r.id === id ? { ...r, published: true } : r)));
    setSavingId(id);
    try {
      await profilesApi.publishProfile(id);
    } catch (e) {
      if (prev) setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  // "Did we actually check everything?", see GET /profiles/coverage.
  // Refreshed alongside the grid so the banner can't outlive the state it
  // describes (an analyst re-running analysis should see it clear).
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [coverageOpen, setCoverageOpen] = useState(false);
  useEffect(() => {
    if (!clientId || !isAnalysisView) {
      setCoverage(null);
      return;
    }
    let cancelled = false;
    profilesApi
      .coverage(clientId, platform || undefined)
      .then((c) => !cancelled && setCoverage(c))
      .catch(() => !cancelled && setCoverage(null));
    return () => { cancelled = true; };
    // `profiles` is in the deps on purpose: it changes whenever the grid
    // reloads (including the 3s live poll during a run), which is exactly
    // when coverage can have moved.
  }, [clientId, platform, isAnalysisView, profiles]);

  const [publishingAll, setPublishingAll] = useState(false);
  const [publishScope, setPublishScope] = useState<"all" | "recent" | "2days" | "week">("all");
  const [deletingPlatformData, setDeletingPlatformData] = useState(false);

  // Irreversible hard delete of every profile (both Discovery and Analysis
  // phase), evidence screenshot, and published incident for the currently
  // selected client + platform, see
  // backend/services/profile_service.py::delete_for_client_platform.
  // Neither view ever has an ambiguous "all platforms" state (see the
  // platform-selection effect above), so `platform` is always a single,
  // unambiguous target at the moment this is called.
  const handleDeletePlatformData = async () => {
    if (!clientId || !platform) return;
    const platformName = platforms.find((p) => p.platform === platform)?.name || platform;
    const ok = await confirmAction(
      `Permanently delete ALL ${platformName} data for client "${clientId}"? This removes every ` +
      `Discovery and Analysis profile, evidence screenshot, and published incident for this platform ` +
      `from the database. This cannot be undone.`,
    );
    if (!ok) return;
    setDeletingPlatformData(true);
    try {
      const res = await profilesApi.deletePlatformData(clientId, platform);
      toast.success(
        `Deleted ${res.deleted_profiles} profile(s), ${res.deleted_evidence} screenshot(s), ` +
        `${res.deleted_published_incidents} published incident(s) for ${platformName}`,
        { icon: "🗑" },
      );
      await load(false);
    } catch (e) {
      toast.error((e as Error).message);
      onError?.((e as Error).message);
    } finally {
      setDeletingPlatformData(false);
    }
  };

  const PUBLISH_SCOPE_LABELS: Record<typeof publishScope, string> = {
    all: "All",
    recent: "Recent (last 24h)",
    "2days": "Last 2 Days",
    week: "Last Week",
  };

  const publishAll = async () => {
    if (!clientId) return;
    setPublishingAll(true);
    try {
      const res = await profilesApi.publishAllProfiles(clientId, platform || undefined, publishScope);
      toast.success(`${res.published} incident(s) published`, { icon: "✅" });
      await load(false);
    } catch (e) {
      toast.error((e as Error).message);
      onError?.((e as Error).message);
    } finally {
      setPublishingAll(false);
    }
  };

  // Applies a dotted-path edit (e.g. "socialProfileInfo.location") to a
  // profile's own `incident` preview object, immutably, the same shape
  // profile_repository.patch() expands `incident_overrides` into server-side.
  const withIncidentPath = (r: Profile, path: string, value: unknown): Profile => {
    if (!r.incident) return r;
    if (!path.includes(".")) return { ...r, incident: { ...r.incident, [path]: value } };
    const [parent, child] = path.split(".", 2);
    return {
      ...r,
      incident: { ...r.incident, [parent]: { ...(r.incident as unknown as Record<string, object>)[parent], [child]: value } },
    };
  };

  // Export re-fetches from the server rather than exporting local state
  // (see handleExport below), so it's only "immediate" if every edit the
  // analyst just made has actually landed in Mongo before that fetch
  // fires, a save is only a fire-and-forget onBlur/onChange/click, so a
  // very fast "edit a field, then immediately click Excel" could otherwise
  // race ahead of its own PATCH. This set tracks every in-flight save on
  // ANY profile field (incident overrides AND the Match toggles below);
  // handleExport/handleCopyUrls await all of them first.
  const pendingSaves = useRef<Set<Promise<void>>>(new Set());

  // Guards every profile-row mutation (a Match toggle click, an incident
  // field's onBlur) against a STALE response landing after a NEWER one for
  // the SAME row. Without this, two edits fired close together on one row
  //, a fast double-click, tabbing through two fields before the first
  // save lands, can have their PATCH responses resolve out of order, and
  // whichever happened to land last would silently win even if it was the
  // older edit, quietly reverting the newer one. Confirmed as a real gap:
  // neither save path checked this before applying its response.
  const rowSaveSeq = useRef<Map<string, number>>(new Map());
  const nextRowSeq = (id: string): number => {
    const n = (rowSaveSeq.current.get(id) ?? 0) + 1;
    rowSaveSeq.current.set(id, n);
    return n;
  };
  const isLatestRowSeq = (id: string, seq: number): boolean => rowSaveSeq.current.get(id) === seq;

  const saveIncidentField = (id: string, path: string, rawValue: string): void => {
    const prev = profiles.find((r) => r.id === id);
    const seq = nextRowSeq(id);
    // booleans/numbers travel through the DOM as strings, coerce back
    // before both the optimistic update and the PATCH payload
    const value: unknown =
      rawValue === "true" || rawValue === "false" ? rawValue === "true"
      : path === "socialProfileInfo.numberOfFollowers" ? (rawValue === "" ? null : Number(rawValue))
      : rawValue;
    setProfiles((rows) => rows.map((r) => {
      if (r.id !== id) return r;
      const updatedR = withIncidentPath(r, path, value);
      const inc = updatedR.incident?.socialProfileInfo;
      const previewScore = computeIncidentRiskScorePreview({
        logoMatch: inc?.isSimilarLogo ?? logoMatchOf(r),
        usernameMatch: inc?.isSimilarName ?? usernameMatchOf(r),
        followers: inc?.numberOfFollowers ?? r.followers,
        location: inc?.location ?? r.location,
        lastPostDate: inc?.lastPostDate ?? r.last_post_date,
        isActive: inc?.isActive ?? r.is_active,
      });
      return withIncidentPath(updatedR, "riskRating", String(previewScore));
    }));
    const task = (async () => {
      try {
        const updated = await profilesApi.patchProfile(id, { incident_overrides: { [path]: value } });
        // A newer edit on this SAME row has started since this one fired,
        // applying this (older) response now would silently revert it.
        // Reconciling with the server's own response (not just trusting the
        // local optimistic guess forever) is what makes the table and any
        // export actually match Mongo, not just "look right" until the next
        // full reload.
        if (isLatestRowSeq(id, seq)) setProfiles((rows) => rows.map((r) => (r.id === id ? updated : r)));
      } catch (e) {
        if (prev && isLatestRowSeq(id, seq)) setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
        onError?.((e as Error).message);
      }
    })();
    pendingSaves.current.add(task);
    task.finally(() => pendingSaves.current.delete(task));
  };

  // Editing the RAW username_match/logo_match fields (not the incident
  // preview's cosmetic socialProfileInfo.isSimilarName/isSimilarLogo
  // overrides, see saveIncidentField above) is what actually feeds
  // compute_incident_risk_score server-side, so the Risk badge only ever
  // changes from editing these. Recomputes the score locally right away
  // (computeIncidentRiskScorePreview mirrors the backend formula exactly)
  // instead of waiting on the PATCH round trip or the 3s live-poll, then
  // reconciles with the server's authoritative response when it lands.
  //
  // `savingId` is set for the duration of this row's own save and checked
  // by the Match buttons' `disabled` below, a second click on the SAME
  // button (or the other Match button on the SAME row) before the first
  // PATCH resolves is exactly what let two overlapping saves race each
  // other; disabling the row's own controls while its save is in flight
  // makes that race impossible to trigger in the first place, rather than
  // just resolved gracefully after the fact.
  const saveProfileField = async (id: string, field: "username_match" | "logo_match", value: boolean): Promise<void> => {
    const prev = profiles.find((r) => r.id === id);
    if (!prev) return;
    const seq = nextRowSeq(id);
    const inc = prev.incident?.socialProfileInfo;
    const logoMatch = field === "logo_match" ? value : (inc?.isSimilarLogo ?? logoMatchOf(prev));
    const usernameMatch = field === "username_match" ? value : (inc?.isSimilarName ?? usernameMatchOf(prev));
    const followers = inc?.numberOfFollowers ?? prev.followers;
    const location = inc?.location ?? prev.location;
    const lastPostDate = inc?.lastPostDate ?? prev.last_post_date;
    const isActive = inc?.isActive ?? prev.is_active;

    const previewScore = computeIncidentRiskScorePreview({
      logoMatch, usernameMatch, followers, location, lastPostDate, isActive,
    });
    setProfiles((rows) =>
      rows.map((r) => {
        if (r.id !== id) return r;
        if (!r.incident) return { ...r, [field]: value };
        return {
          ...r, [field]: value,
          incident: {
            ...r.incident,
            riskRating: String(previewScore),
            socialProfileInfo: {
              ...r.incident.socialProfileInfo,
              isSimilarName: usernameMatch,
              isSimilarLogo: logoMatch,
            },
          },
        };
      }),
    );
    setSavingId(id);
    const task = (async () => {
      try {
        const updated = await profilesApi.patchProfile(id, { [field]: value });
        if (isLatestRowSeq(id, seq)) setProfiles((rows) => rows.map((r) => (r.id === id ? updated : r)));
      } catch (e) {
        if (isLatestRowSeq(id, seq)) setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
        onError?.((e as Error).message);
      } finally {
        // only the LATEST save on this row clears the busy state, an
        // older, slower request finishing after a newer one started must
        // not un-disable the row while the newer save is still in flight
        if (isLatestRowSeq(id, seq)) setSavingId((cur) => (cur === id ? null : cur));
      }
    })();
    pendingSaves.current.add(task);
    task.finally(() => pendingSaves.current.delete(task));
    await task;
  };

  // Analysis-only bulk apply: sets the same assetName override across every
  // selected profile in one action, reusing saveIncidentField's existing
  // optimistic-update + PATCH + rollback-on-error machinery per profile
  // rather than a new backend endpoint, this is exactly what a single
  // card's Asset Name dropdown already does, just looped over a selection.
  const [bulkAssetNameBusy, setBulkAssetNameBusy] = useState(false);
  const bulkSetAssetName = async (assetName: string) => {
    if (!assetName || !selectedIds.size) return;
    setBulkAssetNameBusy(true);
    try {
      for (const id of selectedIds) saveIncidentField(id, "assetName", assetName);
      if (pendingSaves.current.size) await Promise.all(pendingSaves.current);
    } finally {
      setBulkAssetNameBusy(false);
    }
  };

  const handleCopy = async (type: "urls" | "table", formatOverride?: "incident" | "legacy") => {
    if (!clientId) return;
    setCopyUrlState("idle");
    setCopyMenuOpen(false);

    const fmt = formatOverride || exportFormat;

    const fetchText = async (): Promise<{ text: string; count: number; label: string }> => {
      if (pendingSaves.current.size) {
        await Promise.all(pendingSaves.current);
      }

      // Checkbox selection is an explicit, row-level override, when
      // anything is selected, copy exactly those rows
      const selected = selectedIds.size > 0 ? displayed.filter((r) => selectedIds.has(r.id)) : null;

      let filtered: Profile[];
      let scopeLabel: string;
      if (selected) {
        filtered = selected;
        scopeLabel = ` (${selected.length} selected)`;
      } else {
        const res = await profilesApi.profiles({
          client_id: clientId,
          platform: platform || undefined,
          status: !isAnalysisView && status ? status : undefined,
          keyword: keywordFilter || undefined,
          keyword_match_type: keywordMatchType || undefined,
          phase,
          published: isAnalysisView ? publishedFilter === "published" : undefined,
          limit: EXPORT_LIMIT,
          offset: 0,
        });
        filtered = filterResults(res.items, filters, extra, platform, clientKeywordSets);
        scopeLabel = "";
      }

      if (type === "urls") {
        const targetProfiles = selected ? filtered : !isAnalysisView && status ? filtered.filter((r) => r.status === status) : filtered;
        const urls = targetProfiles.map((r) => r.url).filter(Boolean);
        if (!urls.length) {
          throw new Error(`No profile URLs found to copy${scopeLabel}.`);
        }
        return { text: urls.join("\n"), count: urls.length, label: "URLs" };
      } else {
        // Table copy (TSV)
        if (isAnalysisView) {
          const rows = fmt === "legacy" ? toLegacyExportRows(filtered) : toIncidentExportRows(filtered);
          if (!rows.length) throw new Error(`No analysis table data to copy${scopeLabel}.`);
          const label = fmt === "legacy" ? "Legacy Table" : "Platform Format Table";
          return { text: rowsToTsv(rows), count: rows.length, label };
        } else {
          const discoveryRows = filtered.map((r) => Object.fromEntries(DISCOVERY_EXPORT_COLS.map((c) => [c, r[c]])));
          if (!discoveryRows.length) throw new Error(`No discovery table data to copy${scopeLabel}.`);
          return { text: rowsToTsv(discoveryRows), count: discoveryRows.length, label: "Table Data" };
        }
      }
    };

    try {
      const { text, count, label } = await fetchText();
      try {
        await navigator.clipboard.writeText(text);
        setCopyUrlState("copied");
        toast.success(`Copied ${count} ${label} to clipboard`);
        setTimeout(() => setCopyUrlState("idle"), 2500);
      } catch {
        setCopyDataCache(text);
      }
    } catch (e) {
      onError?.((e as Error).message || "Copy failed");
      setCopyUrlState("failed");
      toast.error((e as Error).message || "Copy failed");
      setTimeout(() => setCopyUrlState("idle"), 2500);
    }
  };

  // Fetches everything matching the current filters (not just this page) for
  // export, this backend has no export endpoint, so the conversion happens
  // entirely client-side.
  const DISCOVERY_EXPORT_COLS = [
    "id", "platform", "status", "phase", "url", "profile_name", "username", "keyword",
  ] as const;

  const handleExport = async (fmt: "csv" | "json" | "xlsx", formatOverride?: "incident" | "legacy") => {
    if (!clientId) return;
    setExporting(true);
    setExportMenuOpen(false);
    const chosenFormat = formatOverride || exportFormat;
    try {
      if (pendingSaves.current.size) {
        await Promise.all(pendingSaves.current);
      }
      const selected = selectedIds.size > 0 ? displayed.filter((r) => selectedIds.has(r.id)) : null;

      let filtered: Profile[];
      if (selected) {
        filtered = selected;
      } else {
        const res = await profilesApi.profiles({
          client_id: clientId,
          platform: platform || undefined,
          status: !isAnalysisView && status ? status : undefined,
          keyword: keywordFilter || undefined,
          keyword_match_type: keywordMatchType || undefined,
          phase,
          published: isAnalysisView ? publishedFilter === "published" : undefined,
          limit: EXPORT_LIMIT,
          offset: 0,
        });
        filtered = filterResults(res.items, filters, extra, platform, clientKeywordSets);
      }

      if (!filtered.length) {
        throw new Error(`No profiles match the current filters to export.`);
      }
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      const rows: Record<string, unknown>[] = isAnalysisView
        ? chosenFormat === "legacy"
          ? toLegacyExportRows(filtered)
          : toIncidentExportRows(filtered)
        : filtered.map((r) => Object.fromEntries(DISCOVERY_EXPORT_COLS.map((c) => [c, r[c]])));

      const formatSuffix = isAnalysisView ? `-${chosenFormat === "legacy" ? "legacy" : "platform-format"}` : "";
      const stem = `${(filtered[0]?.client_name || clientId).replace(/[/\\:*?"<>|]/g, "_")}-${phase}${formatSuffix}-${stamp}`;
      if (fmt === "csv") {
        download(`${stem}.csv`, rowsToCsv(rows), "text/csv");
      } else if (fmt === "xlsx") {
        const filename = `${stem}.xlsx`;
        const fileBlob = await profilesApi.exportXlsx(filename, rows);
        downloadBlob(filename, fileBlob);
      } else {
        download(`${stem}.json`, JSON.stringify(rows, null, 2), "application/json");
      }
      toast.success(`Exported ${rows.length} profiles (${fmt.toUpperCase()})`);
    } catch (e) {
      onError?.((e as Error).message);
      toast.error((e as Error).message || "Export failed");
    } finally {
      setExporting(false);
    }
  };

  // `ids` defaults to the current checkbox selection (the bulk action bar);
  // page-wide Validate All/Reject All pass their own id list directly so
  // they work with nothing selected at all, see the toolbar buttons below.
  const bulkDecide = async (next: Status, ids?: string[]) => {
    const targetIds = ids ?? [...selectedIds];
    if (!targetIds.length) return;
    const prevItems = targetIds.map((id) => {
      const p = profiles.find((r) => r.id === id);
      return { id, prevStatus: p?.status || ("pending" as Status) };
    });
    pushUndo(`${next === "approved" ? "Validate" : "Reject"} ${targetIds.length} profiles`, prevItems);
    setBulkBusy(true);
    try {
      const res = await profilesApi.bulkPatch(targetIds, next);
      setSelectedIds(new Set());
      if (res.failed.length) {
        toast.error(`${res.failed.length} of ${targetIds.length} profile(s) failed to update.`);
        onError?.(`${res.failed.length} of ${targetIds.length} profile(s) failed to update.`);
      } else {
        if (next === "approved" && !isAnalysisView) {
          toast.custom(
            (t) => (
              <div
                className="undo-toast-box"
                style={{
                  background: "var(--bg-card, #151d2a)",
                  color: "#fff",
                  border: "1px solid var(--cyan, #00E5FF)",
                  padding: "10px 14px",
                  borderRadius: "8px",
                  boxShadow: "0 4px 14px rgba(0, 0, 0, 0.4)",
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  fontSize: "13px",
                }}
              >
                <span>✅ {targetIds.length} profiles validated — queued for analysis</span>
                <button
                  className="toast-action-link"
                  onClick={() => {
                    toast.dismiss(t.id);
                    setPhase("analysis");
                  }}
                >
                  View in Analysis →
                </button>
                <button
                  className="undo-toast-btn"
                  onClick={() => {
                    toast.dismiss(t.id);
                    handleUndo();
                  }}
                >
                  🔄 Undo
                </button>
              </div>
            ),
            { duration: 5000 }
          );
        } else {
          toast.custom(
            (t) => (
              <div
                className="undo-toast-box"
                style={{
                  background: "var(--bg-card, #151d2a)",
                  color: "#fff",
                  border: "1px solid var(--border-subtle)",
                  padding: "10px 14px",
                  borderRadius: "8px",
                  boxShadow: "0 4px 14px rgba(0, 0, 0, 0.4)",
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  fontSize: "13px",
                }}
              >
                <span>{targetIds.length} incidents {next === "approved" ? "validated" : "rejected"}</span>
                <button
                  className="undo-toast-btn"
                  onClick={() => {
                    toast.dismiss(t.id);
                    handleUndo();
                  }}
                >
                  🔄 Undo
                </button>
              </div>
            ),
            { duration: 5000 }
          );
        }
      }
      await load(false);
    } catch (e) {
      toast.error((e as Error).message);
      onError?.((e as Error).message);
    } finally {
      setBulkBusy(false);
    }
  };

  // Re-resolves name/photo for just the selected profiles, no keyword
  // search, one page visit per profile (Facebook only; see
  // discoveryApi.resweepSelected / backend's _resweep_selected). The direct
  // fix for a card stuck showing a bare numeric id/no photo: point this at
  // it instead of waiting on, or forcing, a whole new keyword sweep.
  const resweepSelected = async (ids?: string[]) => {
    const targetIds = ids ?? [...selectedIds];
    if (!targetIds.length || !clientId) return;
    setResweepBusy(true);
    try {
      const { job_id } = await discoveryApi.resweepSelected(clientId, targetIds);
      // eslint-disable-next-line no-constant-condition
      while (true) {
        await new Promise((r) => setTimeout(r, 2000));
        const job = await jobsApi.job(job_id);
        if (job.status === "done" || job.status === "failed" || job.status === "cancelled") {
          if (job.status === "failed") onError?.(job.error || "Re-sweep failed");
          break;
        }
      }
      await load(false);
    } catch (e) {
      onError?.((e as Error).message);
    } finally {
      setResweepBusy(false);
    }
  };

  const splitUrls = (text: string): string[] =>
    text.split(/[\n,]+/).map((u) => u.trim()).filter(Boolean);

  const submitManualUrls = async () => {
    if (!clientId) return;
    const individualUrls = splitUrls(manualIndividualUrlsText);
    const domainUrls = splitUrls(manualDomainUrlsText);
    if (!individualUrls.length && !domainUrls.length) return;
    setManualUrlsBusy(true);
    try {
      const res = await profilesApi.addManualUrls(clientId, { individualUrls, domainUrls });
      setManualIndividualUrlsText("");
      setManualDomainUrlsText("");
      if (res.skipped.length) {
        onError?.(`${res.added} added. ${res.skipped.length} skipped (unrecognized platform): ${res.skipped.join(", ")}`);
      }
      setManualUrlsOpen(false);
      await load(false);
    } catch (e) {
      onError?.((e as Error).message);
    } finally {
      setManualUrlsBusy(false);
    }
  };

  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const addSelected = (id: string) => {
    setSelectedIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  };

  const removeSelected = (ids: string[]) => {
    if (!ids.length) return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  };

  // Kept in sync below purely so the window-level drag handlers (registered
  // once, see the effect after this) can read the CURRENT selection
  // without going stale, they close over refs, not state, on purpose.
  const selectedIdsRef = useRef<Set<string>>(selectedIds);
  useEffect(() => {
    selectedIdsRef.current = selectedIds;
  }, [selectedIds]);

  // The previous version armed drag-select on the mousedown itself, so any
  // click that so much as twitched a couple pixels onto a neighbouring
  // card, resting a finger on a trackpad, a slightly imprecise click near
  // a card edge, would silently sweep that neighbour into the selection
  // too, with no visible cue it had happened. Fixed with a movement
  // threshold: a plain click (mousedown+mouseup with no meaningful
  // movement) toggles just the one card it landed on; only real movement
  // starts a drag.
  //
  // The drag itself is a paint-and-erase gesture, not a one-way sweep:
  // moving forward over a not-yet-visited card selects it; backtracking
  // over a card THIS SAME DRAG already selected un-selects it, as if the
  // cursor were physically erasing the mark it just made. `dragPath` is
  // the ordered trail of cards this one continuous drag has touched,
  // re-entering any earlier point in that trail rewinds (deselects)
  // everything painted after it, however far back the retrace goes, not
  // just the immediately-previous card. Deliberately still one-directional
  // with respect to anything selected BEFORE this drag started
  // (`dragStartSelection`, snapshotted the instant the drag arms): a card
  // that was already selected coming in is never touched by this drag,
  // forward or backward, retracing over it doesn't un-select a decision
  // some earlier action made, only ones this gesture itself made.
  const DRAG_THRESHOLD_PX = 6;
  const dragOrigin = useRef<{ id: string; x: number; y: number } | null>(null);
  const dragPath = useRef<string[]>([]);
  const dragStartSelection = useRef<Set<string>>(new Set());

  const dragSelectHandlers = (id: string) => ({
    onMouseDown: (e: ReactMouseEvent) => {
      if ((e.target as HTMLElement).closest("button, a, input, select, textarea")) return;
      dragOrigin.current = { id, x: e.clientX, y: e.clientY };
    },
    onMouseEnter: () => {
      if (!dragSelectActive.current) return;
      const idx = dragPath.current.indexOf(id);
      if (idx === -1) {
        // forward progress onto a card this drag hasn't touched yet
        dragPath.current.push(id);
        addSelected(id);
      } else if (idx < dragPath.current.length - 1) {
        // retraced back to an earlier point in this drag's own trail:
        // erase everything painted after it (but never anything that was
        // already selected before this drag began)
        const toErase = dragPath.current.slice(idx + 1).filter((pid) => !dragStartSelection.current.has(pid));
        dragPath.current = dragPath.current.slice(0, idx + 1);
        removeSelected(toErase);
      }
      // idx === last index: re-entering the card already at the head of
      // the trail (e.g. a wobble within its own bounds), no-op
    },
  });

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const origin = dragOrigin.current;
      if (!origin || dragSelectActive.current) return;
      if (Math.hypot(e.clientX - origin.x, e.clientY - origin.y) < DRAG_THRESHOLD_PX) return;
      // threshold crossed, this is a genuine drag, not a click; select
      // the card the drag started on and start painting from here
      dragSelectActive.current = true;
      document.body.style.userSelect = "none";
      dragStartSelection.current = new Set(selectedIdsRef.current);
      dragPath.current = [origin.id];
      addSelected(origin.id);
    };
    const endDrag = () => {
      // armed but never crossed the movement threshold, a plain click,
      // toggle exactly the one card it landed on
      if (dragOrigin.current && !dragSelectActive.current) toggleSelected(dragOrigin.current.id);
      dragOrigin.current = null;
      dragSelectActive.current = false;
      dragPath.current = [];
      document.body.style.userSelect = "";
    };
    // an interrupted gesture (focus lost, tab hidden, cursor left the
    // document entirely), abort without guessing at single-click intent
    const abortDrag = () => {
      dragOrigin.current = null;
      dragSelectActive.current = false;
      dragPath.current = [];
      document.body.style.userSelect = "";
    };
    const onKeyDown = (e: KeyboardEvent) => {
      // fastest possible "undo that", clears the whole selection and any
      // in-flight drag with one keypress, no need to reach for a mouse.
      // Unconditional (no "is there anything to clear" guard) so this
      // effect never needs `selectedIds` as a dependency, an empty-Set
      // update when nothing was selected is a harmless no-op re-render,
      // not worth re-subscribing all these listeners on every drag tick to avoid.
      if (e.key === "Escape") {
        abortDrag();
        setSelectedIds(new Set());
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", endDrag);
    window.addEventListener("blur", abortDrag);
    window.addEventListener("keydown", onKeyDown);
    document.addEventListener("mouseleave", abortDrag);
    document.addEventListener("visibilitychange", abortDrag);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", endDrag);
      window.removeEventListener("blur", abortDrag);
      window.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mouseleave", abortDrag);
      document.removeEventListener("visibilitychange", abortDrag);
      document.body.style.userSelect = "";
    };
  }, []);

  // Keyboard navigation & triage shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target && target.closest("input, textarea, select")) return;
      if (editingId || manualUrlsOpen || diffProfile || copyDataCache !== null) {
        if (e.key === "Escape") {
          setEditingId(null);
          setManualUrlsOpen(false);
          setDiffProfile(null);
          setShortcutsHelpOpen(false);
          setCopyDataCache(null);
        }
        return;
      }

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        handleUndo();
        return;
      }

      if (e.key === "i" || e.key === "I") {
        e.preventDefault();
        toggleSplitView();
        return;
      }

      if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
        e.preventDefault();
        setShortcutsHelpOpen((v) => !v);
        return;
      }

      if (e.key === "Escape") {
        if (shortcutsHelpOpen) {
          setShortcutsHelpOpen(false);
          return;
        }
        setSelectedIds(new Set());
        setFocusedIndex(-1);
        return;
      }

      if (!displayed.length) return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIndex((prev) => (prev < displayed.length - 1 ? prev + 1 : 0));
        return;
      }
      if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIndex((prev) => (prev > 0 ? prev - 1 : displayed.length - 1));
        return;
      }

      const activeRow = focusedIndex >= 0 && focusedIndex < displayed.length ? displayed[focusedIndex] : null;
      if (!activeRow) return;

      if (e.key === " " && !e.repeat) {
        e.preventDefault();
        toggleSelected(activeRow.id);
        return;
      }

      if (e.key === "v" || e.key === "V") {
        e.preventDefault();
        if (!isAnalysisView && activeRow.status !== "approved") {
          validate(activeRow.id);
        }
        return;
      }

      if (e.key === "x" || e.key === "X") {
        e.preventDefault();
        if (activeRow.status !== "rejected") {
          decide(activeRow.id, "rejected");
        }
        return;
      }

      if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        if (isAnalysisView && activeRow.incident) {
          setEditingId(activeRow.id);
        }
        return;
      }

      if (e.key === "p" || e.key === "P") {
        e.preventDefault();
        if (isAnalysisView && activeRow.published === false && !analysisWasBlocked(activeRow)) {
          publish(activeRow.id);
          toast.success("Published finding via shortcut [P]!", { icon: "🚀" });
        }
        return;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [displayed, focusedIndex, isAnalysisView, editingId, manualUrlsOpen, diffProfile, shortcutsHelpOpen, copyDataCache]);

  // "select all" only ever means "every row currently on screen", not
  // every row matching the filter across all pages, which the analyst
  // can't see and shouldn't be bulk-deciding blind.
  const allOnPageSelected = displayed.length > 0 && displayed.every((r) => selectedIds.has(r.id));
  const toggleSelectAllOnPage = () => {
    setSelectedIds((prev) => {
      if (allOnPageSelected) {
        const next = new Set(prev);
        displayed.forEach((r) => next.delete(r.id));
        return next;
      }
      const next = new Set(prev);
      displayed.forEach((r) => next.add(r.id));
      return next;
    });
  };

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.floor(offset / pageSize) + 1;

  return (
    <div style={{ animation: "fadeUp 0.4s ease" }}>
      {(discoveryRunning || analysisRunning) && (
        <div className="scanning-banner">
          <span
            style={{
              width: "12px", height: "12px", borderRadius: "50%",
              background: "var(--success)", boxShadow: "0 0 12px var(--success)",
              animation: "blink 1.2s infinite",
            }}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "14px", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-main)" }}>
              Scanning platforms for active impersonation signals…
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {[discoveryRunning && "Discovery running", analysisRunning && "Analysis running"]
                .filter(Boolean)
                .join(" · ")}{" "}
              · {total} matching profile{total === 1 ? "" : "s"}
            </div>
          </div>
          <div
            style={{
              width: "22px", height: "22px", border: "2px solid rgba(136, 56, 221, 0.25)",
              borderTopColor: "var(--cyan)", borderRadius: "50%", animation: "spin 0.8s linear infinite",
            }}
          />
        </div>
      )}

      <div className="content-shell">
        <div className="content-shell-inner">
          {/* Phase tabs */}
          <div className="platform-rail-grid" style={{ gridTemplateColumns: "repeat(2, 1fr)", marginBottom: "16px" }}>
            {(["discovery", "analysis"] as const).map((ph) => (
              <div
                key={ph}
                className={`platform-rail-item ${phase === ph ? "active" : ""}`}
                onClick={() => setPhase(ph)}
              >
                <div className="rail-card-head">
                  <span style={{ fontSize: "16px" }}>{ph === "discovery" ? "🔍" : "📊"}</span>
                  <span style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-primary)" }}>
                    {ph === "discovery" ? "Discovery" : "Analysis"}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Platform filter rail, view-only. Discovery/analysis on this
              backend always run across every ready platform at once, so
              there is nothing per-platform to launch from here anymore. */}
          <div className="platform-rail-grid" style={{ gridTemplateColumns: `repeat(${platforms.length}, 1fr)` }}>
            {platforms.map((p) => {
              const count = counts.platforms[p.platform] || 0;
              return (
                <div
                  key={p.platform}
                  className={`platform-rail-item ${platform === p.platform ? "active" : ""}`}
                  onClick={() => setPlatform(p.platform)}
                >
                  <div className="rail-card-head">
                    <PlatformIcon platform={p.platform} size={18} />
                    <span style={{ fontSize: "12px", fontWeight: 500 }}>{p.name}</span>
                  </div>
                  <div className="rail-card-foot" style={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
                    <span
                      className="rail-pill"
                      style={{ color: p.session_state === "ready" ? "var(--success)" : "var(--text-dim)" }}
                    >
                      {p.session_state}
                    </span>
                    <span className="rail-pill" style={{ color: count > 0 ? "var(--text-main)" : "var(--text-dim)", fontWeight: count > 0 ? 700 : 400 }}>
                      {count} {count === 1 ? "result" : "results"}
                    </span>
                  </div>
                  {(discoveryRunning || phase === "discovery") && discoveryProgress[p.platform] && (
                    <PlatformProgressRow label="🔍 Discovery" progress={discoveryProgress[p.platform]} />
                  )}
                  {analysisRunning && analysisProgress[p.platform] && (
                    <PlatformProgressRow label="📊 Analysis" progress={analysisProgress[p.platform]} />
                  )}
                </div>
              );
            })}
          </div>

          {discoveryRunning && analysisRunning && (
            <div
              className="dashboard-card-box"
              style={{
                marginTop: "16px",
                borderLeft: "4px solid var(--warn-yellow)",
                background: "linear-gradient(90deg, rgba(0,229,255,0.05), rgba(136,56,221,0.05))",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "10px" }}>
                <span style={{ fontSize: "16px" }}>⚡</span>
                <span style={{ fontWeight: 700, fontSize: "13px", color: "var(--text-main)" }}>
                  Both Jobs Running — Live Split by Platform
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {platforms.map((p) => {
                  const d = discoveryProgress[p.platform];
                  const a = analysisProgress[p.platform];
                  if (!d && !a) return null;
                  return (
                    <div
                      key={p.platform}
                      style={{
                        display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap",
                        background: "var(--bg-surface)", borderRadius: "10px", padding: "7px 12px",
                        border: "1px solid var(--border-subtle)",
                      }}
                    >
                      <PlatformIcon platform={p.platform} size={16} />
                      <span style={{ fontSize: "12px", fontWeight: 700, textTransform: "capitalize", minWidth: "70px" }}>
                        {p.name}
                      </span>
                      <span style={{ flex: 1 }} />
                      <span style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", color: d ? PLATFORM_STATUS_LOOK[d.status].color : "var(--text-dim)" }}>
                        🔍 {d ? `${PLATFORM_STATUS_LOOK[d.status].icon} ${d.processed}/${d.total || "?"}${d.status === "running" && d.eta_seconds !== null ? ` · ${formatEta(d.eta_seconds)}` : ""}` : "idle"}
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", color: a ? PLATFORM_STATUS_LOOK[a.status].color : "var(--text-dim)" }}>
                        📊 {a ? `${PLATFORM_STATUS_LOOK[a.status].icon} ${a.processed}/${a.total || "?"}${a.status === "running" && a.eta_seconds !== null ? ` · ${formatEta(a.eta_seconds)}` : ""}` : "idle"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {(discoveryRunning || (phase === "discovery" && Object.keys(discoveryProgress).length > 0)) && (
            <div className="dashboard-card-box" style={{ marginTop: "16px", borderLeft: "4px solid var(--cyan)", background: "rgba(0, 229, 255, 0.04)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "18px" }}>🔍</span>
                  <span style={{ fontWeight: 700, color: "var(--text-main)", fontSize: "14px" }}>
                    {discoveryRunning ? "Live Discovery Sweep Progress" : "Recent Discovery Status"}
                  </span>
                  {discoveryRunning ? (
                    <span className="rail-pill" style={{ background: "var(--cyan)", color: "#000", fontWeight: 700, animation: "pulse 1.5s infinite" }}>
                      RUNNING
                    </span>
                  ) : (
                    <span className="rail-pill" style={{ background: "rgba(54,181,160,0.2)", color: "var(--success)", fontWeight: 700 }}>
                      COMPLETED
                    </span>
                  )}
                  {discoveryRunning && onStopDiscovery && (
                    <button
                      type="button"
                      onClick={onStopDiscovery}
                      style={{
                        background: "linear-gradient(135deg, rgba(239,68,68,0.25), rgba(220,38,38,0.35))",
                        color: "#ff6b6b",
                        border: "1px solid rgba(239,68,68,0.5)",
                        padding: "3px 10px",
                        borderRadius: "12px",
                        fontSize: "11px",
                        fontWeight: 700,
                        cursor: "pointer",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "5px",
                        transition: "all 0.2s ease",
                      }}
                      title="Abort active discovery sweep"
                    >
                      <span>⏹</span> Stop Sweep
                    </button>
                  )}
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--cyan)" }}>
                  {Object.values(discoveryProgress).reduce((acc, p) => acc + (p.processed || 0), 0)} / {Object.values(discoveryProgress).reduce((acc, p) => acc + (p.total || 0), 0) || "?"} Sweeps Completed
                </div>
              </div>
              <div style={{ height: "8px", background: "var(--bg-inner)", borderRadius: "4px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round((Object.values(discoveryProgress).reduce((acc, p) => acc + (p.processed || 0), 0) / (Object.values(discoveryProgress).reduce((acc, p) => acc + (p.total || 0), 0) || 1)) * 100))}%`,
                    background: "linear-gradient(90deg, var(--cyan), var(--purple))",
                    transition: "width 0.4s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: "10px", marginTop: "12px", flexWrap: "wrap" }}>
                {Object.entries(discoveryProgress).map(([plat, prog]) => (
                  <div key={plat} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", background: "var(--bg-surface)", padding: "5px 12px", borderRadius: "16px", border: "1px solid var(--border-color)" }}>
                    <PlatformIcon platform={plat} size={15} />
                    <span style={{ fontWeight: 600, textTransform: "capitalize", color: "var(--text-main)" }}>{plat}:</span>
                    <span style={{ color: PLATFORM_STATUS_LOOK[prog.status]?.color || "var(--text-main)", fontWeight: 700 }}>
                      {PLATFORM_STATUS_LOOK[prog.status]?.icon} {prog.processed}/{prog.total}
                    </span>
                    {prog.eta_seconds !== null && prog.status === "running" && (
                      <span style={{ fontSize: "11px", color: "var(--text-dim)", marginLeft: "4px" }}>
                        ({formatEta(prog.eta_seconds)})
                      </span>
                    )}
                    {prog.status === "failed" && !discoveryRunning && (
                      <button
                        type="button"
                        onClick={async (e) => {
                          e.stopPropagation();
                          try {
                            await discoveryApi.discover({ client_id: clientId, keywords: clientNameKeywords.concat(clientDomainKeywords), platform: plat });
                            toast.success(`Started discovery retry for ${plat}`);
                          } catch (err) {
                            onError?.((err as Error).message);
                          }
                        }}
                        style={{
                          background: "rgba(239, 68, 68, 0.2)",
                          border: "1px solid rgba(239, 68, 68, 0.4)",
                          color: "#fca5a5",
                          borderRadius: "6px",
                          padding: "2px 6px",
                          fontSize: "10px",
                          cursor: "pointer",
                          fontWeight: 600,
                        }}
                        title={`Retry discovery for ${plat}`}
                      >
                        🔄 Retry
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {(analysisRunning || (phase === "analysis" && Object.keys(analysisProgress).length > 0)) && (
            <div className="dashboard-card-box" style={{ marginTop: "16px", borderLeft: "4px solid var(--purple)", background: "rgba(136, 56, 221, 0.05)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "18px" }}>📊</span>
                  <span style={{ fontWeight: 700, color: "var(--text-main)", fontSize: "14px" }}>
                    {analysisRunning ? "Live Analysis Progress" : "Recent Analysis Status"}
                  </span>
                  {analysisRunning ? (
                    <span className="rail-pill" style={{ background: "var(--purple)", color: "#fff", fontWeight: 700, animation: "pulse 1.5s infinite" }}>
                      RUNNING
                    </span>
                  ) : (
                    <span className="rail-pill" style={{ background: "rgba(54,181,160,0.2)", color: "var(--success)", fontWeight: 700 }}>
                      COMPLETED
                    </span>
                  )}
                  {analysisRunning && onStopAnalysis && (
                    <button
                      type="button"
                      onClick={onStopAnalysis}
                      style={{
                        background: "linear-gradient(135deg, rgba(239,68,68,0.25), rgba(220,38,38,0.35))",
                        color: "#ff6b6b",
                        border: "1px solid rgba(239,68,68,0.5)",
                        padding: "3px 10px",
                        borderRadius: "12px",
                        fontSize: "11px",
                        fontWeight: 700,
                        cursor: "pointer",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "5px",
                        transition: "all 0.2s ease",
                      }}
                      title="Abort active analysis run"
                    >
                      <span>⏹</span> Stop Analysis
                    </button>
                  )}
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "13px", fontWeight: 700, color: "var(--purple)" }}>
                  {Object.values(analysisProgress).reduce((acc, p) => acc + (p.processed || 0), 0)} / {Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0) || "?"} Profiles Analysed
                  {Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0) > 0 ? ` (${Math.round((Object.values(analysisProgress).reduce((acc, p) => acc + (p.processed || 0), 0) / Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0)) * 100)}%)` : ""}
                </div>
              </div>
              <div style={{ height: "8px", background: "var(--bg-inner)", borderRadius: "4px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round((Object.values(analysisProgress).reduce((acc, p) => acc + (p.processed || 0), 0) / (Object.values(analysisProgress).reduce((acc, p) => acc + (p.total || 0), 0) || 1)) * 100))}%`,
                    background: "linear-gradient(90deg, var(--purple), var(--cyan), var(--success))",
                    transition: "width 0.4s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: "10px", marginTop: "12px", flexWrap: "wrap" }}>
                {Object.entries(analysisProgress).map(([plat, prog]) => (
                  <div key={plat} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", background: "var(--bg-surface)", padding: "5px 12px", borderRadius: "16px", border: "1px solid var(--border-color)" }}>
                    <PlatformIcon platform={plat} size={15} />
                    <span style={{ fontWeight: 600, textTransform: "capitalize", color: "var(--text-main)" }}>{plat}:</span>
                    <span style={{ color: PLATFORM_STATUS_LOOK[prog.status]?.color || "var(--text-main)", fontWeight: 700 }}>
                      {PLATFORM_STATUS_LOOK[prog.status]?.icon} {prog.processed}/{prog.total}
                    </span>
                    {prog.eta_seconds !== null && prog.status === "running" && (
                      <span style={{ fontSize: "11px", color: "var(--text-dim)", marginLeft: "4px" }}>
                        ({formatEta(prog.eta_seconds)})
                      </span>
                    )}
                    {prog.status === "failed" && !analysisRunning && (
                      <button
                        type="button"
                        onClick={async (e) => {
                          e.stopPropagation();
                          try {
                            await analysisApi.analyse({ client_id: clientId, platform: plat });
                            toast.success(`Started analysis retry for ${plat}`);
                          } catch (err) {
                            onError?.((err as Error).message);
                          }
                        }}
                        style={{
                          background: "rgba(239, 68, 68, 0.2)",
                          border: "1px solid rgba(239, 68, 68, 0.4)",
                          color: "#fca5a5",
                          borderRadius: "6px",
                          padding: "2px 6px",
                          fontSize: "10px",
                          cursor: "pointer",
                          fontWeight: 600,
                        }}
                        title={`Retry analysis for ${plat}`}
                      >
                        🔄 Retry
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {discoveryRunning && discoveryLog.length > 0 && <LiveFeed title="Discovery Feed" log={discoveryLog} />}
          {analysisRunning && analysisLog.length > 0 && <LiveFeed title="Analysis Feed" log={analysisLog} />}

          {!isAnalysisView && (
            <div className="status-summary-row" style={{ marginTop: "16px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {(["pending", "approved", "rejected"] as const).map((s) => {
                const count = counts.statuses[s] ?? 0;
                const look = {
                  pending: { label: "⏳ Pending", color: "var(--purple)", text: "var(--text-main)" },
                  approved: { label: "✅ Validated", color: "var(--cyan-bright)", text: "var(--cyan-bright)" },
                  rejected: { label: "✕ Rejected", color: "#c084fc", text: "#c084fc" },
                }[s];
                return (
                  <button
                    key={s}
                    className={`status-chip ${status === s ? "on" : ""}`}
                    onClick={() => setStatus(status === s ? "" : s)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      padding: "6px 14px",
                      borderRadius: "20px",
                      border: `1px solid ${status === s ? look.color : "var(--border-color)"}`,
                      background: status === s ? "var(--bg-surface)" : "transparent",
                      cursor: "pointer",
                      fontSize: "12px",
                      fontWeight: 600,
                      color: status === s ? look.text : "var(--text-muted)",
                      transition: "all 0.2s ease",
                    }}
                  >
                    <span>{look.label}</span>
                    <span
                      style={{
                        background: status === s ? look.color : "var(--bg-inner)",
                        color: status === s ? "#fff" : "var(--text-dim)",
                        padding: "2px 8px",
                        borderRadius: "12px",
                        fontSize: "11px",
                        fontWeight: 700,
                      }}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {/* Page-wide "clear the queue" fast path, no selection needed at
              all. Deliberately scoped to PENDING rows on this page only:
              it decides what's still awaiting a call, it never silently
              overrides a decision already made (an already-approved or
              already-rejected row on the same page is left untouched). If
              the analyst has the Pending status chip active, `displayed`
              is already only pending rows, so this reads as "decide
              everything on screen", exactly the one-click-per-page
              workflow that was missing. */}
          {!isAnalysisView && displayed.length > 0 && (() => {
            const pendingOnPage = displayed.filter((r) => r.status === "pending").map((r) => r.id);
            if (!pendingOnPage.length) return null;
            return (
              <div
                className="dashboard-card-box"
                style={{
                  marginTop: "12px", display: "flex", alignItems: "center", gap: "10px",
                  flexWrap: "wrap", borderLeft: "4px solid var(--cyan-bright)",
                }}
              >
                <span style={{ fontSize: "12px", fontWeight: 700 }}>
                  ⚡ {pendingOnPage.length} pending on this page
                </span>
                <button
                  className="btn-cyber-primary"
                  style={{ width: "auto", padding: "6px 12px", fontSize: "11px", marginTop: 0, background: "rgba(154,80,233,0.15)", color: "var(--cyan-bright)", border: "1px solid var(--cyan-bright)" }}
                  disabled={bulkBusy}
                  onClick={() => bulkDecide("approved", pendingOnPage)}
                  title="Validates every pending profile currently shown on this page"
                >
                  {bulkBusy ? "…" : `✅ Validate All (${pendingOnPage.length})`}
                </button>
                <button
                  className="btn-cyber-primary"
                  style={{ width: "auto", padding: "6px 12px", fontSize: "11px", marginTop: 0, background: "rgba(192,132,252,0.15)", color: "#c084fc", border: "1px solid #c084fc" }}
                  disabled={bulkBusy}
                  onClick={() => bulkDecide("rejected", pendingOnPage)}
                  title="Rejects every pending profile currently shown on this page"
                >
                  {bulkBusy ? "…" : `✕ Reject All (${pendingOnPage.length})`}
                </button>
              </div>
            );
          })()}

          {/* Bulk triage bar, for a targeted subset instead of the whole
              page: check specific cards (or drag across them, see
              dragSelectHandlers) and decide just those. "select all" only
              ever means "on this page" (see toggleSelectAllOnPage), so
              this never silently acts on rows the analyst hasn't actually
              looked at. */}
          {!isAnalysisView && selectedIds.size > 0 && (
            <div
              className="dashboard-card-box"
              style={{
                marginTop: "12px", display: "flex", alignItems: "center", gap: "10px",
                flexWrap: "wrap", borderLeft: "4px solid var(--cyan)",
              }}
            >
              <span style={{ fontSize: "12px", fontWeight: 700 }}>
                {selectedIds.size} selected
              </span>
              <button
                className="btn-cyber-primary"
                style={{ width: "auto", padding: "6px 12px", fontSize: "11px", marginTop: 0, background: "rgba(154,80,233,0.15)", color: "var(--cyan-bright)", border: "1px solid var(--cyan-bright)" }}
                disabled={bulkBusy}
                onClick={() => bulkDecide("approved")}
              >
                {bulkBusy ? "…" : `✅ Validate ${selectedIds.size}`}
              </button>
              <button
                className="btn-cyber-primary"
                style={{ width: "auto", padding: "6px 12px", fontSize: "11px", marginTop: 0, background: "rgba(192,132,252,0.15)", color: "#c084fc", border: "1px solid #c084fc" }}
                disabled={bulkBusy}
                onClick={() => bulkDecide("rejected")}
              >
                {bulkBusy ? "…" : `✕ Reject ${selectedIds.size}`}
              </button>
              {isFacebook && (
                <button
                  style={{ width: "auto", padding: "6px 12px", fontSize: "11px", marginTop: 0, background: "rgba(0,229,255,0.1)", color: "var(--cyan)", border: "1px solid var(--cyan)", borderRadius: "8px", cursor: resweepBusy ? "wait" : "pointer" }}
                  disabled={resweepBusy || bulkBusy}
                  onClick={() => resweepSelected()}
                  title="Re-visits just these profiles to fetch a real name/photo -- fixes a card stuck showing a bare numeric id, without a full keyword re-sweep"
                >
                  {resweepBusy ? "🔄 Re-resolving…" : `🔄 Re-sweep ${selectedIds.size}`}
                </button>
              )}
              <button
                style={{ width: "auto", padding: "6px 10px", fontSize: "11px", background: "transparent", border: "1px solid var(--border-color)", borderRadius: "8px", color: "var(--text-muted)", cursor: "pointer" }}
                onClick={() => setSelectedIds(new Set())}
                disabled={bulkBusy}
                title="Or just press Esc"
              >
                Clear <span style={{ opacity: 0.6 }}>(Esc)</span>
              </button>
            </div>
          )}

          {/* Analysis-phase multi-select bulk apply, lets an analyst pick
              several profiles at once and set the same Asset Name across
              all of them in one action, sourced from the client's
              standalone drk_keywords list (see IncidentAssetNameField). */}
          {isAnalysisView && selectedIds.size > 0 && (
            <div
              className="dashboard-card-box"
              style={{
                marginTop: "12px", display: "flex", alignItems: "center", gap: "10px",
                flexWrap: "wrap", borderLeft: "4px solid var(--cyan)",
              }}
            >
              <span style={{ fontSize: "12px", fontWeight: 700 }}>
                {selectedIds.size} selected
              </span>
              {drkOptions.length > 0 ? (
                <select
                  className="select-filter"
                  defaultValue=""
                  disabled={bulkAssetNameBusy}
                  onChange={(e) => {
                    if (e.target.value) bulkSetAssetName(e.target.value);
                    e.target.value = "";
                  }}
                  title="Apply this Asset Name to every selected profile"
                >
                  <option value="" disabled>
                    {bulkAssetNameBusy ? "Applying…" : "Set Asset Name to…"}
                  </option>
                  {drkOptions.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : (
                <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
                  Add Asset Names on the client's config form to bulk-apply one here.
                </span>
              )}
              <button
                style={{ width: "auto", padding: "6px 10px", fontSize: "11px", background: "transparent", border: "1px solid var(--border-color)", borderRadius: "8px", color: "var(--text-muted)", cursor: "pointer" }}
                onClick={() => setSelectedIds(new Set())}
                disabled={bulkAssetNameBusy}
                title="Or just press Esc"
              >
                Clear <span style={{ opacity: 0.6 }}>(Esc)</span>
              </button>
            </div>
          )}


          {/* Filter toolbar */}
          <div className="filter-toolbar" style={{ marginTop: "12px" }}>
            {/* Same exact-match dropdown in both views now, this used to be
                freetext in analysis view because the server-side filter
                wasn't being sent there (see load()), so it only ever
                filtered whatever page happened to already be loaded: typing
                a real keyword could show "no results" simply because the
                matches were on a different page. Scoping the query
                server-side (like discovery always did) fixes that, and a
                dropdown of the client's actual keywords is also just a
                better match for "exact keyword" than freetext ever was. */}
            <select
              value={keywordFilter}
              onChange={(e) => setKeywordFilter(e.target.value)}
              className="select-filter"
              title="Only show profiles found by this exact keyword"
            >
              <option value="">All Keywords</option>
              {Object.entries(counts.keywords)
                .sort((a, b) => b[1] - a[1])
                .map(([kw, n]) => (
                  <option key={kw} value={kw}>
                    🔑 {kw} ({n})
                  </option>
                ))}
            </select>
            <select
              value={keywordMatchType}
              onChange={(e) => setKeywordMatchType(e.target.value as "" | "individual" | "domain")}
              className="select-filter"
              title="Filter to profiles matched via an Individual Name keyword vs a Domain keyword, per this client's configured keyword lists"
            >
              <option value="">Individual + Domain</option>
              <option value="individual">👤 Individual Match Only</option>
              <option value="domain">🏷️ Domain Match Only</option>
            </select>
            {!isAnalysisView && (
              <select
                value={matchLevel}
                onChange={(e) => setMatchLevel(e.target.value as "" | "high" | "medium" | "low")}
                className="select-filter"
                title="How closely the scraped name matches the keyword that found it"
              >
                <option value="">All Match Levels</option>
                <option value="high">🎯 High Match</option>
                <option value="medium">🎯 Medium Match</option>
                <option value="low">🎯 Low Match</option>
              </select>
            )}
            {isFacebookPlatform && (
              <select
                value={entityType}
                onChange={(e) => setEntityType(e.target.value as "" | "profile" | "page" | "group")}
                className="select-filter"
                title="Facebook discovery distinguishes people, Pages, and Groups -- filter to just one"
              >
                <option value="">People + Pages + Groups</option>
                <option value="profile">👤 People Only</option>
                <option value="page">📄 Pages Only</option>
                <option value="group">👥 Groups Only</option>
              </select>
            )}
            {isAnalysisView && (
              <>
                <select value={sortOrder} onChange={(e) => setSortOrder(e.target.value as "recent" | "past")} className="select-filter">
                  <option value="recent">Sort: Highest Risk / Score</option>
                  <option value="past">Sort: Lowest Risk / Score</option>
                </select>
                <select value={priority} onChange={(e) => setPriority(e.target.value)} className="select-filter">
                  <option value="">All Priorities</option>
                  <option value="High">HIGH Priority</option>
                  <option value="Medium">MEDIUM Priority</option>
                  <option value="Low">LOW Priority</option>
                </select>
              </>
            )}
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="🔎 Search name / handle…"
              className="input-filter"
              style={{ flex: 1, minWidth: "160px" }}
            />
            {/* Card view is discovery-only, an analysis card is the full
                incident-edit panel (~15 fields) permanently expanded, which
                makes a card grid unwieldy compared to the table's one-row-
                per-profile density. Analysis always renders as a table;
                the toggle itself is hidden there since there's nothing to
                toggle to. */}
            {!isAnalysisView && (
              <div style={{ display: "flex", gap: "6px" }}>
                <button
                  onClick={() => setViewMode("grid")}
                  title="Card view"
                  style={{
                    background: viewMode === "grid" ? "rgba(136, 56, 221,0.12)" : "var(--bg-surface)",
                    border: `1px solid ${viewMode === "grid" ? "var(--cyan)" : "var(--border-color)"}`,
                    color: viewMode === "grid" ? "var(--text-main)" : "var(--text-muted)",
                    borderRadius: "8px",
                    padding: "7px 10px",
                    cursor: "pointer",
                  }}
                >
                  📱 Cards
                </button>
                <button
                  onClick={() => setViewMode("table")}
                  title="Table view"
                  style={{
                    background: viewMode === "table" ? "rgba(136, 56, 221,0.12)" : "var(--bg-surface)",
                    border: `1px solid ${viewMode === "table" ? "var(--cyan)" : "var(--border-color)"}`,
                    color: viewMode === "table" ? "var(--text-main)" : "var(--text-muted)",
                    borderRadius: "8px",
                    padding: "7px 10px",
                    cursor: "pointer",
                  }}
                >
                  📋 Table
                </button>
              </div>
            )}
            <button
              onClick={toggleSplitView}
              title="Toggle side-by-side Live Inspection pane (I)"
              style={{
                background: splitViewOpen ? "rgba(0, 229, 255, 0.25)" : "var(--bg-surface)",
                border: `1.5px solid ${splitViewOpen ? "var(--cyan, #00E5FF)" : "var(--border-color)"}`,
                color: splitViewOpen ? "var(--cyan, #00E5FF)" : "var(--text-muted)",
                borderRadius: "8px",
                padding: "7px 12px",
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                fontSize: "12px",
                fontWeight: 700,
                boxShadow: splitViewOpen ? "0 0 14px rgba(0, 229, 255, 0.35)" : "none",
                transition: "all 0.2s ease",
              }}
            >
              <span>🖥️</span> {splitViewOpen ? "Split View (ON)" : "Split View"}
            </button>
            {/* 📋 Unified Copy Dropdown */}
            <div className="action-dropdown-container" ref={copyMenuRef}>
              <button
                className="btn-cyber-primary"
                style={{
                  padding: "7px 12px",
                  fontSize: "11px",
                  marginTop: 0,
                  width: "auto",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "5px",
                  background:
                    copyUrlState === "copied"
                      ? "var(--success)"
                      : copyUrlState === "failed"
                      ? "var(--danger)"
                      : "rgba(54, 181, 160, 0.15)",
                  color: copyUrlState === "copied" ? "#fff" : "var(--cyan)",
                  border: `1px solid ${copyUrlState === "copied" ? "var(--success)" : "var(--cyan)"}`,
                }}
                onClick={() => {
                  setCopyMenuOpen(!copyMenuOpen);
                  setExportMenuOpen(false);
                }}
                title="Copy profile URLs or formatted table rows to clipboard"
              >
                {copyUrlState === "copied" ? (
                  "✓ Copied"
                ) : copyUrlState === "failed" ? (
                  "✕ Failed"
                ) : selectedIds.size > 0 ? (
                  `📋 Copy (${selectedIds.size}) ▾`
                ) : (
                  "📋 Copy ▾"
                )}
              </button>

              {copyMenuOpen && (
                <div className="action-dropdown-menu">
                  <div className="action-dropdown-header">Copy Options</div>
                  {selectedIds.size > 0 ? (
                    <div className="action-dropdown-scope-badge">
                      <span>🎯</span> {selectedIds.size} Selected Row{selectedIds.size > 1 ? "s" : ""}
                    </div>
                  ) : (
                    <div className="action-dropdown-scope-badge" style={{ background: "rgba(148, 163, 184, 0.12)", color: "var(--text-dim)" }}>
                      <span>🌐</span> All Filtered ({displayed.length})
                    </div>
                  )}

                  <button
                    className="action-dropdown-item"
                    onClick={() => handleCopy("urls")}
                  >
                    <div className="action-dropdown-item-left">
                      <span className="action-dropdown-item-icon">🔗</span>
                      <span>{selectedIds.size > 0 ? `Copy Selected URLs (${selectedIds.size})` : "Copy Profile URLs"}</span>
                    </div>
                    <span className="action-dropdown-item-badge">1-per-line</span>
                  </button>

                  <div className="action-dropdown-divider" />

                  {isAnalysisView ? (
                    <>
                      <button
                        className="action-dropdown-item"
                        onClick={() => handleCopy("table", "incident")}
                      >
                        <div className="action-dropdown-item-left">
                          <span className="action-dropdown-item-icon">📊</span>
                          <span>Copy Table (Platform Format)</span>
                        </div>
                        <span className="action-dropdown-item-badge">TSV</span>
                      </button>

                      <button
                        className="action-dropdown-item"
                        onClick={() => handleCopy("table", "legacy")}
                      >
                        <div className="action-dropdown-item-left">
                          <span className="action-dropdown-item-icon">📑</span>
                          <span>Copy Table (Legacy Format)</span>
                        </div>
                        <span className="action-dropdown-item-badge">TSV</span>
                      </button>
                    </>
                  ) : (
                    <button
                      className="action-dropdown-item"
                      onClick={() => handleCopy("table")}
                    >
                      <div className="action-dropdown-item-left">
                        <span className="action-dropdown-item-icon">📊</span>
                        <span>Copy Table Data</span>
                      </div>
                      <span className="action-dropdown-item-badge">TSV</span>
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* 📥 Unified Export Dropdown */}
            <div className="action-dropdown-container" ref={exportMenuRef}>
              <button
                className="btn-cyber-primary"
                style={{
                  padding: "7px 12px",
                  fontSize: "11px",
                  marginTop: 0,
                  width: "auto",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "5px",
                }}
                onClick={() => {
                  setExportMenuOpen(!exportMenuOpen);
                  setCopyMenuOpen(false);
                }}
                disabled={exporting || !clientId}
                title="Download table data as Excel (.xlsx), CSV, or JSON"
              >
                {exporting ? "⏳ Exporting…" : "📥 Export ▾"}
              </button>

              {exportMenuOpen && (
                <div className="action-dropdown-menu" style={{ minWidth: "290px" }}>
                  <div className="action-dropdown-header">Export Data</div>
                  {selectedIds.size > 0 ? (
                    <div className="action-dropdown-scope-badge">
                      <span>🎯</span> Exporting {selectedIds.size} Selected Profile{selectedIds.size > 1 ? "s" : ""}
                    </div>
                  ) : (
                    <div className="action-dropdown-scope-badge" style={{ background: "rgba(148, 163, 184, 0.12)", color: "var(--text-dim)" }}>
                      <span>🌐</span> Exporting All Filtered ({total || displayed.length})
                    </div>
                  )}

                  {isAnalysisView && (
                    <>
                      <div className="action-dropdown-header" style={{ marginTop: "4px" }}>Column Layout Preset</div>
                      <div className="action-format-selector">
                        <button
                          type="button"
                          className={`action-format-btn ${exportFormat === "incident" ? "active" : ""}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setExportFormat("incident");
                          }}
                          title="Platform Format: OrgId, Domain, Platform, AssetType, Incident Title, Source URL, Risk Rating, etc."
                        >
                          Platform Format
                        </button>
                        <button
                          type="button"
                          className={`action-format-btn ${exportFormat === "legacy" ? "active" : ""}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setExportFormat("legacy");
                          }}
                          title="Legacy Format: Original Name, IMPERSONATED, Profile name, Profile URL, Followers, etc."
                        >
                          Legacy Format
                        </button>
                      </div>
                    </>
                  )}

                  <button
                    className="action-dropdown-item"
                    onClick={() => handleExport("xlsx")}
                    disabled={exporting}
                  >
                    <div className="action-dropdown-item-left">
                      <span className="action-dropdown-item-icon">📗</span>
                      <span>Excel Spreadsheet</span>
                    </div>
                    <span className="action-dropdown-item-badge">.xlsx</span>
                  </button>

                  <button
                    className="action-dropdown-item"
                    onClick={() => handleExport("csv")}
                    disabled={exporting}
                  >
                    <div className="action-dropdown-item-left">
                      <span className="action-dropdown-item-icon">📄</span>
                      <span>CSV Document</span>
                    </div>
                    <span className="action-dropdown-item-badge">.csv</span>
                  </button>

                  <button
                    className="action-dropdown-item"
                    onClick={() => handleExport("json")}
                    disabled={exporting}
                  >
                    <div className="action-dropdown-item-left">
                      <span className="action-dropdown-item-icon">📦</span>
                      <span>JSON Export</span>
                    </div>
                    <span className="action-dropdown-item-badge">.json</span>
                  </button>
                </div>
              )}
            </div>
            {!isAnalysisView && (
              <button
                className="btn-cyber-primary"
                style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto", background: "rgba(221, 56, 59, 0.15)", color: "var(--danger, #DD383B)", border: "1px solid var(--danger, #DD383B)" }}
                onClick={handleDeletePlatformData}
                disabled={deletingPlatformData || !clientId || !platform}
                title="Permanently delete every Discovery and Analysis profile, screenshot, and published incident for this platform and client"
              >
                {deletingPlatformData ? "Deleting…" : "🗑 Delete Platform Data"}
              </button>
            )}
            {isAnalysisView && (
              <>
                <button
                  className="btn-cyber-primary"
                  style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto", background: "rgba(0, 229, 255, 0.15)", color: "var(--cyan)", border: "1px solid var(--cyan)" }}
                  onClick={() => setManualUrlsOpen(true)}
                >
                  🔗 Add URLs
                </button>
                <select
                  value={publishScope}
                  onChange={(e) => setPublishScope(e.target.value as typeof publishScope)}
                  disabled={publishingAll}
                  title="Which analysed profiles Publish should include"
                  style={{
                    padding: "6px 8px", fontSize: "11px", background: "var(--bg-inner)",
                    border: "1px solid var(--border-color)", borderRadius: "8px", color: "var(--text-main)",
                  }}
                >
                  {(Object.keys(PUBLISH_SCOPE_LABELS) as (typeof publishScope)[]).map((s) => (
                    <option key={s} value={s}>{PUBLISH_SCOPE_LABELS[s]}</option>
                  ))}
                </select>
                <button
                  className="btn-cyber-primary"
                  style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }}
                  onClick={publishAll}
                  disabled={publishingAll || !clientId}
                  title="Publish held analysis results matching the current platform view and selected scope"
                >
                  {publishingAll ? "Publishing…" : `📢 Publish ${publishScope === "all" ? "All" : PUBLISH_SCOPE_LABELS[publishScope]}`}
                </button>
                <button
                  className="btn-cyber-primary"
                  style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto", background: "rgba(221, 56, 59, 0.15)", color: "var(--danger, #DD383B)", border: "1px solid var(--danger, #DD383B)" }}
                  onClick={handleDeletePlatformData}
                  disabled={deletingPlatformData || !clientId || !platform}
                  title="Permanently delete every Discovery and Analysis profile, screenshot, and published incident for this platform and client"
                >
                  {deletingPlatformData ? "Deleting…" : "🗑 Delete Platform Data"}
                </button>
              </>
            )}
          </div>

          {isAnalysisView && coverage && !coverage.complete && (
            <div className={`coverage-banner${coverage.analysis_failed ? " coverage-blocked" : ""}`}>
              <span>
                {coverage.analysis_failed > 0 ? "⛔" : "⏳"} Coverage incomplete —{" "}
                <strong>{coverage.analysed}</strong> of <strong>{coverage.approved}</strong> validated
                profiles analysed
              </span>
              <span className="coverage-banner-detail">
                {coverage.awaiting_analysis > 0 && `${coverage.awaiting_analysis} still queued`}
                {coverage.awaiting_analysis > 0 && coverage.analysis_failed > 0 && " · "}
                {coverage.analysis_failed > 0 &&
                  `${coverage.analysis_failed} could not be read and will not be retried automatically`}
              </span>
              {coverage.blocked.length > 0 && (
                <button onClick={() => setCoverageOpen((v) => !v)}>
                  {coverageOpen ? "hide" : "show which"}
                </button>
              )}
            </div>
          )}

          {isAnalysisView && coverageOpen && coverage && coverage.blocked.length > 0 && (
            <div className="coverage-banner coverage-blocked" style={{ display: "block" }}>
              <div style={{ marginBottom: "6px" }}>
                These validated profiles were never successfully read. A client report is not complete
                until each is retried or explicitly written off:
              </div>
              <ul style={{ margin: 0, paddingLeft: "18px" }}>
                {coverage.blocked.map((b) => (
                  <li key={b.id} style={{ marginBottom: "3px" }}>
                    <a href={b.url} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>
                      {b.profile_name || b.url}
                    </a>{" "}
                    <span className="coverage-banner-detail">
                      ({b.platform} · {b.reason} after {b.attempts} attempt{b.attempts === 1 ? "" : "s"}
                      {b.detail ? ` · ${b.detail}` : ""})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Published/Unpublished filter, analysis only, sits right above
              the table (below every other filter/coverage banner). A
              published row is a confirmed, client-facing finding; an
              unpublished one is still on its hold or awaiting an explicit
              Publish (see backend/docs/adr/0007-publish-hold.md). Equal-width
              slots so the underline can slide between them with a plain CSS
              transform transition, no width measurement needed. */}
          {isAnalysisView && (
            <div style={{ position: "relative", display: "inline-flex", marginBottom: "10px" }}>
              {(["published", "unpublished"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPublishedFilter(key)}
                  title={
                    key === "published"
                      ? "Findings already confirmed and visible to the client"
                      : "Still on the publish hold, or awaiting an explicit Publish"
                  }
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    width: "140px",
                    textAlign: "left",
                    padding: "6px 0 10px",
                    fontSize: "14px",
                    fontWeight: 600,
                    fontFamily: "inherit",
                    color: publishedFilter === key ? "var(--purple)" : "var(--text-muted)",
                    transition: "color 0.2s ease",
                  }}
                >
                  {key === "published" ? "Published" : "Unpublished"}
                </button>
              ))}
              <span
                aria-hidden="true"
                style={{
                  position: "absolute",
                  left: 0,
                  bottom: 0,
                  height: "2px",
                  width: "140px",
                  borderRadius: "2px",
                  background: "var(--purple)",
                  transform: `translateX(${publishedFilter === "published" ? 0 : 140}px)`,
                  transition: "transform 0.25s ease",
                }}
              />
            </div>
          )}
{loading && <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>Loading…</div>}

          {!loading && !clientId && (
            <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>
              Set a client on Live Discovery to see results.
            </div>
          )}

          {!loading && clientId && !displayed.length && (
            <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>
              No profiles match the current filters.
            </div>
          )}

          {!loading && displayed.length > 0 && !isAnalysisView && viewMode === "grid" && (
            <div className={splitViewOpen ? "results-split-layout" : undefined} style={{ marginTop: "12px" }}>
              <div className={splitViewOpen ? "split-table-wrapper" : undefined} style={{ minWidth: 0, width: "100%" }}>
                <div className="profile-grid-container">
                  {displayed.map((r, i) => (
                    <div
                      key={r.id}
                      onClick={() => setFocusedIndex(i)}
                      style={{
                        borderRadius: "12px",
                        outline: focusedIndex === i && splitViewOpen ? "2px solid var(--cyan, #00E5FF)" : "none",
                        outlineOffset: "2px",
                        transition: "outline 0.15s ease",
                      }}
                    >
                      <ProfileCard
                        r={r} isAnalysisView={isAnalysisView} savingId={savingId}
                        onDecide={decide} onValidate={validate}
                        onSaveIncidentField={saveIncidentField}
                        selected={selectedIds.has(r.id)} onToggleSelected={toggleSelected}
                        dragHandlers={dragSelectHandlers(r.id)}
                        onOpenDiff={(p) => setDiffProfile(p)}
                      />
                    </div>
                  ))}
                </div>
              </div>
              {splitViewOpen && (
                <LiveInspectionPane
                  profile={focusedIndex >= 0 && focusedIndex < displayed.length ? displayed[focusedIndex] : displayed[0]}
                  isAnalysisView={isAnalysisView}
                  onValidate={validate}
                  onReject={(id) => decide(id, "rejected")}
                  onEdit={(id) => setEditingId(id)}
                  onClose={toggleSplitView}
                />
              )}
            </div>
          )}

          {!loading && displayed.length > 0 && (isAnalysisView || viewMode === "table") && (
            // Keyed on the Published/Unpublished filter so switching it
            // remounts this wrapper and replays the fade-in, a visible
            <div
              key={isAnalysisView ? publishedFilter : "table"}
              className={splitViewOpen ? "results-split-layout" : undefined}
              style={{ marginTop: "12px", animation: "fadeUp 0.3s ease" }}
            >
              <div
                className={splitViewOpen ? "split-table-wrapper" : undefined}
                style={{ overflowX: "auto", minWidth: 0 }}
              >
                <table className="core_table">
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        checked={allOnPageSelected}
                        onChange={toggleSelectAllOnPage}
                        title="Select all on this page"
                      />
                    </th>
                    <th></th>
                    <th>Name</th>
                    <th>Platform</th>
                    {isAnalysisView && <th>Screenshot</th>}
                    {isAnalysisView && <th>AssetName</th>}
                    {isAnalysisView && <th>Risk</th>}
                    {isAnalysisView && <th>Category</th>}
                    {isAnalysisView && <th>Domain</th>}
                    {isAnalysisView && <th>Followers</th>}
                    {isAnalysisView && <th>Location</th>}
                    {isAnalysisView && <th>Last Post</th>}
                    {isAnalysisView && <th>Username Match</th>}
                    {isAnalysisView && <th>Logo Match</th>}
                    {isAnalysisView && <th>Active</th>}
                    {isAnalysisView && <th>Date</th>}
                    {isAnalysisView && <th style={{ textAlign: "center" }}>Risk Score</th>}
                    {!isAnalysisView && <th>Status</th>}
                    <th className="core_table-actions-cell">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && displayed.length === 0 ? (
                    Array.from({ length: 7 }).map((_, i) => (
                      <tr key={`skeleton-${i}`}>
                        <td colSpan={isAnalysisView ? 16 : 6} style={{ padding: '8px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
                          <div className="skeleton-row" style={{ width: '100%', opacity: Math.max(0.1, 1 - (i * 0.15)) }} />
                        </td>
                      </tr>
                    ))
                  ) : displayed.length === 0 ? (
                    <tr>
                      <td colSpan={isAnalysisView ? 16 : 6} style={{ textAlign: "center", padding: "40px", color: "var(--text-dim)" }}>
                        No profiles match the current filters.
                      </td>
                    </tr>
                  ) : (
                    displayed.map((r, i) => {
                      const isHeld = isAnalysisView && r.published === false;
                      const inc = r.incident;
                      return (
                      <tr
                        key={r.id}
                        {...dragSelectHandlers(r.id)}
                        onClick={() => setFocusedIndex(i)}
                        className={focusedIndex === i ? "row-focused" : undefined}
                        style={selectedIds.has(r.id) ? { outline: "2px solid var(--primary)", outlineOffset: "-2px", boxShadow: "0 0 12px rgba(136, 56, 221, 0.35)", background: "rgba(136, 56, 221, 0.08)" } : undefined}
                      >
                      <td>
                        <input
                          type="checkbox"
                          checked={selectedIds.has(r.id)}
                          onChange={() => toggleSelected(r.id)}
                          title={isAnalysisView ? "Select for bulk Asset Name apply" : "Select for bulk approve/reject"}
                        />
                      </td>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: isAnalysisView ? "10px" : "4px" }}>
                          <ProfileAvatar
                            r={r}
                            size={isAnalysisView ? 52 : 28}
                            style={isAnalysisView ? { border: "2px solid rgba(0, 229, 255, 0.35)", boxShadow: "0 2px 10px rgba(0, 229, 255, 0.15)" } : undefined}
                          />

                        </div>
                      </td>
                      <td style={{ maxWidth: "220px", position: "relative" }}>
                        <a
                          href={isAnalysisView && inc ? inc.source : r.url}
                          target="_blank" rel="noreferrer" style={{ color: "var(--text-main)" }}
                          title={isAnalysisView && inc ? inc.title : r.profile_name || r.username || r.url}
                          className="table-name-truncate"
                        >
                          {isAnalysisView && inc ? inc.title : r.profile_name || r.username || r.url}
                        </a>
                        {r.verified && <span className="verified-check" title="Verified account on this platform"> ✓</span>}
                        {r.has_logo && <span title="Uses a logo/brand photo"> 🏷️</span>}
                        <div className="row-quick-actions">
                          <button
                            type="button"
                            className="row-quick-action-btn"
                            title="Copy profile URL"
                            onClick={(e) => {
                              e.stopPropagation();
                              const targetUrl = isAnalysisView && inc ? inc.source : r.url;
                              navigator.clipboard.writeText(targetUrl);
                              toast.success("Profile URL copied!", { duration: 2000, id: `copy-${r.id}` });
                            }}
                          >
                            📋
                          </button>
                          {isAnalysisView && inc && (
                            <button
                              type="button"
                              className="row-quick-action-btn"
                              title="Edit incident details"
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditingId(r.id);
                              }}
                            >
                              ✏️
                            </button>
                          )}
                        </div>
                      </td>
                      <td><PlatformIcon platform={r.platform} size={16} /></td>
                      {isAnalysisView && (
                        <td
                          onClick={(e) => e.stopPropagation()}
                          onMouseDown={(e) => e.stopPropagation()}
                          onPointerDown={(e) => e.stopPropagation()}
                        >
                          <ScreenshotCell r={r} />
                        </td>
                      )}
                      {isAnalysisView && (
                        <td title={inc?.assetName ?? ""} style={{ maxWidth: "140px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#ffffff" }}>
                          {inc?.assetName || "—"}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          {(() => {
                            const risk = getRiskBadgeDetails(inc?.riskRating);
                            if (risk.label === "—") return "—";
                            return (
                              <span
                                className="risk-capsule-badge"
                                style={{
                                  background: risk.color,
                                  color: "#ffffff",
                                  padding: "3px 12px",
                                  borderRadius: "14px",
                                  fontSize: "11px",
                                  fontWeight: 700,
                                  display: "inline-block",
                                  letterSpacing: "0.4px",
                                  boxShadow: `0 2px 8px ${risk.color}40`,
                                  textTransform: "capitalize",
                                }}
                              >
                                {risk.label}
                              </span>
                            );
                          })()}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "#ffffff" }}>
                          {inc ? `${inc.category}${inc.subCategory ? ` · ${inc.subCategory}` : ""}` : "—"}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "#ffffff" }}>{inc?.domain || "—"}</td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "#ffffff" }}>
                          {inc?.socialProfileInfo.numberOfFollowers ?? r.followers ?? emptyLabel(r, r.platform, "followers")}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "#ffffff" }}>
                          {inc?.socialProfileInfo.location || r.location || emptyLabel(r, r.platform, "location")}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "#ffffff", whiteSpace: "nowrap" }}>
                          {inc?.socialProfileInfo.lastPostDate || r.last_post_date || emptyLabel(r, r.platform, "last_post_date")}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <button
                            type="button"
                            disabled={savingId === r.id}
                            onClick={(e) => { e.stopPropagation(); saveProfileField(r.id, "username_match", !usernameMatchOf(r)); }}
                            style={{
                              cursor: savingId === r.id ? "default" : "pointer",
                              opacity: savingId === r.id ? 0.6 : 1,
                              background: usernameMatchOf(r) ? "var(--success, #10B981)" : "rgba(156, 163, 175, 0.2)",
                              color: usernameMatchOf(r) ? "#fff" : "#ffffff",
                              border: "1px solid " + (usernameMatchOf(r) ? "transparent" : "var(--border-color)"),
                              padding: "4px 10px",
                              borderRadius: "14px",
                              fontSize: "12px",
                              fontWeight: usernameMatchOf(r) ? 600 : 400,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              transition: "all 0.15s ease",
                            }}
                            title={
                              savingId === r.id
                                ? "Saving…"
                                : "Click anywhere to instantly toggle Username Match"
                            }
                          >
                            {usernameMatchOf(r) ? "✓ Match" : "+ Match"}
                          </button>
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <button
                            type="button"
                            disabled={savingId === r.id}
                            onClick={(e) => { e.stopPropagation(); saveProfileField(r.id, "logo_match", !logoMatchOf(r)); }}
                            style={{
                              cursor: savingId === r.id ? "default" : "pointer",
                              opacity: savingId === r.id ? 0.6 : 1,
                              background: logoMatchOf(r) ? "var(--success, #10B981)" : "rgba(156, 163, 175, 0.2)",
                              color: logoMatchOf(r) ? "#fff" : "#ffffff",
                              border: "1px solid " + (logoMatchOf(r) ? "transparent" : "var(--border-color)"),
                              padding: "4px 10px",
                              borderRadius: "14px",
                              fontSize: "12px",
                              fontWeight: logoMatchOf(r) ? 600 : 400,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              transition: "all 0.15s ease",
                            }}
                            title={
                              savingId === r.id
                                ? "Saving…"
                                : "Click anywhere to instantly toggle Logo Match"
                            }
                          >
                            {logoMatchOf(r) ? "✓ Match" : "+ Match"}
                          </button>
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          {/* Three states, not two. `null` means no last-post
                              date was available to judge by (Telegram never
                              exposes one; Instagram often doesn't; a
                              cut-short run never got one), rendering that
                              as "inactive" states a fact about a profile
                              nobody checked. */}
                          {inc?.socialProfileInfo.isActive === null ||
                          inc?.socialProfileInfo.isActive === undefined ? (
                            <span
                              style={{ color: "#ffffff", opacity: 0.8, fontStyle: "italic" }}
                              title={
                                analysisWasBlocked(r)
                                  ? "Analysis could not read this profile, so activity is unknown"
                                  : "No last-post date available for this profile, so activity is unknown"
                              }
                            >
                              ? unknown
                            </span>
                          ) : (
                            <span style={{ color: inc.socialProfileInfo.isActive ? "var(--success)" : "#ffffff" }}>
                              {inc.socialProfileInfo.isActive ? "● active" : "○ inactive"}
                            </span>
                          )}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "#ffffff", whiteSpace: "nowrap" }}>{inc?.date || "—"}</td>
                      )}
                      {isAnalysisView && (
                        <td style={{ textAlign: "center", padding: "8px 4px" }}>
                          {(() => {
                            const risk = getRiskBadgeDetails(inc?.riskRating);
                            return (
                              <span
                                className="risk-score-circle"
                                title={`Risk Score: ${risk.score}/10 (${risk.label})`}
                                style={{
                                  background: risk.color,
                                  color: "#ffffff",
                                  width: "24px",
                                  height: "24px",
                                  borderRadius: "50%",
                                  display: "inline-flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  fontSize: "11px",
                                  fontWeight: 800,
                                  boxShadow: `0 2px 8px ${risk.color}66`,
                                  margin: "0 auto",
                                }}
                              >
                                {risk.score}
                              </span>
                            );
                          })()}
                        </td>
                      )}
                      {!isAnalysisView && (
                        <td>
                          <span className="status-chip on" style={{ cursor: "default" }}>
                            {r.status}
                          </span>
                          {r.status === "pending" && changeSummary(r.changes) && (
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); setDiffProfile(r); }}
                              style={{
                                fontSize: "10px", color: "var(--warn-yellow, #FDB71B)", marginTop: "3px",
                                background: "rgba(253, 183, 27, 0.12)", border: "1px solid rgba(253, 183, 27, 0.3)",
                                borderRadius: "4px", padding: "2px 6px", cursor: "pointer", display: "inline-flex",
                                alignItems: "center", gap: "4px", whiteSpace: "nowrap", maxWidth: "180px",
                                overflow: "hidden", textOverflow: "ellipsis",
                              }}
                              title="Click to view full comparison of changes"
                            >
                              🔄 {changeSummary(r.changes)}
                            </button>
                          )}
                        </td>
                      )}
                      <td className="core_table-actions-cell" style={{ whiteSpace: "nowrap" }}>
                        <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "nowrap", whiteSpace: "nowrap" }}>
                          {isAnalysisView && inc && (
                            <button
                              onClick={() => setEditingId(r.id)}
                              title="Edit every incident field (OrgId, Domain, Description, Location, Followers, Last Post, and more)"
                              className="table-btn-edit"
                              style={{
                                background: "linear-gradient(135deg, rgba(0, 229, 255, 0.15), rgba(0, 184, 212, 0.05))",
                                color: "var(--cyan, #00E5FF)",
                                border: "1px solid rgba(0, 229, 255, 0.4)",
                                borderRadius: "8px",
                                padding: "6px 12px",
                                fontSize: "12px",
                                fontWeight: 600,
                                letterSpacing: "0.3px",
                                cursor: "pointer",
                                boxShadow: "0 2px 6px rgba(0, 229, 255, 0.12)",
                                transition: "all 0.2s ease",
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "5px",
                              }}
                            >
                              <span>✏️</span> Edit
                            </button>
                          )}
                          {/* A profile only reaches analysis after already being
                              validated once in discovery, re-showing "Validate"
                              there was a redundant, confusing leftover of the
                              discovery-phase workflow. Analysis's own primary
                              positive action is Publish (below), not a second
                              approval step; Validate stays discovery-only. */}
                          {!isAnalysisView && r.status !== "approved" && (
                            <button
                              disabled={savingId === r.id}
                              onClick={() => decide(r.id, "approved")}
                              style={{ marginRight: "4px", background: "rgba(154,80,233,0.12)", color: "var(--cyan-bright)", border: "1px solid rgba(154,80,233,0.3)", borderRadius: "6px", padding: "4px 8px", cursor: "pointer" }}
                            >
                              ✅ Validate
                            </button>
                          )}
                          {isHeld && (
                            <button
                              // A run that never read the profile produced no
                              // finding, and the server refuses to publish it
                              // (409). Disabling here turns that into an
                              // explained non-action rather than a click that
                              // pops an error toast.
                              disabled={savingId === r.id || analysisWasBlocked(r)}
                              onClick={() => publish(r.id)}
                              title={
                                analysisWasBlocked(r)
                                  ? `Cannot publish: analysis never read this profile (${r.analysis_status}). Re-run analysis once the session is healthy.`
                                  : "Publish this finding as a client-facing incident"
                              }
                              className="table-btn-publish"
                              style={{
                                background: "linear-gradient(135deg, rgba(136, 56, 221, 0.25), rgba(0, 229, 255, 0.2))",
                                color: "#fff",
                                border: "1px solid rgba(0, 229, 255, 0.6)",
                                borderRadius: "8px",
                                padding: "6px 14px",
                                fontSize: "12px",
                                fontWeight: 700,
                                letterSpacing: "0.4px",
                                cursor: "pointer",
                                boxShadow: "0 0 12px rgba(0, 229, 255, 0.25), inset 0 1px 0 rgba(255,255,255,0.2)",
                                transition: "all 0.2s ease",
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "5px",
                                opacity: analysisWasBlocked(r) ? 0.4 : 1,
                              }}
                            >
                              <span>📢</span> Publish
                            </button>
                          )}
                          {r.status !== "rejected" && (
                            <button
                              disabled={savingId === r.id}
                              onClick={() => decide(r.id, "rejected")}
                              className={isAnalysisView ? "table-btn-reject" : undefined}
                              style={
                                isAnalysisView
                                  ? {
                                      background: "linear-gradient(135deg, rgba(244, 63, 94, 0.15), rgba(190, 24, 93, 0.05))",
                                      color: "rgb(251, 113, 133)",
                                      border: "1px solid rgba(244, 63, 94, 0.35)",
                                      borderRadius: "8px",
                                      padding: "6px 12px",
                                      fontSize: "12px",
                                      fontWeight: 600,
                                      letterSpacing: "0.3px",
                                      cursor: "pointer",
                                      boxShadow: "0 2px 6px rgba(244, 63, 94, 0.12)",
                                      transition: "all 0.2s ease",
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "5px",
                                    }
                                  : { marginRight: "4px", background: "rgba(192,132,252,0.1)", color: "#c084fc", border: "1px solid rgba(192,132,252,0.3)", borderRadius: "6px", padding: "4px 8px", cursor: "pointer" }
                              }
                            >
                              <span>✕</span> Reject
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    );
                  })
                )}
                </tbody>
              </table>
              </div>

              {splitViewOpen && displayed.length > 0 && (
                <LiveInspectionPane
                  profile={focusedIndex >= 0 && focusedIndex < displayed.length ? displayed[focusedIndex] : displayed[0]}
                  isAnalysisView={isAnalysisView}
                  onValidate={validate}
                  onReject={(id) => decide(id, "rejected")}
                  onEdit={(id) => setEditingId(id)}
                  onClose={toggleSplitView}
                />
              )}
            </div>
          )}

          {!loading && total > 0 && (
            <div style={{ display: "flex", justifyContent: "center", gap: "6px", alignItems: "center", marginTop: "16px", flexWrap: "wrap" }}>
              <label style={{ fontSize: "11px", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: "5px" }}>
                Show
                <select
                  value={pageSize}
                  onChange={(e) => { setOffset(0); setPageSize(Number(e.target.value)); }}
                  className="select-filter"
                  style={{ padding: "4px 6px" }}
                  title="How many results to load per page"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                per page
              </label>
              {total > pageSize && (
                <>
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(0)}
                    className="btn-cyber-primary"
                    title="First page"
                    style={{ width: "auto", padding: "6px 10px", marginTop: 0 }}
                  >
                    ⏮
                  </button>
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - pageSize * 10))}
                    className="btn-cyber-primary"
                    title="Back 10 pages"
                    style={{ width: "auto", padding: "6px 10px", marginTop: 0 }}
                  >
                    −10
                  </button>
                  <button
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - pageSize))}
                    className="btn-cyber-primary"
                    style={{ width: "auto", padding: "6px 12px", marginTop: 0 }}
                  >
                    ← Prev
                  </button>
                  <span style={{ fontSize: "12px", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: "6px" }}>
                    Page <PageJumpInput currentPage={currentPage} pageCount={pageCount} onJump={(p) => setOffset((p - 1) * pageSize)} /> of{" "}
                    {pageCount} · {total} total
                  </span>
                  <button
                    disabled={currentPage >= pageCount}
                    onClick={() => setOffset(offset + pageSize)}
                    className="btn-cyber-primary"
                    style={{ width: "auto", padding: "6px 12px", marginTop: 0 }}
                  >
                    Next →
                  </button>
                  <button
                    disabled={currentPage >= pageCount}
                    onClick={() => setOffset(Math.min((pageCount - 1) * pageSize, offset + pageSize * 10))}
                    className="btn-cyber-primary"
                    title="Forward 10 pages"
                    style={{ width: "auto", padding: "6px 10px", marginTop: 0 }}
                  >
                    +10
                  </button>
                  <button
                    disabled={currentPage >= pageCount}
                    onClick={() => setOffset((pageCount - 1) * pageSize)}
                    className="btn-cyber-primary"
                    title="Last page"
                    style={{ width: "auto", padding: "6px 10px", marginTop: 0 }}
                  >
                    ⏭
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Full-field edit drawer, replaces having all 18 incident fields
          permanently inline-editable in the table. Reuses IncidentEditPanel
          unchanged (already built for the card view) so there's exactly one
          implementation of "edit an incident field", not a second copy. */}
      {editingId && (() => {
        const editing = displayed.find((r) => r.id === editingId);
        if (!editing) return null;
        const isSaving = savingId === editing.id;
        const isUnpublished = isAnalysisView && editing.published === false;
        return (
          <div
            role="dialog"
            aria-modal="true"
            onClick={() => setEditingId(null)}
            style={{
              position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
              display: "flex", alignItems: "flex-start", justifyContent: "center",
              padding: "40px 16px", zIndex: 1000, overflowY: "auto",
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              className="dashboard-card-box"
              style={{ width: "min(640px, 100%)", background: "var(--bg-card)" }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "10px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <div style={{ fontSize: "14px", fontWeight: 700 }}>
                    ✏️ Edit incident — {editing.incident?.title || editing.profile_name || editing.username}
                  </div>
                  {isSaving ? (
                    <span className="drawer-save-indicator saving">⏳ Saving…</span>
                  ) : (
                    <span className="drawer-save-indicator saved">● All changes saved</span>
                  )}
                </div>
                <button
                  onClick={() => setEditingId(null)}
                  style={{ background: "transparent", border: "none", color: "var(--text-muted)", fontSize: "16px", cursor: "pointer" }}
                  title="Close"
                >
                  ✕
                </button>
              </div>
              <IncidentEditPanel r={editing} onSave={(path, value) => saveIncidentField(editing.id, path, value)} drkOptions={drkOptions} onToggleMatch={(field, val) => saveProfileField(editing.id, field, val)} />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "16px", paddingTop: "12px", borderTop: "1px solid var(--border-subtle)" }}>
                <div>
                  {isUnpublished && (
                    <button
                      type="button"
                      disabled={isSaving || analysisWasBlocked(editing)}
                      onClick={async () => {
                        await publish(editing.id);
                        toast.success("Incident published successfully!", { icon: "🚀" });
                        setEditingId(null);
                      }}
                      className="table-btn-publish"
                      style={{
                        background: "linear-gradient(135deg, rgba(136, 56, 221, 0.25), rgba(0, 229, 255, 0.2))",
                        color: "#fff",
                        border: "1px solid rgba(0, 229, 255, 0.6)",
                        borderRadius: "8px",
                        padding: "7px 16px",
                        fontSize: "13px",
                        fontWeight: 700,
                        cursor: "pointer",
                        boxShadow: "0 0 12px rgba(0, 229, 255, 0.25)",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "6px",
                      }}
                    >
                      <span>🚀</span> Publish Finding
                    </button>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setEditingId(null)}
                  className="btn-cyber-primary"
                  style={{ width: "auto", padding: "7px 18px", fontSize: "12px", background: "var(--bg-surface)", color: "var(--text-main)", border: "1px solid var(--border-color)" }}
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {isAnalysisView && manualUrlsOpen && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(8,15,30,0.75)",
          backdropFilter: "blur(8px)", zIndex: 9999, display: "flex",
          alignItems: "center", justifyContent: "center", padding: "20px"
        }}>
          <div style={{
            background: "var(--bg-surface)", border: "1px solid var(--border-color)",
            borderRadius: "12px", width: "100%", maxWidth: "620px", padding: "24px"
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <h3 style={{ margin: 0, fontSize: "17px", fontWeight: 700, color: "var(--text-primary)" }}>
                🔗 Add profile URL(s) manually
              </h3>
              <button onClick={() => setManualUrlsOpen(false)} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "16px", fontWeight: 700 }}>✕</button>
            </div>

            {/* Sliding Tabs */}
            <div className="manual-url-tab-rail">
              <div className={`manual-url-tab-slider ${manualUrlTab === "domain" ? "domain" : ""}`} />
              <button
                type="button"
                className={`manual-url-tab-btn ${manualUrlTab === "individual" ? "active" : ""}`}
                onClick={() => setManualUrlTab("individual")}
              >
                👤 Executive URLs {splitUrls(manualIndividualUrlsText).length > 0 && `(${splitUrls(manualIndividualUrlsText).length})`}
              </button>
              <button
                type="button"
                className={`manual-url-tab-btn ${manualUrlTab === "domain" ? "active" : ""}`}
                onClick={() => setManualUrlTab("domain")}
              >
                🏷️ Domain URLs {splitUrls(manualDomainUrlsText).length > 0 && `(${splitUrls(manualDomainUrlsText).length})`}
              </button>
            </div>

            <div style={{ fontSize: "11px", color: "var(--text-dim)", marginBottom: "12px" }}>
              {manualUrlTab === "individual"
                ? "Enter executive/individual profile URLs (one per line or comma-separated). Automatically classified as Executive impersonation."
                : "Enter brand/domain profile URLs (one per line or comma-separated). Automatically classified as Domain impersonation."}
            </div>

            {manualUrlTab === "individual" ? (
              <textarea
                className="input-filter"
                style={{ width: "100%", minHeight: "160px", fontFamily: "var(--font-mono)", fontSize: "12px" }}
                placeholder="https://x.com/exec-handle&#10;https://www.instagram.com/exec-handle"
                value={manualIndividualUrlsText}
                onChange={(e) => setManualIndividualUrlsText(e.target.value)}
                disabled={manualUrlsBusy}
              />
            ) : (
              <textarea
                className="input-filter"
                style={{ width: "100%", minHeight: "160px", fontFamily: "var(--font-mono)", fontSize: "12px" }}
                placeholder="https://www.facebook.com/profile.php?id=...&#10;https://www.instagram.com/brand-handle"
                value={manualDomainUrlsText}
                onChange={(e) => setManualDomainUrlsText(e.target.value)}
                disabled={manualUrlsBusy}
              />
            )}

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "16px" }}>
              <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>
                Total: {splitUrls(manualIndividualUrlsText).length + splitUrls(manualDomainUrlsText).length} URL(s) to add
              </span>
              <button
                className="btn-cyber-primary"
                style={{ width: "auto", padding: "9px 20px", fontSize: "13px" }}
                onClick={submitManualUrls}
                disabled={
                  manualUrlsBusy ||
                  (!manualIndividualUrlsText.trim() && !manualDomainUrlsText.trim()) ||
                  !clientId
                }
              >
                {manualUrlsBusy ? "Adding…" : `➕ Add & Analyse (${splitUrls(manualIndividualUrlsText).length + splitUrls(manualDomainUrlsText).length})`}
              </button>
            </div>
          </div>
        </div>
      )}

      {copyDataCache !== null && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(8,15,30,0.75)",
          backdropFilter: "blur(8px)", zIndex: 9999, display: "flex",
          alignItems: "center", justifyContent: "center", padding: "20px"
        }}>
          <div style={{
            background: "var(--bg-surface)", border: "1px solid var(--border-color)",
            borderRadius: "12px", width: "100%", maxWidth: "400px", padding: "24px",
            textAlign: "center"
          }}>
            <h3 style={{ margin: "0 0 10px 0", fontSize: "17px", fontWeight: 700, color: "var(--text-primary)" }}>
              Data Ready
            </h3>
            <p style={{ fontSize: "12px", color: "var(--text-dim)", marginBottom: "20px" }}>
              The full table has been fetched. Please click below to copy it to your clipboard.
            </p>
            <div style={{ display: "flex", gap: "10px", justifyContent: "center" }}>
              <button
                className="btn-cyber-primary"
                onClick={() => {
                  navigator.clipboard.writeText(copyDataCache).catch(() => {
                    const el = document.createElement("textarea");
                    el.value = copyDataCache;
                    document.body.appendChild(el);
                    el.select();
                    document.execCommand("copy");
                    document.body.removeChild(el);
                  });
                  setCopyDataCache(null);
                  setCopyUrlState("copied");
                  setTimeout(() => setCopyUrlState("idle"), 2000);
                }}
              >
                📋 Copy to Clipboard
              </button>
              <button
                className="action-btn"
                onClick={() => {
                  setCopyDataCache(null);
                  setCopyUrlState("idle");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {diffProfile && (
        <VisualDiffModal profile={diffProfile} onClose={() => setDiffProfile(null)} />
      )}

      {shortcutsHelpOpen && (
        <ShortcutsModal onClose={() => setShortcutsHelpOpen(false)} />
      )}

      <button
        type="button"
        className="floating-shortcuts-btn"
        onClick={() => setShortcutsHelpOpen(true)}
        title="View keyboard shortcuts guide (?)"
      >
        <span>⌨️</span> Shortcuts <span className="kbd-badge" style={{ fontSize: "10px", padding: "1px 4px" }}>?</span>
      </button>
    </div>
  );
}
