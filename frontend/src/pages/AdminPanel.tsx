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
import {
  SessionsKeyIcon,
  MailAlertIcon,
  ProxyNodeIcon,
  SchedulerClockIcon,
  ActivityWaveIcon,
} from "../components/AppIcons";

interface Props {
  sessions: SessionInfo[];
  onChanged: () => void;
}

type AdminTab = "sessions" | "mail" | "proxies" | "scheduler" | "activity";

const TABS: { id: AdminTab; label: string; icon: (active: boolean) => React.ReactNode }[] = [
  { id: "sessions", label: "Sessions", icon: (a) => <SessionsKeyIcon size={15} color={a ? "#00F0FF" : "currentColor"} /> },
  { id: "mail", label: "Mail", icon: (a) => <MailAlertIcon size={15} color={a ? "#00F0FF" : "currentColor"} /> },
  { id: "proxies", label: "Proxies", icon: (a) => <ProxyNodeIcon size={15} color={a ? "#00F0FF" : "currentColor"} /> },
  { id: "scheduler", label: "Scheduler", icon: (a) => <SchedulerClockIcon size={15} color={a ? "#00F0FF" : "currentColor"} /> },
  { id: "activity", label: "Live Activity", icon: (a) => <ActivityWaveIcon size={15} color={a ? "#00F0FF" : "currentColor"} /> },
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
            <span>{t.icon(tab === t.id)}</span>
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
