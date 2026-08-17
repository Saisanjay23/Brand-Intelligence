// The app's outer shell, top nav header + scrollable content area, split
// out of App.tsx so routing/state composition (App.tsx) and page chrome
// (this file) aren't the same component. Renders identical markup to what
// App.tsx used to build inline; no behavior change.
import type { ReactNode } from "react";
import { Header, type ViewPage } from "../components/Header";
import type { Client } from "../api/types";
import type { RecentClient } from "../services/recentClients";

interface Props {
  page: ViewPage;
  onPage: (page: ViewPage) => void;
  clientId: string;
  clientName: string;
  recentClients: RecentClient[];
  allClients?: Client[];
  onClient: (clientId: string, name: string) => void;
  onForgetClient: (clientId: string) => void;
  activeJobsCount: number;
  readySessionsCount: number;
  platformCount: number;
  liveResultsCount?: number;
  error: string;
  children: ReactNode;
}

export function AppLayout({ error, children, ...headerProps }: Props) {
  return (
    <div className="app-container">
      <Header {...headerProps} />

      <main className="page-content">
        {error && (
          <div
            style={{
              background: "rgba(233, 80, 83,0.1)",
              border: "1px solid rgba(233, 80, 83,0.25)",
              color: "var(--danger)",
              padding: "10px 16px",
              borderRadius: "10px",
              marginBottom: "16px",
              fontSize: "13px",
            }}
          >
            ⚠️ {error}
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
