import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import toast from "react-hot-toast";
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
  sortResults,
  type ExtraFilters,
  type ResultFilters,
} from "../services/resultsFilter";
import { toIncidentExportRows } from "../services/incidentExport";
import { download, downloadBlob, rowsToCsv, rowsToTsv } from "../utils/download";

interface Props {
  clientId: string;
  platforms: PlatformHealth[];
  discoveryRunning: boolean;
  discoveryLog: JobEvent[];
  discoveryProgress: Record<string, PlatformProgress>;
  analysisRunning: boolean;
  analysisLog: JobEvent[];
  analysisProgress: Record<string, PlatformProgress>;
  onError?: (msg: string) => void;
}

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;
const EXPORT_LIMIT = 5000;
// how long an approve/reject/validate stays undo-able before the toast
// disappears -- long enough to catch a misclick, short enough that "undo"
// never becomes a second, confusing source of truth for a profile's status
const UNDO_WINDOW_MS = 8000;

// "5s" / "2m 30s" / "1h 5m" -- never both units at zero, never blank.
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
  // never attempted at all (session wasn't ready when the sweep started) --
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
// rediscovery actually observed a real change (display name and/or logo --
// see backend's RECONSIDER_FIELDS) -- this turns that raw {field: {old,
// new}} diff into a readable one-liner, so the analyst sees WHY it's back
// instead of having to trust the queue blindly.
const CHANGE_FIELD_LABELS: Record<string, string> = { display_name: "name", has_logo: "logo" };
function changeSummary(changes?: Record<string, { old: unknown; new: unknown }> | null): string {
  if (!changes || !Object.keys(changes).length) return "";
  return Object.entries(changes)
    .map(([f, { old, new: next }]) => `${CHANGE_FIELD_LABELS[f] ?? f}: ${old ?? "—"} → ${next ?? "—"}`)
    .join("; ");
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

// Direct "jump to page N" input -- Prev/Next alone means clicking dozens of
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
// -- build_incident_doc merges these onto the computed preview, and
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
// pre-approved name instead of retyping one -- but always falls back to
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

// The full client-facing published-incident record -- this IS the analysis
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
          <IncidentCheckField
            label="Username Match" value={r.username_match ?? inc.socialProfileInfo.isSimilarName}
            path="socialProfileInfo.isSimilarName" onSave={(_, val) => onToggleMatch ? onToggleMatch("username_match", val === "true") : onSave("socialProfileInfo.isSimilarName", val)}
          />
          <IncidentCheckField
            label="Logo Match" value={r.logo_match ?? inc.socialProfileInfo.isSimilarLogo}
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
  onValidate: (id: string, logoMatch: boolean, usernameMatch: boolean) => void;
  onSaveIncidentField: (id: string, path: string, value: string) => void;
  drkOptions?: string[];
  // bulk-triage selection -- discovery cards only (see the bulk action bar
  // in the main component); undefined/no-op for an analysis card.
  selected?: boolean;
  onToggleSelected?: (id: string) => void;
  // drag-to-select: mousedown+drag across cards adds each one to the
  // selection -- see dragSelectHandlers() in the main component.
  dragHandlers?: { onMouseDown: (e: ReactMouseEvent) => void; onMouseEnter: () => void };
}

// Mirrors backend shared/models/scoring.py::NAME_THRESHOLD.
const MATCH_HIGH_THRESHOLD = 80;

// Risk-tier colour bands for the analysis card's Risk badge -- same three
// bands as the old High/Medium/Low priority badge it replaces, just keyed
// off the numeric riskRating (backend/shared/models/incident_scoring.py)
// instead of the tool's own internal priority field.
function riskBadgeColor(riskRating: string): string {
  const n = Number(riskRating);
  if (!Number.isFinite(n)) return "rgba(102,112,133,0.85)";
  if (n >= 7) return "rgba(233,80,83,0.85)";
  if (n >= 4) return "rgba(255,128,0,0.85)";
  return "rgba(102,112,133,0.85)";
}

function ProfileCard({
  r, isAnalysisView, savingId, onDecide, onValidate, onSaveIncidentField, drkOptions, selected, onToggleSelected, dragHandlers,
}: CardProps) {
  const inc = r.incident;
  const name = isAnalysisView && inc ? inc.title : r.profile_name || r.username || r.url;
  const linkUrl = isAnalysisView && inc ? inc.source : r.url;
  const isHeld = isAnalysisView && r.published === false;
  const isDiscovery = !isAnalysisView;
  const [logoMatch, setLogoMatch] = useState(r.logo_match ?? false);
  const [usernameMatch, setUsernameMatch] = useState(r.username_match ?? false);

  useEffect(() => {
    setLogoMatch(r.logo_match ?? false);
    setUsernameMatch(r.username_match ?? false);
  }, [r.id, r.logo_match, r.username_match]);

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
              background: r.name_score >= MATCH_HIGH_THRESHOLD ? "rgba(54,181,160,0.85)" : "rgba(255,128,0,0.85)",
              color: "#fff",
            }}
          >
            {r.name_score >= MATCH_HIGH_THRESHOLD ? "🎯 High Match" : "🎯 Low Match"}
          </span>
        )}
        <span className="card-badge-platform">
          <PlatformIcon platform={r.platform} size={14} />
          {r.platform}
        </span>
      </div>
      <div className="profile-card-body">
        <div className="profile-name-row">
          <a href={linkUrl} target="_blank" rel="noreferrer" className="profile-display-name" style={{ color: "var(--text-main)" }}>
            {name}
          </a>
          {r.verified && (
            <span className="verified-check" title="Verified account on this platform">
              ✓
            </span>
          )}
        </div>
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
              padding: "4px 8px", marginTop: "4px",
            }}
            title="This profile was previously rejected -- a rediscovery found a real change (not just a re-signed CDN image link), so it's back for another look"
          >
            🔄 Back for review — {changeSummary(r.changes)}
          </div>
        )}

        {isHeld && (
          <div
            style={{
              fontSize: "11px", color: "var(--purple)", background: "rgba(136,56,221,0.1)",
              border: "1px solid rgba(136,56,221,0.3)", borderRadius: "6px",
              padding: "4px 8px", marginTop: "4px",
            }}
            title="Not yet published — only visible inside this tool until explicitly published"
          >
            ⚠️ Not published
          </div>
        )}

        {(r.logo_match || r.username_match) && (
          <div className="card-detail-row">
            {r.logo_match && <span>🖼️ Logo match</span>}
            {r.username_match && <span>🔖 Username match</span>}
          </div>
        )}

        {isDiscovery && (
          <div className="card-meta-row">
            <span style={{ fontSize: "11px", color: "var(--text-dim)" }}>{r.comments || ""}</span>
          </div>
        )}

        {isDiscovery && r.status !== "approved" && r.status !== "rejected" && (
          <div className="card-validate-row" title="Tap what you visually confirmed matches the brand, then Validate">
            {/* Big tap-target toggle buttons, not tiny native checkboxes --
                this is pure local state (no network round trip), so the
                only thing standing between a fast click and it registering
                was ever the hit target itself. touch-action: manipulation
                (see styles.css) drops the mobile browser's ~300ms tap
                delay on top of that. */}
            <button
              type="button"
              className={`match-toggle${logoMatch ? " on" : ""}`}
              onClick={() => setLogoMatch((v) => !v)}
            >
              {logoMatch ? "✅" : "⬜"} Logo match
            </button>
            <button
              type="button"
              className={`match-toggle${usernameMatch ? " on" : ""}`}
              onClick={() => setUsernameMatch((v) => !v)}
            >
              {usernameMatch ? "✅" : "⬜"} Username match
            </button>
          </div>
        )}

        {isAnalysisView && (
          <IncidentEditPanel r={r} onSave={(path, value) => onSaveIncidentField(r.id, path, value)} drkOptions={drkOptions} />
        )}

        {/* This card is discovery-only -- analysis always renders as a
            table (see ResultsGrid's viewMode logic), so there's no
            analysis-phase Validate/Publish path to handle here. */}
        <div className="card-actions-row">
          {r.status !== "approved" && (
            <button
              className="btn-accept"
              disabled={savingId === r.id}
              onClick={() => onValidate(r.id, logoMatch, usernameMatch)}
              title="Validates this profile and records the logo/username match confirmation, carried through to analysis"
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
  onError,
}: Props) {
  const [platform, setPlatform] = useState(platforms[0]?.platform ?? "");
  const [phase, setPhase] = useState<"discovery" | "analysis">("discovery");
  const [status, setStatus] = useState("pending");
  const [priority, setPriority] = useState("");
  const [sortOrder, setSortOrder] = useState<"recent" | "past">("recent");
  const [keywordFilter, setKeywordFilter] = useState("");
  const [matchLevel, setMatchLevel] = useState<"" | "high" | "low">("");
  const [entityType, setEntityType] = useState<"" | "profile" | "page">("");
  const [keywordMatchType, setKeywordMatchType] = useState<"" | "individual" | "domain">("");
  const [searchQuery, setSearchQuery] = useState("");
  // Search is now a server query (it has to be, or it only ever searches
  // the page you happen to be on) -- so the raw keystroke value must not
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
  // Analysis-phase row currently open in the full-field edit drawer -- see
  // the modal near the bottom of this component's JSX. Replaces having all
  // 18 incident fields permanently inline-editable in the table (a wall of
  // 50-160px-wide <input>s the analyst had to horizontal-scroll through);
  // the table now shows only the handful of fields worth scanning at a
  // glance, and editing the rest happens in one focused place.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [copyUrlState, setCopyUrlState] = useState<"idle" | "copied" | "failed">("idle");
  const [copyDataCache, setCopyDataCache] = useState<string | null>(null);

  // Manual URL entry -- an analyst who already has a specific profile
  // link (a tip, a report, something an earlier sweep never turned up)
  // shouldn't have to invent a keyword just to get it into the pipeline.
  // See profilesApi.addManualUrls: each URL goes straight to "approved"
  // and analysis is auto-queued, same as any other approved card.
  const [manualUrlsOpen, setManualUrlsOpen] = useState(false);
  const [manualUrlsText, setManualUrlsText] = useState("");
  const [manualUrlsBusy, setManualUrlsBusy] = useState(false);

  // the brand's own real logo, shown next to a discovered profile's avatar
  // during analysis triage so "is this an impersonation" doesn't require a
  // separate tab to find the real logo to compare against. Optional --
  // clients with none set just don't get the comparison strip.
  const [clientLogoUrl, setClientLogoUrl] = useState("");
  // The client's own configured keyword lists + standalone DRK asset-name
  // options -- fetched once per client, used for the individual/domain
  // match filter (resultsFilter.ts's keywordMatchType) and the Asset Name
  // dropdown (see IncidentEditPanel), not re-fetched per profile.
  const [clientNameKeywords, setClientNameKeywords] = useState<string[]>([]);
  const [clientDomainKeywords, setClientDomainKeywords] = useState<string[]>([]);
  const [drkOptions, setDrkOptions] = useState<string[]>([]);
  useEffect(() => {
    if (!clientId) {
      setClientLogoUrl("");
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
        setClientLogoUrl(c.logo_url || "");
        setClientNameKeywords(c.name_keywords || []);
        setClientDomainKeywords(c.domain_keywords || []);
        setDrkOptions([
          ...(c.asset_name_individual_keywords || []),
          ...(c.asset_name_domain_keywords || []),
        ]);
      })
      .catch(() => {
        if (cancelled) return;
        setClientLogoUrl("");
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

  // discovery-only multi-select for bulk approve/reject -- keyed by profile
  // id so it survives a re-render/re-sort of the same underlying rows.
  // Cleared on any filter/page/client change so a selection never silently
  // carries over onto a different set of rows than the analyst was looking
  // at when they made it.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  // Re-resolving name/photo for the current selection (Facebook only) --
  // separate from bulkBusy since it can run alongside a still-open
  // selection (unlike approve/reject, it doesn't clear it) and takes much
  // longer (a real page visit per profile, not a single PATCH).
  const [resweepBusy, setResweepBusy] = useState(false);

  // Drag-to-select: mousedown on a card/row (outside its buttons/links)
  // starts a paint gesture -- every card/row the cursor then passes over
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
  // LAST wins -- even if it's the stale one -- silently reverting a card
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
          // string) -- the server-side filter works for analysis rows too,
          // it just wasn't being sent there before.
          keyword: keywordFilter || undefined,
          entity_type: !isAnalysisView && platform === "facebook" && entityType ? entityType : undefined,
          // These four used to be applied only in the browser, over
          // whatever page had been fetched, while `total` and the pager
          // still came from the unfiltered query. Filtering 500 analysis
          // rows to "High" therefore showed the High rows inside page 1 and
          // still claimed 500 results -- which reads as the tool having
          // lost data. They are now real query parameters.
          priority: isAnalysisView && priority ? priority : undefined,
          match_level: !isAnalysisView && matchLevel ? matchLevel : undefined,
          keyword_match_type: keywordMatchType || undefined,
          search: debouncedSearch || undefined,
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
    // priority / matchLevel / keywordMatchType / debouncedSearch are query
    // parameters now, so load() must re-run when any of them changes
    [clientId, platform, status, phase, keywordFilter, entityType, isAnalysisView,
     priority, matchLevel, keywordMatchType, debouncedSearch, offset, pageSize, onError],
  );

  // Any filter change invalidates the current page number: page 4 of the
  // old result set is meaningless against the new one, and leaving offset
  // where it was lands on an empty page.
  useEffect(() => {
    setOffset(0);
  }, [clientId, platform, status, phase, keywordFilter, entityType, keywordMatchType,
      priority, matchLevel, debouncedSearch, pageSize]);

  // Client-scoped filters must reset on a client switch -- a leftover
  // Individual/Domain match selection from a different client's keyword
  // lists would silently misclassify (or blank out) results here.
  useEffect(() => {
    setKeywordMatchType("");
  }, [clientId]);

  // A selection only ever makes sense against the rows the analyst was
  // looking at when they made it -- clear it whenever the underlying set
  // changes so a stale selection can't silently bulk-act on different rows.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [clientId, platform, status, phase, keywordFilter, entityType, keywordMatchType,
      priority, matchLevel, debouncedSearch, offset, pageSize]);

  // Neither Discovery nor Analysis has an "All Platforms" tab -- whenever we
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
  // WebSocket-driven view refreshed at -- this backend polls for progress
  // too now (see docs/adr/0002), so results polling matches that rhythm.
  useEffect(() => {
    if (!discoveryRunning && !analysisRunning) return;
    const interval = setInterval(() => load(false), 3000);
    return () => clearInterval(interval);
  }, [discoveryRunning, analysisRunning, load]);

  const isFacebook = !isAnalysisView && platform === "facebook";
  // status and priority each only have a picker UI in one view (status:
  // Discovery, priority: Analysis) -- both must be blanked in the other
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
    // with -- using the raw input here would blank the grid mid-keystroke
    // while the request for those characters is still in flight
    searchQuery: debouncedSearch,
    matchLevel: !isAnalysisView ? matchLevel : "",
    entityType: !isAnalysisView && isFacebook ? entityType : "",
    keywordMatchType,
  };
  // The server has already applied all of these before pagination (see
  // load()); this pass only reconciles rows whose local state is ahead of
  // the server -- an optimistic status change not yet PATCHed, or rows
  // still in memory from a live-poll refresh.
  const displayed = useMemo(
    () =>
      sortResults(
        filterResults(profiles, filters, extra, isAnalysisView ? "" : platform, clientKeywordSets),
        sortOrder,
        phase,
        keywordFilter,
        status,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [profiles, status, priority, phase, keywordFilter, matchLevel, entityType, keywordMatchType, clientKeywordSets, debouncedSearch, sortOrder, platform, isAnalysisView],
  );

  const decide = async (id: string, next: Status) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => {
      const updated = rows.map((r) => (r.id === id ? { ...r, status: next } : r));
      // `status` is the Discovery-tab status filter -- it has no meaning
      // (and no UI to clear it) in Analysis view, where it can still hold
      // a leftover value from before the tab switch. Applying it there
      // would prune every other currently-loaded analysis row out of local
      // state on every approve/reject, not just the one just decided.
      return !isAnalysisView && status ? updated.filter((r) => r.status === status) : updated;
    });
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: next });
      // Automatically pull next items from page 2 into page 1 without needing manual navigation
      await load(false);
    } catch (e) {
      if (prev) setProfiles((rows) => [...rows.filter((r) => r.id !== prev.id), prev]);
      onError?.((e as Error).message);
    } finally {
      setSavingId(null);
    }
  };

  // Validates the profile the same way `decide(id, "approved")` does, but
  // also records the analyst's own visual confirmation of a logo/username
  // impersonation match -- saved to the DB and carried through unchanged
  // onto the analysis-phase record (see backend/services/profile_service.py).
  const validate = async (id: string, logoMatch: boolean, usernameMatch: boolean) => {
    const prev = profiles.find((r) => r.id === id);
    setProfiles((rows) => {
      const updated = rows.map((r) =>
        r.id === id ? { ...r, status: "approved" as Status, logo_match: logoMatch, username_match: usernameMatch } : r,
      );
      // see decide()'s identical guard above -- `status` is Discovery-only
      return !isAnalysisView && status ? updated.filter((r) => r.status === status) : updated;
    });
    setSavingId(id);
    try {
      await profilesApi.patchProfile(id, { status: "approved", logo_match: logoMatch, username_match: usernameMatch });
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

  // "Did we actually check everything?" -- see GET /profiles/coverage.
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

  const publishAll = async () => {
    if (!clientId) return;
    setPublishingAll(true);
    try {
      await profilesApi.publishAllProfiles(clientId, platform || undefined);
      toast.success("All incidents successfully published", { icon: "✅" });
      await load(false);
    } catch (e) {
      toast.error((e as Error).message);
      onError?.((e as Error).message);
    } finally {
      setPublishingAll(false);
    }
  };

  // Applies a dotted-path edit (e.g. "socialProfileInfo.location") to a
  // profile's own `incident` preview object, immutably -- the same shape
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
  // fires -- a save is only a fire-and-forget onBlur/onChange, so a very
  // fast "edit a field, then immediately click Excel" could otherwise
  // race ahead of its own PATCH. This set tracks every in-flight
  // incident-field save; handleExport awaits all of them first.
  const pendingIncidentSaves = useRef<Set<Promise<void>>>(new Set());

  const saveIncidentField = (id: string, path: string, rawValue: string): void => {
    const prev = profiles.find((r) => r.id === id);
    // booleans/numbers travel through the DOM as strings -- coerce back
    // before both the optimistic update and the PATCH payload
    const value: unknown =
      rawValue === "true" || rawValue === "false" ? rawValue === "true"
      : path === "socialProfileInfo.numberOfFollowers" ? (rawValue === "" ? null : Number(rawValue))
      : rawValue;
    setProfiles((rows) => rows.map((r) => (r.id === id ? withIncidentPath(r, path, value) : r)));
    const task = (async () => {
      try {
        await profilesApi.patchProfile(id, { incident_overrides: { [path]: value } });
      } catch (e) {
        if (prev) setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
        onError?.((e as Error).message);
      }
    })();
    pendingIncidentSaves.current.add(task);
    task.finally(() => pendingIncidentSaves.current.delete(task));
  };

  // Editing the RAW username_match/logo_match fields (not the incident
  // preview's cosmetic socialProfileInfo.isSimilarName/isSimilarLogo
  // overrides -- see saveIncidentField above) is what actually feeds
  // compute_incident_risk_score server-side, so the Risk badge only ever
  // changes from editing these. Recomputes the score locally right away
  // (computeIncidentRiskScorePreview mirrors the backend formula exactly)
  // instead of waiting on the PATCH round trip or the 3s live-poll, then
  // reconciles with the server's authoritative response when it lands.
  const saveProfileField = async (id: string, field: "username_match" | "logo_match", value: boolean): Promise<void> => {
    const prev = profiles.find((r) => r.id === id);
    if (!prev) return;
    const logoMatch = field === "logo_match" ? value : !!prev.logo_match;
    const usernameMatch = field === "username_match" ? value : !!prev.username_match;
    const previewScore = computeIncidentRiskScorePreview({
      logoMatch, usernameMatch, followers: prev.followers, location: prev.location, lastPostDate: prev.last_post_date,
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
    try {
      const updated = await profilesApi.patchProfile(id, { [field]: value });
      setProfiles((rows) => rows.map((r) => (r.id === id ? updated : r)));
    } catch (e) {
      setProfiles((rows) => rows.map((r) => (r.id === id ? prev : r)));
      onError?.((e as Error).message);
    }
  };

  // Analysis-only bulk apply: sets the same assetName override across every
  // selected profile in one action, reusing saveIncidentField's existing
  // optimistic-update + PATCH + rollback-on-error machinery per profile
  // rather than a new backend endpoint -- this is exactly what a single
  // card's Asset Name dropdown already does, just looped over a selection.
  const [bulkAssetNameBusy, setBulkAssetNameBusy] = useState(false);
  const bulkSetAssetName = async (assetName: string) => {
    if (!assetName || !selectedIds.size) return;
    setBulkAssetNameBusy(true);
    try {
      for (const id of selectedIds) saveIncidentField(id, "assetName", assetName);
      if (pendingIncidentSaves.current.size) await Promise.all(pendingIncidentSaves.current);
    } finally {
      setBulkAssetNameBusy(false);
    }
  };

  const handleCopyUrls = async () => {
    if (!clientId) return;
    setCopyUrlState("idle");

    const fetchBlob = async (): Promise<Blob> => {
      if (pendingIncidentSaves.current.size) {
        await Promise.all(pendingIncidentSaves.current);
      }
      const res = await profilesApi.profiles({
        client_id: clientId,
        platform: platform || undefined,
        status: !isAnalysisView && status ? status : undefined,
        keyword: keywordFilter || undefined,
        phase,
        limit: EXPORT_LIMIT,
        offset: 0,
      });
      const filtered = filterResults(res.items, filters, extra, platform, clientKeywordSets);

      if (isAnalysisView) {
        const rows = toIncidentExportRows(filtered);
        if (!rows.length) throw new Error("No analysis table data to copy.");
        return new Blob([rowsToTsv(rows)], { type: "text/plain" });
      } else {
        const targetProfiles = status ? filtered.filter((r) => r.status === status) : filtered;
        const urls = targetProfiles.map((r) => r.url).filter(Boolean);
        if (!urls.length) {
          const label = status === "approved" ? "validated" : status === "rejected" ? "rejected" : status ? status : "matching";
          throw new Error(`No ${label} profiles to copy.`);
        }
        return new Blob([urls.join("\n")], { type: "text/plain" });
      }
    };

    try {
      const blob = await fetchBlob();
      const text = await blob.text();
      try {
        await navigator.clipboard.writeText(text);
        setCopyUrlState("copied");
        setTimeout(() => setCopyUrlState("idle"), 2000);
      } catch {
        // Fallback: browser blocked async clipboard copy. Show modal to get a synchronous click.
        setCopyDataCache(text);
      }
    } catch (e) {
      onError?.((e as Error).message || "Copy failed");
      setCopyUrlState("failed");
      setTimeout(() => setCopyUrlState("idle"), 2000);
    }
  };

  // Fetches everything matching the current filters (not just this page) for
  // export -- this backend has no export endpoint, so the conversion happens
  // entirely client-side.
  // Discovery-phase export keeps the old raw-Profile field set (there's no
  // incident record before analysis); analysis-phase export always goes
  // through the same incident-row shaping as the published-incident record
  // itself (services/incidentExport.ts), so CSV/JSON/Excel and what
  // Publish actually writes never drift apart.
  const DISCOVERY_EXPORT_COLS = [
    "id", "platform", "status", "phase", "url", "profile_name", "username", "keyword",
  ] as const;

  const handleExport = async (fmt: "csv" | "json" | "xlsx") => {
    if (!clientId) return;
    setExporting(true);
    try {
      // let every incident-field edit already in flight land before
      // fetching -- otherwise a save fired moments ago could still be
      // mid-PATCH when this export's own fetch races past it
      if (pendingIncidentSaves.current.size) {
        await Promise.all(pendingIncidentSaves.current);
      }
      const res = await profilesApi.profiles({
        client_id: clientId,
        platform: platform || undefined,
        status: !isAnalysisView && status ? status : undefined,
        keyword: keywordFilter || undefined,
        phase,
        limit: EXPORT_LIMIT,
        offset: 0,
      });
      const filtered = filterResults(res.items, filters, extra, platform);
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      const rows: Record<string, unknown>[] = isAnalysisView
        ? toIncidentExportRows(filtered)
        : filtered.map((r) => Object.fromEntries(DISCOVERY_EXPORT_COLS.map((c) => [c, r[c]])));
      if (fmt === "csv") {
        download(`${clientId}-${phase}-${stamp}.csv`, rowsToCsv(rows), "text/csv");
      } else if (fmt === "xlsx") {
        // a real .xlsx binary, built server-side via openpyxl -- not the
        // old HTML-table-with-an-Excel-MIME-type trick (see download.ts's
        // git history), which loses formatting/column types and can
        // trigger an "unreadable content" security warning on open.
        const filename = `${clientId}-${phase}-${stamp}.xlsx`;
        const fileBlob = await profilesApi.exportXlsx(filename, rows);
        downloadBlob(filename, fileBlob);
      } else {
        download(`${clientId}-${phase}-${stamp}.json`, JSON.stringify(rows, null, 2), "application/json");
      }
    } catch (e) {
      onError?.((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  // `ids` defaults to the current checkbox selection (the bulk action bar);
  // page-wide Validate All/Reject All pass their own id list directly so
  // they work with nothing selected at all -- see the toolbar buttons below.
  const bulkDecide = async (next: Status, ids?: string[]) => {
    const targetIds = ids ?? [...selectedIds];
    if (!targetIds.length) return;
    setBulkBusy(true);
    try {
      const res = await profilesApi.bulkPatch(targetIds, next);
      setSelectedIds(new Set());
      if (res.failed.length) {
        toast.error(`${res.failed.length} of ${targetIds.length} profile(s) failed to update.`);
        onError?.(`${res.failed.length} of ${targetIds.length} profile(s) failed to update.`);
      } else {
        toast.success(`${targetIds.length} incidents successfully ${next === "approved" ? "validated" : "rejected"}`, { icon: next === "approved" ? "✅" : "✕" });
      }
      await load(false);
    } catch (e) {
      toast.error((e as Error).message);
      onError?.((e as Error).message);
    } finally {
      setBulkBusy(false);
    }
  };

  // Re-resolves name/photo for just the selected profiles -- no keyword
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

  const submitManualUrls = async () => {
    if (!clientId) return;
    const urls = manualUrlsText
      .split(/[\n,]+/)
      .map((u) => u.trim())
      .filter(Boolean);
    if (!urls.length) return;
    setManualUrlsBusy(true);
    try {
      const res = await profilesApi.addManualUrls(clientId, urls);
      setManualUrlsText("");
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
  // without going stale -- they close over refs, not state, on purpose.
  const selectedIdsRef = useRef<Set<string>>(selectedIds);
  useEffect(() => {
    selectedIdsRef.current = selectedIds;
  }, [selectedIds]);

  // The previous version armed drag-select on the mousedown itself, so any
  // click that so much as twitched a couple pixels onto a neighbouring
  // card -- resting a finger on a trackpad, a slightly imprecise click near
  // a card edge -- would silently sweep that neighbour into the selection
  // too, with no visible cue it had happened. Fixed with a movement
  // threshold: a plain click (mousedown+mouseup with no meaningful
  // movement) toggles just the one card it landed on; only real movement
  // starts a drag.
  //
  // The drag itself is a paint-and-erase gesture, not a one-way sweep:
  // moving forward over a not-yet-visited card selects it; backtracking
  // over a card THIS SAME DRAG already selected un-selects it, as if the
  // cursor were physically erasing the mark it just made. `dragPath` is
  // the ordered trail of cards this one continuous drag has touched --
  // re-entering any earlier point in that trail rewinds (deselects)
  // everything painted after it, however far back the retrace goes, not
  // just the immediately-previous card. Deliberately still one-directional
  // with respect to anything selected BEFORE this drag started
  // (`dragStartSelection`, snapshotted the instant the drag arms): a card
  // that was already selected coming in is never touched by this drag,
  // forward or backward -- retracing over it doesn't un-select a decision
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
        // retraced back to an earlier point in this drag's own trail --
        // erase everything painted after it (but never anything that was
        // already selected before this drag began)
        const toErase = dragPath.current.slice(idx + 1).filter((pid) => !dragStartSelection.current.has(pid));
        dragPath.current = dragPath.current.slice(0, idx + 1);
        removeSelected(toErase);
      }
      // idx === last index: re-entering the card already at the head of
      // the trail (e.g. a wobble within its own bounds) -- no-op
    },
  });

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const origin = dragOrigin.current;
      if (!origin || dragSelectActive.current) return;
      if (Math.hypot(e.clientX - origin.x, e.clientY - origin.y) < DRAG_THRESHOLD_PX) return;
      // threshold crossed -- this is a genuine drag, not a click; select
      // the card the drag started on and start painting from here
      dragSelectActive.current = true;
      document.body.style.userSelect = "none";
      dragStartSelection.current = new Set(selectedIdsRef.current);
      dragPath.current = [origin.id];
      addSelected(origin.id);
    };
    const endDrag = () => {
      // armed but never crossed the movement threshold -- a plain click,
      // toggle exactly the one card it landed on
      if (dragOrigin.current && !dragSelectActive.current) toggleSelected(dragOrigin.current.id);
      dragOrigin.current = null;
      dragSelectActive.current = false;
      dragPath.current = [];
      document.body.style.userSelect = "";
    };
    // an interrupted gesture (focus lost, tab hidden, cursor left the
    // document entirely) -- abort without guessing at single-click intent
    const abortDrag = () => {
      dragOrigin.current = null;
      dragSelectActive.current = false;
      dragPath.current = [];
      document.body.style.userSelect = "";
    };
    const onKeyDown = (e: KeyboardEvent) => {
      // fastest possible "undo that" -- clears the whole selection and any
      // in-flight drag with one keypress, no need to reach for a mouse.
      // Unconditional (no "is there anything to clear" guard) so this
      // effect never needs `selectedIds` as a dependency -- an empty-Set
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

  // "select all" only ever means "every row currently on screen" -- not
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



          {/* Platform filter rail -- view-only. Discovery/analysis on this
              backend always run across every ready platform at once, so
              there is nothing per-platform to launch from here anymore. */}
          <div className="platform-rail-grid">
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

          {/* Page-wide "clear the queue" fast path -- no selection needed at
              all. Deliberately scoped to PENDING rows on this page only:
              it decides what's still awaiting a call, it never silently
              overrides a decision already made (an already-approved or
              already-rejected row on the same page is left untouched). If
              the analyst has the Pending status chip active, `displayed`
              is already only pending rows, so this reads as "decide
              everything on screen" -- exactly the one-click-per-page
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

          {/* Bulk triage bar -- for a targeted subset instead of the whole
              page: check specific cards (or drag across them -- see
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

          {/* Analysis-phase multi-select bulk apply -- lets an analyst pick
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
            {/* Same exact-match dropdown in both views now -- this used to be
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
                onChange={(e) => setMatchLevel(e.target.value as "" | "high" | "low")}
                className="select-filter"
                title="How closely the scraped name matches the keyword that found it"
              >
                <option value="">All Match Levels</option>
                <option value="high">🎯 High Match</option>
                <option value="low">🎯 Low Match</option>
              </select>
            )}
            {!isAnalysisView && isFacebook && (
              <select
                value={entityType}
                onChange={(e) => setEntityType(e.target.value as "" | "profile" | "page")}
                className="select-filter"
                title="Facebook only distinguishes people profiles from Pages -- filter to just one"
              >
                <option value="">People + Pages</option>
                <option value="profile">👤 People Only</option>
                <option value="page">📄 Pages Only</option>
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
            {/* Card view is discovery-only -- an analysis card is the full
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
            <button className="btn-cyber-primary" style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }} onClick={() => handleExport("csv")} disabled={exporting || !clientId}>
              {exporting ? "…" : "CSV"}
            </button>
            <button className="btn-cyber-primary" style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }} onClick={() => handleExport("json")} disabled={exporting || !clientId}>
              {exporting ? "…" : "JSON"}
            </button>
            <button
              className="btn-cyber-primary"
              style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }}
              onClick={() => handleExport("xlsx")}
              disabled={exporting || !clientId}
              title={isAnalysisView ? "Export the takedown-report column layout" : "Export as an Excel-compatible spreadsheet"}
            >
              {exporting ? "…" : "Excel"}
            </button>
            <button
              className="btn-cyber-primary"
              style={{
                padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto",
                background: copyUrlState === "copied" ? "var(--success)" : copyUrlState === "failed" ? "var(--danger)" : "rgba(54, 181, 160, 0.15)",
                color: "var(--success)", border: "1px solid var(--success)",
              }}
              onClick={handleCopyUrls}
              title={
                isAnalysisView
                  ? "Copy all table data in Excel-compatible format to clipboard"
                  : status === "rejected"
                  ? "Copy rejected profile URLs to clipboard"
                  : status === "approved"
                  ? "Copy validated profile URLs to clipboard"
                  : "Copy profile URLs to clipboard"
              }
            >
              {copyUrlState === "copied"
                ? "✓ Copied"
                : copyUrlState === "failed"
                ? "✕ Failed"
                : isAnalysisView
                ? "📋 Copy Table (Excel)"
                : status === "rejected"
                ? "📋 Copy Rejected URLs"
                : status === "approved"
                ? "📋 Copy Validated URLs"
                : "📋 Copy URLs"}
            </button>
            {isAnalysisView && (
              <>
                <button
                  className="btn-cyber-primary"
                  style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto", background: "rgba(0, 229, 255, 0.15)", color: "var(--cyan)", border: "1px solid var(--cyan)" }}
                  onClick={() => setManualUrlsOpen(true)}
                >
                  🔗 Add URLs
                </button>
                <button
                  className="btn-cyber-primary"
                  style={{ padding: "7px 11px", fontSize: "11px", marginTop: 0, width: "auto" }}
                  onClick={publishAll}
                  disabled={publishingAll || !clientId}
                  title="Publish every held analysis result matching the current platform view"
                >
                  {publishingAll ? "Publishing…" : "📢 Publish All"}
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
            <div className="profile-grid-container" style={{ marginTop: "12px" }}>
              {displayed.map((r) => (
                <ProfileCard
                  key={r.id} r={r} isAnalysisView={isAnalysisView} savingId={savingId}
                  onDecide={decide} onValidate={validate}
                  onSaveIncidentField={saveIncidentField}
                  selected={selectedIds.has(r.id)} onToggleSelected={toggleSelected}
                  dragHandlers={dragSelectHandlers(r.id)}
                />
              ))}
            </div>
          )}

          {!loading && displayed.length > 0 && (isAnalysisView || viewMode === "table") && (
            <div style={{ overflowX: "auto", marginTop: "12px" }}>
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
                    {/* Trimmed from 18 always-inline-editable columns (OrgId,
                        Domain, AssetType, ThirdParty, Description, Name
                        Match, Logo Match, Location, Followers, Last Post,
                        each its own 50-160px input) down to the handful
                        worth scanning at a glance. Everything else is one
                        click away in the Edit drawer -- see the modal near
                        the bottom of this component and the ✏️ Edit
                        button below. */}
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
                    {!isAnalysisView && <th>Status</th>}
                    <th className="core_table-actions-cell">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && displayed.length === 0 ? (
                    Array.from({ length: 7 }).map((_, i) => (
                      <tr key={`skeleton-${i}`}>
                        <td colSpan={15} style={{ padding: '8px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
                          <div className="skeleton-row" style={{ width: '100%', opacity: Math.max(0.1, 1 - (i * 0.15)) }} />
                        </td>
                      </tr>
                    ))
                  ) : displayed.length === 0 ? (
                    <tr>
                      <td colSpan={15} style={{ textAlign: "center", padding: "40px", color: "var(--text-dim)" }}>
                        No profiles match the current filters.
                      </td>
                    </tr>
                  ) : (
                    displayed.map((r) => {
                      const isHeld = isAnalysisView && r.published === false;
                      const inc = r.incident;
                      return (
                      <tr
                        key={r.id}
                      {...dragSelectHandlers(r.id)}
                      style={selectedIds.has(r.id) ? { outline: "2px solid var(--cyan)", outlineOffset: "-2px" } : undefined}
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
                          {/* Side-by-side against the brand's own real logo
                              (set on the client config form) -- previously
                              the analyst had to open a separate tab to find
                              the real logo to compare against. */}
                          {isAnalysisView && clientLogoUrl && (
                            <>
                              <span style={{ fontSize: "11px", fontWeight: "bold", color: "var(--text-dim)" }}>vs</span>
                              <img
                                src={clientLogoUrl}
                                alt="Reference brand logo"
                                title="This client's real logo, for comparison"
                                style={{ width: 52, height: 52, borderRadius: "50%", objectFit: "cover", flexShrink: 0, border: "2px solid rgba(136, 56, 221, 0.4)", boxShadow: "0 2px 10px rgba(136, 56, 221, 0.15)" }}
                                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                              />
                            </>
                          )}
                        </div>
                      </td>
                      <td style={{ maxWidth: "220px" }}>
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
                      </td>
                      <td><PlatformIcon platform={r.platform} size={16} /></td>
                      {/* Read-only glance columns -- everything else (OrgId,
                          Domain, AssetType, ThirdParty, Description, Name
                          Match, Logo Match, Location, Followers, Last Post)
                          moved into the Edit drawer below, opened via the
                          ✏️ Edit action in this row's Actions cell. */}
                      {isAnalysisView && (
                        <td title={inc?.assetName ?? ""} style={{ maxWidth: "140px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {inc?.assetName || "—"}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          {inc && (
                            <span
                              style={{
                                background: riskBadgeColor(inc.riskRating), color: "#fff",
                                padding: "2px 8px", borderRadius: "999px", fontSize: "11px", fontWeight: 700,
                              }}
                            >
                              {inc.riskRating}
                            </span>
                          )}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                          {inc ? `${inc.category}${inc.subCategory ? ` · ${inc.subCategory}` : ""}` : "—"}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "var(--text-muted)" }}>{inc?.domain || "—"}</td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                          {inc?.socialProfileInfo.numberOfFollowers ?? r.followers ?? emptyLabel(r, r.platform, "followers")}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                          {inc?.socialProfileInfo.location || r.location || emptyLabel(r, r.platform, "location")}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                          {inc?.socialProfileInfo.lastPostDate || r.last_post_date || emptyLabel(r, r.platform, "last_post_date")}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); saveProfileField(r.id, "username_match", !r.username_match); }}
                            style={{
                              cursor: "pointer",
                              background: r.username_match ? "var(--success, #10B981)" : "rgba(156, 163, 175, 0.2)",
                              color: r.username_match ? "#fff" : "var(--text-dim)",
                              border: "1px solid " + (r.username_match ? "transparent" : "var(--border-color)"),
                              padding: "4px 10px",
                              borderRadius: "14px",
                              fontSize: "12px",
                              fontWeight: r.username_match ? 600 : 400,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              transition: "all 0.15s ease",
                            }}
                            title="Click anywhere to instantly toggle Username Match"
                          >
                            {r.username_match ? "✓ Match" : "+ Match"}
                          </button>
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); saveProfileField(r.id, "logo_match", !r.logo_match); }}
                            style={{
                              cursor: "pointer",
                              background: r.logo_match ? "var(--success, #10B981)" : "rgba(156, 163, 175, 0.2)",
                              color: r.logo_match ? "#fff" : "var(--text-dim)",
                              border: "1px solid " + (r.logo_match ? "transparent" : "var(--border-color)"),
                              padding: "4px 10px",
                              borderRadius: "14px",
                              fontSize: "12px",
                              fontWeight: r.logo_match ? 600 : 400,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              transition: "all 0.15s ease",
                            }}
                            title="Click anywhere to instantly toggle Logo Match"
                          >
                            {r.logo_match ? "✓ Match" : "+ Match"}
                          </button>
                        </td>
                      )}
                      {isAnalysisView && (
                        <td>
                          {/* Three states, not two. `null` means no last-post
                              date was available to judge by (Telegram never
                              exposes one; Instagram often doesn't; a
                              cut-short run never got one) -- rendering that
                              as "inactive" states a fact about a profile
                              nobody checked. */}
                          {inc?.socialProfileInfo.isActive === null ||
                          inc?.socialProfileInfo.isActive === undefined ? (
                            <span
                              style={{ color: "var(--text-dim)", fontStyle: "italic" }}
                              title={
                                analysisWasBlocked(r)
                                  ? "Analysis could not read this profile, so activity is unknown"
                                  : "No last-post date available for this profile, so activity is unknown"
                              }
                            >
                              ? unknown
                            </span>
                          ) : (
                            <span style={{ color: inc.socialProfileInfo.isActive ? "var(--success)" : "var(--text-dim)" }}>
                              {inc.socialProfileInfo.isActive ? "● active" : "○ inactive"}
                            </span>
                          )}
                        </td>
                      )}
                      {isAnalysisView && (
                        <td style={{ fontSize: "11px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{inc?.date || "—"}</td>
                      )}
                      {!isAnalysisView && (
                        <td>
                          <span className="status-chip on" style={{ cursor: "default" }}>
                            {r.status}
                          </span>
                          {r.status === "pending" && changeSummary(r.changes) && (
                            <div
                              style={{ fontSize: "10px", color: "var(--warn-yellow, #FDB71B)", marginTop: "3px", whiteSpace: "nowrap", maxWidth: "160px", overflow: "hidden", textOverflow: "ellipsis" }}
                              title={`Previously rejected -- a rediscovery found a real change: ${changeSummary(r.changes)}`}
                            >
                              🔄 {changeSummary(r.changes)}
                            </div>
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
                              validated once in discovery -- re-showing "Validate"
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

      {/* Full-field edit drawer -- replaces having all 18 incident fields
          permanently inline-editable in the table. Reuses IncidentEditPanel
          unchanged (already built for the card view) so there's exactly one
          implementation of "edit an incident field", not a second copy. */}
      {editingId && (() => {
        const editing = displayed.find((r) => r.id === editingId);
        if (!editing) return null;
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
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
                <div style={{ fontSize: "13px", fontWeight: 700 }}>
                  ✏️ Edit incident — {editing.incident?.title || editing.profile_name || editing.username}
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
            borderRadius: "12px", width: "100%", maxWidth: "500px", padding: "24px"
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <h3 style={{ margin: 0, fontSize: "17px", fontWeight: 700, color: "var(--text-primary)" }}>
                🔗 Add profile URL(s) manually
              </h3>
              <button onClick={() => setManualUrlsOpen(false)} style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "16px", fontWeight: 700 }}>✕</button>
            </div>
            <div style={{ fontSize: "11px", color: "var(--text-dim)", marginBottom: "16px" }}>
              One per line (or comma-separated) -- Facebook, X/Twitter, Instagram, YouTube, Telegram.
              Each is created as approved and sent straight to analysis, no keyword search needed.
            </div>
            <textarea
              className="input-filter"
              style={{ width: "100%", minHeight: "150px", fontFamily: "var(--font-mono)", fontSize: "12px", marginBottom: "16px" }}
              placeholder="https://www.facebook.com/profile.php?id=...&#10;https://x.com/handle"
              value={manualUrlsText}
              onChange={(e) => setManualUrlsText(e.target.value)}
              disabled={manualUrlsBusy}
            />
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                className="btn-cyber-primary"
                style={{ width: "auto", padding: "9px 20px", fontSize: "13px" }}
                onClick={submitManualUrls}
                disabled={manualUrlsBusy || !manualUrlsText.trim() || !clientId}
              >
                {manualUrlsBusy ? "Adding…" : "➕ Add & Analyse"}
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
    </div>
  );
}
