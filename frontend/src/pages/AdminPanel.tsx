// Consolidates the operational/admin surfaces. Sessions, Mail, Proxies,
// Scheduler, behind one nav entry instead of scattering them across the
// top-level nav (which used to have separate "Sessions" and "Proxies"
// buttons with no shared shell). SessionPanel/ProxyPanel are unchanged,
// just nested here as tabs; MailPanel/SchedulerPanel are new.
import { useState } from "react";
import type { SessionInfo } from "../api/types";
import { LiveActivityPanel } from "./LiveActivityPanel";
import { MailPanel } from "./MailPanel";
import { ProxyPanel } from "./ProxyPanel";
import { SchedulerPanel } from "./SchedulerPanel";
import { SessionPanel } from "./SessionPanel";

interface Props {
  sessions: SessionInfo[];
  onChanged: () => void;
}

type AdminTab = "sessions" | "mail" | "proxies" | "scheduler" | "activity";

const TABS: { id: AdminTab; label: string; icon: string }[] = [
  { id: "sessions", label: "Sessions", icon: "🔑" },
  { id: "mail", label: "Mail", icon: "📧" },
  { id: "proxies", label: "Proxies", icon: "🌐" },
  { id: "scheduler", label: "Scheduler", icon: "🔁" },
  { id: "activity", label: "Live Activity", icon: "⚡" },
];

export function AdminPanel({ sessions, onChanged }: Props) {
  const [tab, setTab] = useState<AdminTab>("sessions");

  return (
    <div style={{ animation: "fadeUp 0.4s ease" }}>
      <div className="mode-tab-row" style={{ marginBottom: "20px" }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`mode-tab-btn ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            <span>{t.icon}</span>
            <span>{t.label}</span>
          </button>
        ))}
      </div>

      {tab === "sessions" && <SessionPanel sessions={sessions} onChanged={onChanged} />}
      {tab === "mail" && <MailPanel />}
      {tab === "proxies" && <ProxyPanel sessions={sessions} onChanged={onChanged} />}
      {tab === "scheduler" && <SchedulerPanel />}
      {tab === "activity" && <LiveActivityPanel />}
    </div>
  );
}
