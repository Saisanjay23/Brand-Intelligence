import { useState } from "react";
import type { RecentClient } from "../services/recentClients";

export type ViewPage = "home" | "results" | "dashboard" | "sessions";

interface Props {
  page: ViewPage;
  onPage: (page: ViewPage) => void;
  clientId: string;
  clientName: string;
  recentClients: RecentClient[];
  onClient: (clientId: string, name: string) => void;
  onForgetClient: (clientId: string) => void;
  activeJobsCount: number;
  readySessionsCount: number;
  platformCount: number;
  liveResultsCount?: number;
}

export function Header({
  page,
  onPage,
  clientId,
  clientName,
  recentClients,
  onClient,
  onForgetClient,
  activeJobsCount,
  readySessionsCount,
  platformCount,
  liveResultsCount = 0,
}: Props) {
  const [openDropdown, setOpenDropdown] = useState(false);

  const label = clientName || clientId;
  const initial = label ? label.charAt(0).toUpperCase() : "C";

  return (
    <header className="top-header">
      <div className="brand-logo-area">
        <div style={{ cursor: "pointer" }} onClick={() => onPage("home")}>
          <div className="brand-logo-title">BRAND INTEL</div>
          <div className="brand-logo-sub">
            CYFIRMA Social Intelligence Suite
          </div>
        </div>
      </div>

      <div className="nav-links-row">
        <button
          onClick={() => onPage("home")}
          className={`top-nav-btn ${page === "home" ? "active" : ""}`}
        >
          <span>🏢</span>
          <span>Clients</span>
        </button>

        <button
          onClick={() => onPage("results")}
          className={`top-nav-btn ${page === "results" ? "active" : ""}`}
        >
          <span>🛰️</span>
          <span>Live Results</span>
          {liveResultsCount > 0 && <span className="top-nav-badge">{liveResultsCount}</span>}
        </button>

        <button
          onClick={() => onPage("dashboard")}
          className={`top-nav-btn ${page === "dashboard" ? "active" : ""}`}
        >
          <span>📊</span>
          <span>Dashboard</span>
        </button>

        <button
          onClick={() => onPage("sessions")}
          className={`top-nav-btn ${page === "sessions" ? "active" : ""}`}
        >
          <span>🔑</span>
          <span>Sessions</span>
          <span className="top-nav-badge">
            {readySessionsCount}/{platformCount}
          </span>
        </button>
      </div>

      <div className="header-right-actions">
        <div
          className="bell-btn"
          onClick={() => onPage("dashboard")}
          title={`${activeJobsCount} active background job(s)`}
        >
          🔔
          {activeJobsCount > 0 && (
            <span className="bell-badge">{activeJobsCount}</span>
          )}
        </div>

        <div
          className="client-selector-chip"
          onClick={() => setOpenDropdown(!openDropdown)}
        >
          <span className="client-avatar-sm">{initial}</span>
          <span style={{ fontSize: "13px", fontWeight: 600 }}>
            {label || "Set Client"}
          </span>
          <span style={{ fontSize: "9px", color: "var(--text-dim)" }}>▼</span>
        </div>

        {openDropdown && (
          <div className="client-dropdown">
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-dim)",
                textTransform: "uppercase",
                letterSpacing: "1px",
                fontWeight: 700,
                padding: "2px 4px 8px",
              }}
            >
              Recent Clients (this browser only)
            </div>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "3px",
                maxHeight: "180px",
                overflowY: "auto",
              }}
            >
              {!recentClients.length && (
                <div
                  style={{
                    padding: "8px",
                    fontSize: "12px",
                    color: "var(--text-muted)",
                  }}
                >
                  No clients used yet. Set one from Live Discovery.
                </div>
              )}
              {recentClients.map((c) => (
                <button
                  key={c.client_id}
                  onClick={() => {
                    onClient(c.client_id, c.name);
                    setOpenDropdown(false);
                  }}
                  className={`client-list-item ${c.client_id === clientId ? "selected" : ""}`}
                >
                  <span
                    style={{
                      width: "22px",
                      height: "22px",
                      borderRadius: "50%",
                      background: "var(--bg-hover)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "11px",
                      fontWeight: 700,
                    }}
                  >
                    {(c.name || c.client_id).charAt(0).toUpperCase()}
                  </span>
                  <span style={{ flex: 1 }}>
                    {c.name || c.client_id}
                    {c.name && (
                      <span style={{ color: "var(--text-dim)", fontSize: "11px" }}>
                        {" "}
                        ({c.client_id})
                      </span>
                    )}
                  </span>
                  {c.client_id === clientId && (
                    <span style={{ color: "var(--cyan)" }}>✓</span>
                  )}
                  <span
                    role="button"
                    title="Remove from this list"
                    onClick={(e) => {
                      e.stopPropagation();
                      onForgetClient(c.client_id);
                    }}
                    style={{
                      color: "var(--danger)",
                      opacity: 0.7,
                      fontSize: "12px",
                      padding: "2px 4px",
                    }}
                  >
                    🗑
                  </span>
                </button>
              ))}
            </div>

            <div
              style={{
                fontSize: "11px",
                color: "var(--text-dim)",
                marginTop: "10px",
                paddingTop: "10px",
                borderTop: "1px solid var(--border-subtle)",
              }}
            >
              Set or create a client from the Live Discovery page.
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
