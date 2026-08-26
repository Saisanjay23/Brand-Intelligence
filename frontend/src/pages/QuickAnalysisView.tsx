import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  quickAnalysisApi,
  type QuickAnalysisItemData,
  type QuickAnalysisJobResponse,
} from "../api/quickAnalysisApi";
import {
  ActivityWaveIcon,
  DownloadIcon,
  QuickAnalysisNavIcon,
  SearchIcon,
  StopIcon,
  VerifiedBadgeIcon,
} from "../components/AppIcons";
import { PlatformIcon } from "../components/PlatformIcon";
import { download, downloadBlob, rowsToCsv, rowsToTsv } from "../utils/download";

const SAMPLE_URLS = [
  "https://www.facebook.com/zuck",
  "https://www.instagram.com/instagram",
  "https://twitter.com/elonmusk",
  "https://www.youtube.com/@Google",
  "https://t.me/durov",
  "https://www.tiktok.com/@tiktok",
].join("\n");

const EditableCell = ({ value, onChange, placeholder = "—" }: { value: string, onChange: (v: string) => void, placeholder?: string }) => {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        background: "transparent",
        border: "1px solid transparent",
        color: "inherit",
        width: "100%",
        minWidth: "60px",
        padding: "4px 6px",
        borderRadius: "4px",
        outline: "none",
        transition: "all 0.2s",
        fontSize: "inherit",
        fontFamily: "inherit"
      }}
      onFocus={(e) => {
        e.target.style.background = "rgba(0, 0, 0, 0.2)";
        e.target.style.border = "1px solid var(--border-color)";
      }}
      onBlur={(e) => {
        e.target.style.background = "transparent";
        e.target.style.border = "1px solid transparent";
      }}
    />
  );
};

const ToggleCell = ({ value, onChange }: { value: string, onChange: (v: string) => void }) => {
  const isYes = value === "Yes";
  const isNo = value === "No";
  
  return (
    <button
      type="button"
      onClick={() => onChange(isYes ? "No" : "Yes")}
      style={{
        background: isYes ? "rgba(18, 183, 106, 0.15)" : isNo ? "rgba(233, 80, 83, 0.15)" : "rgba(255, 255, 255, 0.05)",
        border: `1px solid ${isYes ? "rgba(18, 183, 106, 0.4)" : isNo ? "rgba(233, 80, 83, 0.4)" : "rgba(255, 255, 255, 0.1)"}`,
        color: isYes ? "#12B76A" : isNo ? "#E95053" : "var(--text-muted)",
        padding: "4px 10px",
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: 600,
        cursor: "pointer",
        minWidth: "48px",
        transition: "all 0.2s"
      }}
    >
      {isYes ? "Yes" : isNo ? "No" : value || "—"}
    </button>
  );
};

export function QuickAnalysisView() {
  const [urlInput, setUrlInput] = useState("");
  const [targetName, setTargetName] = useState("");
  const [officialFeed, setOfficialFeed] = useState("");
  
  // Job & Results state (in RAM only, lost on refresh)
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobData, setJobData] = useState<QuickAnalysisJobResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  // Table view & filter state
  const [formatMode, setFormatMode] = useState<"incident" | "legacy">("incident");
  const [searchQuery, setSearchQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [riskFilter, setRiskFilter] = useState<string>("all");
  const [exporting, setExporting] = useState(false);

  // Screenshot modal state
  const [previewScreenshot, setPreviewScreenshot] = useState<{
    url: string;
    profileName: string;
  } | null>(null);

  // Inline edits state
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});

  const handleEdit = (itemId: string, field: string, value: string) => {
    setEdits((prev) => ({
      ...prev,
      [itemId]: {
        ...(prev[itemId] || {}),
        [field]: value,
      },
    }));
  };

  // Live URL breakdown computation
  const urlSummary = useMemo(() => {
    const lines = urlInput
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const breakdown: Record<string, number> = {};
    let validCount = 0;

    for (const raw of lines) {
      const lower = raw.toLowerCase();
      let plat = "other";
      if (lower.includes("facebook.com") || lower.includes("fb.me") || lower.includes("fb.com")) plat = "facebook";
      else if (lower.includes("instagram.com")) plat = "instagram";
      else if (lower.includes("twitter.com") || lower.includes("x.com")) plat = "twitter";
      else if (lower.includes("youtube.com") || lower.includes("youtu.be")) plat = "youtube";
      else if (lower.includes("t.me") || lower.includes("telegram.me")) plat = "telegram";
      else if (lower.includes("tiktok.com")) plat = "tiktok";

      breakdown[plat] = (breakdown[plat] || 0) + 1;
      if (plat !== "other") validCount++;
    }

    return { totalLines: lines.length, validCount, breakdown };
  }, [urlInput]);

  // Polling interval reference
  const pollingRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const pollJob = useCallback(async (id: string) => {
    try {
      const data = await quickAnalysisApi.getJob(id);
      setJobData(data);
      if (data.status === "done" || data.status === "cancelled" || data.status === "failed") {
        setLoading(false);
        setCancelling(false);
        stopPolling();
        if (data.status === "done") {
          toast.success(`Quick Analysis completed for ${data.completed}/${data.total} URLs`);
        } else if (data.status === "cancelled") {
          toast.error("Analysis stopped by user");
        }
      }
    } catch (e) {
      stopPolling();
      setLoading(false);
      setCancelling(false);
      toast.error((e as Error).message || "Failed to update job status");
    }
  }, [stopPolling]);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const handleStart = async () => {
    const lines = urlInput
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);

    if (!lines.length) {
      toast.error("Please enter at least one URL to analyze");
      return;
    }

    try {
      setLoading(true);
      setJobData(null);
      const res = await quickAnalysisApi.start(lines, targetName, officialFeed);
      setJobId(res.job_id);

      if (res.skipped && res.skipped.length > 0) {
        toast(
          `Skipped ${res.skipped.length} invalid/duplicate URL(s)`,
          { icon: "ℹ️" }
        );
      }

      // Initial poll immediately
      await pollJob(res.job_id);

      // Start polling interval
      stopPolling();
      pollingRef.current = window.setInterval(() => {
        pollJob(res.job_id);
      }, 1500);
    } catch (e) {
      setLoading(false);
      toast.error((e as Error).message || "Failed to start quick analysis");
    }
  };

  const handleCancel = async () => {
    if (!jobId) return;
    try {
      setCancelling(true);
      await quickAnalysisApi.cancelJob(jobId);
      toast("Stopping analysis...", { icon: "⏳" });
    } catch (e) {
      setCancelling(false);
      toast.error((e as Error).message || "Failed to cancel");
    }
  };

  const handleClear = () => {
    setUrlInput("");
    setTargetName("");
    setOfficialFeed("");
    setJobId(null);
    setJobData(null);
    setSearchQuery("");
    stopPolling();
    toast.success("Workspace reset");
  };

  // Filtered rows for the table
  const filteredItems = useMemo(() => {
    if (!jobData?.items) return [];
    return jobData.items.filter((it) => {
      // Platform filter
      if (platformFilter !== "all" && it.platform !== platformFilter) return false;

      // Risk score filter
      if (riskFilter === "high" && (it.risk_score || 0) < 8) return false;
      if (riskFilter === "medium" && ((it.risk_score || 0) < 4 || (it.risk_score || 0) >= 8)) return false;
      if (riskFilter === "low" && (it.risk_score || 0) > 3) return false;

      // Text search
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchUrl = it.url.toLowerCase().includes(q);
        const matchName = (it.profile_name || "").toLowerCase().includes(q);
        const matchLoc = (it.location || "").toLowerCase().includes(q);
        const matchBio = (it.bio || "").toLowerCase().includes(q);
        if (!matchUrl && !matchName && !matchLoc && !matchBio) return false;
      }

      return true;
    });
  }, [jobData?.items, platformFilter, riskFilter, searchQuery]);

  // Export handlers
  const handleExport = async (fmt: "xlsx" | "csv" | "json" | "tsv") => {
    if (!jobData || !filteredItems.length) {
      toast.error("No analyzed items to export");
      return;
    }

    const rows = filteredItems.map((it) => {
      const baseRow = formatMode === "incident" ? it.incident_row : it.legacy_row;
      const itemEdits = edits[it.id] || {};
      return { ...baseRow, ...itemEdits };
    });

    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const filenameStem = `Quick-Analysis-${formatMode === "incident" ? "Platform-Format" : "Legacy-Format"}-${stamp}`;

    try {
      setExporting(true);
      if (fmt === "csv") {
        download(`${filenameStem}.csv`, rowsToCsv(rows), "text/csv");
        toast.success(`Exported ${rows.length} rows to CSV`);
      } else if (fmt === "json") {
        download(`${filenameStem}.json`, JSON.stringify(rows, null, 2), "application/json");
        toast.success(`Exported ${rows.length} rows to JSON`);
      } else if (fmt === "tsv") {
        const tsvText = rowsToTsv(rows);
        await navigator.clipboard.writeText(tsvText);
        toast.success(`Copied ${rows.length} rows (TSV) to clipboard! Paste into Excel/Sheets.`);
      } else if (fmt === "xlsx") {
        const filename = `${filenameStem}.xlsx`;
        const blob = await quickAnalysisApi.exportXlsx(filename, rows);
        downloadBlob(filename, blob);
        toast.success(`Exported ${rows.length} rows to Excel (.xlsx)`);
      }
    } catch (e) {
      toast.error((e as Error).message || "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const getRiskBadge = (score: number) => {
    if (score >= 8) {
      return (
        <span
          style={{
            background: "rgba(233, 80, 83, 0.2)",
            color: "var(--danger, #E95053)",
            border: "1px solid rgba(233, 80, 83, 0.4)",
            padding: "2px 8px",
            borderRadius: "12px",
            fontSize: "11px",
            fontWeight: 700,
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          ● High ({score})
        </span>
      );
    }
    if (score >= 4) {
      return (
        <span
          style={{
            background: "rgba(247, 144, 9, 0.2)",
            color: "var(--warning, #F79009)",
            border: "1px solid rgba(247, 144, 9, 0.4)",
            padding: "2px 8px",
            borderRadius: "12px",
            fontSize: "11px",
            fontWeight: 700,
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          ● Medium ({score})
        </span>
      );
    }
    return (
      <span
        style={{
          background: "rgba(18, 183, 106, 0.2)",
          color: "var(--success, #12B76A)",
          border: "1px solid rgba(18, 183, 106, 0.4)",
          padding: "2px 8px",
          borderRadius: "12px",
          fontSize: "11px",
          fontWeight: 700,
          display: "inline-flex",
          alignItems: "center",
          gap: "4px",
        }}
      >
        ● Low ({score})
      </span>
    );
  };

  return (
    <div style={{ animation: "fadeUp 0.4s ease", maxWidth: "1520px", margin: "0 auto", paddingBottom: "60px" }}>
      {/* ─── Ephemeral Memory Notification Banner ─── */}
      <div
        style={{
          background: "linear-gradient(90deg, rgba(136, 56, 221, 0.15), rgba(0, 240, 255, 0.08))",
          border: "1px solid rgba(136, 56, 221, 0.35)",
          borderRadius: "10px",
          padding: "12px 18px",
          marginBottom: "20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "16px",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div
            style={{
              width: "32px",
              height: "32px",
              borderRadius: "8px",
              background: "var(--primary-color, #8838DD)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              flexShrink: 0,
            }}
          >
            <QuickAnalysisNavIcon size={18} />
          </div>
          <div>
            <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main, #fff)", display: "flex", alignItems: "center", gap: "8px" }}>
              Quick Analysis — Ad-Hoc Multi-Platform Scraper
              <span
                style={{
                  background: "rgba(0, 240, 255, 0.15)",
                  color: "#00F0FF",
                  border: "1px solid rgba(0, 240, 255, 0.3)",
                  fontSize: "10px",
                  padding: "1px 6px",
                  borderRadius: "4px",
                  fontWeight: 600,
                  textTransform: "uppercase",
                }}
              >
                RAM Session
              </span>
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted, #98a2b3)", marginTop: "2px" }}>
              Paste direct URLs across Facebook, Instagram, Twitter/X, YouTube, Telegram &amp; TikTok. Data lives in temporary memory only and is completely cleared on page refresh.
            </div>
          </div>
        </div>

        {jobData && (
          <button
            onClick={handleClear}
            style={{
              background: "rgba(255, 255, 255, 0.08)",
              border: "1px solid var(--border-color, #344054)",
              color: "var(--text-main, #fff)",
              padding: "6px 14px",
              borderRadius: "6px",
              fontSize: "12px",
              cursor: "pointer",
              transition: "all 0.2s ease",
            }}
            title="Reset form and clear in-memory state"
          >
            Reset Workspace
          </button>
        )}
      </div>

      {/* ─── Input & Configuration Card ─── */}
      <div
        className="home-card"
        style={{
          background: "var(--bg-card, #1D2939)",
          border: "1px solid var(--border-color, #344054)",
          borderRadius: "12px",
          padding: "24px",
          marginBottom: "24px",
          boxShadow: "0 8px 24px rgba(0, 0, 0, 0.25)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px", flexWrap: "wrap", gap: "10px" }}>
          <div>
            <div style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-main, #fff)" }}>
              1. Direct URLs to Analyze
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-muted, #98a2b3)", marginTop: "2px" }}>
              Enter or paste URLs (one per line). Supported: Facebook, Instagram, X/Twitter, YouTube, Telegram, TikTok.
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <button
              type="button"
              onClick={() => setUrlInput(SAMPLE_URLS)}
              style={{
                background: "var(--bg-surface-3, #344054)",
                border: "1px solid var(--border-color, #475467)",
                color: "var(--text-main, #fff)",
                padding: "6px 12px",
                borderRadius: "6px",
                fontSize: "12px",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              Load Sample URLs
            </button>
            {urlInput && (
              <button
                type="button"
                onClick={() => setUrlInput("")}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--text-muted, #98a2b3)",
                  fontSize: "12px",
                  cursor: "pointer",
                  padding: "6px 8px",
                }}
              >
                Clear URLs
              </button>
            )}
          </div>
        </div>

        {/* Textarea */}
        <textarea
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          placeholder={`https://www.facebook.com/sample_account\nhttps://www.instagram.com/sample_profile\nhttps://x.com/sample_user\nhttps://www.youtube.com/@channel_name\nhttps://t.me/sample_channel\nhttps://www.tiktok.com/@sample_creator`}
          rows={5}
          disabled={loading}
          style={{
            width: "100%",
            background: "var(--bg-primary, #080F1E)",
            border: "1px solid var(--border-color, #344054)",
            borderRadius: "8px",
            color: "var(--text-main, #fff)",
            padding: "12px 14px",
            fontSize: "13px",
            fontFamily: "monospace",
            lineHeight: "1.6",
            resize: "vertical",
            outline: "none",
            boxSizing: "border-box",
            marginBottom: "14px",
          }}
        />

        {/* Live Detected Platforms Breakdown */}
        {urlSummary.totalLines > 0 && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              flexWrap: "wrap",
              marginBottom: "18px",
              padding: "10px 14px",
              background: "var(--bg-surface-3, #344054)",
              borderRadius: "8px",
              fontSize: "12px",
            }}
          >
            <span style={{ fontWeight: 600, color: "var(--text-dim, #98a2b3)" }}>
              Detected ({urlSummary.validCount}/{urlSummary.totalLines}):
            </span>
            {Object.entries(urlSummary.breakdown).map(([plat, count]) => {
              if (plat === "other") {
                return (
                  <span
                    key={plat}
                    style={{
                      background: "rgba(233, 80, 83, 0.15)",
                      color: "var(--danger, #E95053)",
                      border: "1px solid rgba(233, 80, 83, 0.3)",
                      padding: "2px 8px",
                      borderRadius: "6px",
                      fontSize: "11px",
                      fontWeight: 600,
                    }}
                  >
                    {count} Unsupported
                  </span>
                );
              }
              return (
                <span
                  key={plat}
                  style={{
                    background: "rgba(136, 56, 221, 0.2)",
                    color: "var(--text-main, #fff)",
                    border: "1px solid rgba(136, 56, 221, 0.4)",
                    padding: "2px 8px",
                    borderRadius: "6px",
                    fontSize: "11px",
                    fontWeight: 600,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "5px",
                  }}
                >
                  <PlatformIcon platform={plat} size={14} />
                  {count} {plat.charAt(0).toUpperCase() + plat.slice(1)}
                </span>
              );
            })}
          </div>
        )}

        {/* Optional Matching Parameters Grid */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "16px", marginBottom: "20px" }}>
          <div>
            <label style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "var(--text-dim, #98a2b3)", marginBottom: "6px" }}>
              Target / Brand Name (Optional)
            </label>
            <input
              type="text"
              value={targetName}
              onChange={(e) => setTargetName(e.target.value)}
              placeholder="e.g. CYFIRMA, BrandName, John Doe"
              disabled={loading}
              style={{
                width: "100%",
                background: "var(--bg-primary, #080F1E)",
                border: "1px solid var(--border-color, #344054)",
                borderRadius: "6px",
                color: "var(--text-main, #fff)",
                padding: "8px 12px",
                fontSize: "13px",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <span style={{ fontSize: "11px", color: "var(--text-muted, #98a2b3)", marginTop: "3px", display: "block" }}>
              Used to score name similarity and impersonation risk.
            </span>
          </div>

          <div>
            <label style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "var(--text-dim, #98a2b3)", marginBottom: "6px" }}>
              Official Feed / Account URL (Optional)
            </label>
            <input
              type="text"
              value={officialFeed}
              onChange={(e) => setOfficialFeed(e.target.value)}
              placeholder="e.g. https://twitter.com/officialbrand"
              disabled={loading}
              style={{
                width: "100%",
                background: "var(--bg-primary, #080F1E)",
                border: "1px solid var(--border-color, #344054)",
                borderRadius: "6px",
                color: "var(--text-main, #fff)",
                padding: "8px 12px",
                fontSize: "13px",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <span style={{ fontSize: "11px", color: "var(--text-muted, #98a2b3)", marginTop: "3px", display: "block" }}>
              Recorded in the export as Original Feed for comparison.
            </span>
          </div>
        </div>

        {/* Action Button Bar */}
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          {!loading ? (
            <button
              type="button"
              onClick={handleStart}
              disabled={!urlSummary.validCount}
              style={{
                background: urlSummary.validCount
                  ? "linear-gradient(135deg, #8838DD 0%, #7727CD 100%)"
                  : "var(--bg-surface-3, #344054)",
                color: "#fff",
                border: "none",
                padding: "10px 24px",
                borderRadius: "8px",
                fontSize: "14px",
                fontWeight: 700,
                cursor: urlSummary.validCount ? "pointer" : "not-allowed",
                boxShadow: urlSummary.validCount ? "0 4px 14px rgba(136, 56, 221, 0.4)" : "none",
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                transition: "all 0.2s ease",
              }}
            >
              <QuickAnalysisNavIcon size={16} />
              <span>Start Quick Analysis ({urlSummary.validCount || 0})</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={handleCancel}
              disabled={cancelling}
              style={{
                background: "var(--danger, #E95053)",
                color: "#fff",
                border: "none",
                padding: "10px 24px",
                borderRadius: "8px",
                fontSize: "14px",
                fontWeight: 700,
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <StopIcon size={16} />
              <span>{cancelling ? "Stopping..." : "Stop Analysis"}</span>
            </button>
          )}
        </div>
      </div>

      {/* ─── Live Execution Progress Card ─── */}
      {jobData && (
        <div
          style={{
            background: "var(--bg-card, #1D2939)",
            border: "1px solid var(--border-color, #344054)",
            borderRadius: "12px",
            padding: "20px",
            marginBottom: "24px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", flexWrap: "wrap", gap: "10px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background:
                    jobData.status === "running"
                      ? "var(--cyan, #00F0FF)"
                      : jobData.status === "done"
                      ? "var(--success, #12B76A)"
                      : "var(--danger, #E95053)",
                  boxShadow: jobData.status === "running" ? "0 0 10px var(--cyan, #00F0FF)" : "none",
                }}
              />
              <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main, #fff)" }}>
                Analysis Progress: {jobData.completed} / {jobData.total} Finished
              </span>
              <span
                style={{
                  fontSize: "11px",
                  color: "var(--text-muted, #98a2b3)",
                  background: "var(--bg-surface-3, #344054)",
                  padding: "2px 8px",
                  borderRadius: "4px",
                  textTransform: "uppercase",
                  fontWeight: 600,
                }}
              >
                {jobData.status}
              </span>
            </div>

            <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-main, #fff)" }}>
              {Math.round((jobData.completed / (jobData.total || 1)) * 100)}%
            </div>
          </div>

          {/* Progress Bar */}
          <div
            style={{
              width: "100%",
              height: "6px",
              background: "var(--bg-primary, #080F1E)",
              borderRadius: "3px",
              overflow: "hidden",
              marginBottom: "16px",
            }}
          >
            <div
              style={{
                width: `${Math.min(100, Math.round((jobData.completed / (jobData.total || 1)) * 100))}%`,
                height: "100%",
                background: "linear-gradient(90deg, var(--cyan, #00F0FF), var(--primary-color, #8838DD))",
                transition: "width 0.3s ease",
              }}
            />
          </div>

          {/* Platform Status Chips */}
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            {Object.entries(jobData.platform_progress || {}).map(([plat, prog]) => (
              <div
                key={plat}
                style={{
                  background: "var(--bg-surface-3, #344054)",
                  border: "1px solid var(--border-color, #475467)",
                  borderRadius: "8px",
                  padding: "6px 12px",
                  fontSize: "12px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <PlatformIcon platform={plat} size={16} />
                <span style={{ fontWeight: 600, color: "var(--text-main, #fff)" }}>{prog.displayName}:</span>
                <span style={{ color: "var(--text-dim, #98a2b3)" }}>
                  {prog.completed}/{prog.total}
                </span>
                <span
                  style={{
                    fontSize: "10px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    color:
                      prog.status === "done"
                        ? "var(--success, #12B76A)"
                        : prog.status === "running"
                        ? "var(--cyan, #00F0FF)"
                        : prog.status === "failed"
                        ? "var(--danger, #E95053)"
                        : "var(--text-muted)",
                  }}
                >
                  {prog.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ─── Dual-Format Results & Export Section ─── */}
      {jobData && jobData.items.length > 0 && (
        <div
          className="home-card"
          style={{
            background: "var(--bg-card, #1D2939)",
            border: "1px solid var(--border-color, #344054)",
            borderRadius: "12px",
            padding: "24px",
            boxShadow: "0 8px 24px rgba(0, 0, 0, 0.25)",
          }}
        >
          {/* Format Toggle & Export Toolbar */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "20px",
              flexWrap: "wrap",
              gap: "16px",
              paddingBottom: "16px",
              borderBottom: "1px solid var(--border-color, #344054)",
            }}
          >
            {/* Format Mode Tabs */}
            <div style={{ display: "flex", alignItems: "center", gap: "6px", background: "var(--bg-primary, #080F1E)", padding: "4px", borderRadius: "8px" }}>
              <button
                type="button"
                onClick={() => setFormatMode("incident")}
                style={{
                  background: formatMode === "incident" ? "var(--primary-color, #8838DD)" : "transparent",
                  color: "#fff",
                  border: "none",
                  padding: "7px 16px",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: formatMode === "incident" ? 700 : 500,
                  cursor: "pointer",
                  transition: "all 0.2s ease",
                }}
              >
                📋 Platform / Incident Format (Takedown)
              </button>

              <button
                type="button"
                onClick={() => setFormatMode("legacy")}
                style={{
                  background: formatMode === "legacy" ? "var(--primary-color, #8838DD)" : "transparent",
                  color: "#fff",
                  border: "none",
                  padding: "7px 16px",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontWeight: formatMode === "legacy" ? 700 : 500,
                  cursor: "pointer",
                  transition: "all 0.2s ease",
                }}
              >
                📊 Legacy Format (Raw Analysis)
              </button>
            </div>

            {/* Export Actions */}
            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={() => handleExport("xlsx")}
                disabled={exporting || !filteredItems.length}
                style={{
                  background: "var(--success, #12B76A)",
                  color: "#fff",
                  border: "none",
                  padding: "7px 14px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 700,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <DownloadIcon size={14} />
                <span>Excel (.xlsx)</span>
              </button>

              <button
                type="button"
                onClick={() => handleExport("csv")}
                disabled={exporting || !filteredItems.length}
                style={{
                  background: "var(--bg-surface-3, #344054)",
                  border: "1px solid var(--border-color, #475467)",
                  color: "var(--text-main, #fff)",
                  padding: "7px 12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                CSV
              </button>

              <button
                type="button"
                onClick={() => handleExport("json")}
                disabled={exporting || !filteredItems.length}
                style={{
                  background: "var(--bg-surface-3, #344054)",
                  border: "1px solid var(--border-color, #475467)",
                  color: "var(--text-main, #fff)",
                  padding: "7px 12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                JSON
              </button>

              <button
                type="button"
                onClick={() => handleExport("tsv")}
                disabled={exporting || !filteredItems.length}
                style={{
                  background: "var(--bg-surface-3, #344054)",
                  border: "1px solid var(--border-color, #475467)",
                  color: "var(--text-main, #fff)",
                  padding: "7px 12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
                title="Copy formatted TSV to paste directly into Google Sheets or Excel"
              >
                Copy TSV
              </button>
            </div>
          </div>

          {/* Filter & Search Bar */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px", flexWrap: "wrap" }}>
            {/* Search Box */}
            <div style={{ position: "relative", minWidth: "260px", flex: 1 }}>
              <SearchIcon size={14} style={{ position: "absolute", left: "10px", top: "10px", color: "var(--text-muted)" }} />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search username, url, bio, location..."
                style={{
                  width: "100%",
                  background: "var(--bg-primary, #080F1E)",
                  border: "1px solid var(--border-color, #344054)",
                  borderRadius: "6px",
                  color: "var(--text-main, #fff)",
                  padding: "7px 10px 7px 32px",
                  fontSize: "12px",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            {/* Platform Filter */}
            <select
              value={platformFilter}
              onChange={(e) => setPlatformFilter(e.target.value)}
              style={{
                background: "var(--bg-primary, #080F1E)",
                border: "1px solid var(--border-color, #344054)",
                color: "var(--text-main, #fff)",
                padding: "7px 12px",
                borderRadius: "6px",
                fontSize: "12px",
                outline: "none",
                cursor: "pointer",
              }}
            >
              <option value="all">All Platforms</option>
              <option value="facebook">Facebook</option>
              <option value="instagram">Instagram</option>
              <option value="twitter">Twitter / X</option>
              <option value="youtube">YouTube</option>
              <option value="telegram">Telegram</option>
              <option value="tiktok">TikTok</option>
            </select>

            {/* Risk Filter */}
            <select
              value={riskFilter}
              onChange={(e) => setRiskFilter(e.target.value)}
              style={{
                background: "var(--bg-primary, #080F1E)",
                border: "1px solid var(--border-color, #344054)",
                color: "var(--text-main, #fff)",
                padding: "7px 12px",
                borderRadius: "6px",
                fontSize: "12px",
                outline: "none",
                cursor: "pointer",
              }}
            >
              <option value="all">All Risk Levels</option>
              <option value="high">High Risk (8-9)</option>
              <option value="medium">Medium Risk (4-7)</option>
              <option value="low">Low Risk (2-3)</option>
            </select>

            <span style={{ fontSize: "12px", color: "var(--text-muted)", marginLeft: "auto" }}>
              Showing {filteredItems.length} of {jobData.items.length} items
            </span>
          </div>

          {/* ─── Interactive Table View ─── */}
          <div style={{ overflowX: "auto", border: "1px solid var(--border-color, #344054)", borderRadius: "8px" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px", textAlign: "left" }}>
              <thead>
                <tr style={{ background: "var(--bg-primary, #080F1E)", color: "var(--text-dim, #98a2b3)", borderBottom: "1px solid var(--border-color, #344054)" }}>
                  <th style={{ padding: "10px 14px", fontWeight: 600, textAlign: "center" }}>Screenshot</th>
                  <th style={{ padding: "10px 14px", fontWeight: 600 }}>Platform</th>
                  <th style={{ padding: "10px 14px", fontWeight: 600 }}>Profile / Account</th>
                  <th style={{ padding: "10px 14px", fontWeight: 600 }}>Risk Rating</th>
                  {formatMode === "incident" ? (
                    <>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Asset Name</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Active</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Name Match</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Logo Match</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Followers</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Last Post</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Location</th>
                    </>
                  ) : (
                    <>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Target Name</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Followers</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Active</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Logo (Yes/No)</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Name (Yes/No)</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Location</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Last Post</th>
                      <th style={{ padding: "10px 14px", fontWeight: 600 }}>Priority</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {!filteredItems.length ? (
                  <tr>
                    <td colSpan={12} style={{ padding: "32px", textAlign: "center", color: "var(--text-muted)" }}>
                      No matching records found.
                    </td>
                  </tr>
                ) : (
                  filteredItems.map((it) => {
                    const originalRow = formatMode === "incident" ? it.incident_row : it.legacy_row;
                    const row = { ...originalRow, ...(edits[it.id] || {}) };
                    return (
                      <tr
                        key={it.id}
                        style={{
                          borderBottom: "1px solid var(--border-color, #293546)",
                          background: it.status === "error" ? "rgba(233, 80, 83, 0.05)" : "transparent",
                          transition: "background 0.15s ease",
                        }}
                      >
                        {/* Screenshot Modal Trigger */}
                        <td style={{ padding: "10px 14px", textAlign: "center" }}>
                          {it.has_screenshot ? (
                            <button
                              type="button"
                              onClick={() =>
                                setPreviewScreenshot({
                                  url: quickAnalysisApi.getScreenshotUrl(jobData.id, it.id),
                                  profileName: it.profile_name || it.entity_id,
                                })
                              }
                              style={{
                                background: "rgba(0, 240, 255, 0.12)",
                                border: "1px solid rgba(0, 240, 255, 0.3)",
                                color: "#00F0FF",
                                padding: "4px 8px",
                                borderRadius: "4px",
                                fontSize: "11px",
                                cursor: "pointer",
                                fontWeight: 600,
                              }}
                            >
                              View Capture
                            </button>
                          ) : (
                            <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>—</span>
                          )}
                        </td>

                        {/* Platform */}
                        <td style={{ padding: "10px 14px", whiteSpace: "nowrap" }}>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontWeight: 600, color: "var(--text-main, #fff)" }}>
                            <PlatformIcon platform={it.platform} size={16} />
                            {it.platform_name}
                          </span>
                        </td>

                        {/* Profile Info */}
                        <td style={{ padding: "10px 14px", minWidth: "220px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            {it.profile_image_url ? (
                              <img
                                src={`/profiles/media-proxy?url=${encodeURIComponent(it.profile_image_url)}`}
                                alt=""
                                style={{ width: "26px", height: "26px", borderRadius: "50%", objectFit: "cover", flexShrink: 0 }}
                                onError={(e) => {
                                  (e.target as HTMLElement).style.display = "none";
                                }}
                              />
                            ) : (
                              <div
                                style={{
                                  width: "26px",
                                  height: "26px",
                                  borderRadius: "50%",
                                  background: "var(--bg-surface-3, #344054)",
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  fontSize: "11px",
                                  fontWeight: 700,
                                  flexShrink: 0,
                                }}
                              >
                                {(it.profile_name || it.entity_id || "?").charAt(0).toUpperCase()}
                              </div>
                            )}

                            <div style={{ minWidth: 0 }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                                <a
                                  href={it.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{ fontWeight: 600, color: "var(--cyan, #00F0FF)", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                                >
                                  {it.profile_name || it.entity_id || "Profile Link"}
                                </a>
                                {it.verified && <VerifiedBadgeIcon size={14} />}
                              </div>
                              {it.error ? (
                                <div style={{ fontSize: "11px", color: "var(--danger, #E95053)" }}>
                                  {it.error}
                                </div>
                              ) : (
                                <div style={{ fontSize: "11px", color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {it.entity_id}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>

                        {/* Risk Rating */}
                        <td style={{ padding: "10px 14px", whiteSpace: "nowrap" }}>
                          {getRiskBadge(it.risk_score || 2)}
                        </td>

                        {/* Mode Specific Columns */}
                        {formatMode === "incident" ? (
                          <>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["AssetName"] || "")} onChange={(v) => handleEdit(it.id, "AssetName", v)} /></td>
                            <td style={{ padding: "0", textAlign: "center" }}><ToggleCell value={String(row["Active (Yes/No)"] || "")} onChange={(v) => handleEdit(it.id, "Active (Yes/No)", v)} /></td>
                            <td style={{ padding: "0", textAlign: "center" }}><ToggleCell value={String(row["Name (Yes/No)"] || "")} onChange={(v) => handleEdit(it.id, "Name (Yes/No)", v)} /></td>
                            <td style={{ padding: "0", textAlign: "center" }}><ToggleCell value={String(row["Logo (Yes/No)"] || "")} onChange={(v) => handleEdit(it.id, "Logo (Yes/No)", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Number of Followers"] ?? "")} onChange={(v) => handleEdit(it.id, "Number of Followers", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Last Post (DD-MM-YYYY) (Optional)"] || "")} onChange={(v) => handleEdit(it.id, "Last Post (DD-MM-YYYY) (Optional)", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Location"] || "")} onChange={(v) => handleEdit(it.id, "Location", v)} /></td>
                          </>
                        ) : (
                          <>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Original Name"] || "")} onChange={(v) => handleEdit(it.id, "Original Name", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Followers"] ?? "")} onChange={(v) => handleEdit(it.id, "Followers", v)} /></td>
                            <td style={{ padding: "0", textAlign: "center" }}><ToggleCell value={String(row["Active (Yes / No)"] || "")} onChange={(v) => handleEdit(it.id, "Active (Yes / No)", v)} /></td>
                            <td style={{ padding: "0", textAlign: "center" }}><ToggleCell value={String(row["Logo (Yes / No)"] || "")} onChange={(v) => handleEdit(it.id, "Logo (Yes / No)", v)} /></td>
                            <td style={{ padding: "0", textAlign: "center" }}><ToggleCell value={String(row["Name (Yes / No)"] || "")} onChange={(v) => handleEdit(it.id, "Name (Yes / No)", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Location"] || "")} onChange={(v) => handleEdit(it.id, "Location", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["Last Post (DD-MM-YYYY) (Optional)"] || "")} onChange={(v) => handleEdit(it.id, "Last Post (DD-MM-YYYY) (Optional)", v)} /></td>
                            <td style={{ padding: "0" }}><EditableCell value={String(row["priority"] || "")} onChange={(v) => handleEdit(it.id, "priority", v)} /></td>
                          </>
                        )}
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ─── Screenshot Modal Preview ─── */}
      {previewScreenshot && (
        <div
          onClick={() => setPreviewScreenshot(null)}
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.85)",
            backdropFilter: "blur(6px)",
            zIndex: 9999,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "20px",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--bg-card, #1D2939)",
              border: "1px solid var(--border-color, #344054)",
              borderRadius: "12px",
              padding: "16px",
              maxWidth: "90vw",
              maxHeight: "90vh",
              display: "flex",
              flexDirection: "column",
              boxShadow: "0 20px 50px rgba(0,0,0,0.5)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
              <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--text-main, #fff)" }}>
                Evidence Capture: {previewScreenshot.profileName}
              </span>
              <button
                onClick={() => setPreviewScreenshot(null)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--text-muted, #98a2b3)",
                  fontSize: "18px",
                  cursor: "pointer",
                  padding: "2px 8px",
                }}
              >
                ✕
              </button>
            </div>
            <div style={{ overflow: "auto", maxHeight: "calc(90vh - 80px)", borderRadius: "6px" }}>
              <img
                src={previewScreenshot.url}
                alt="Profile Evidence Capture"
                style={{ maxWidth: "100%", height: "auto", display: "block", borderRadius: "6px" }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
