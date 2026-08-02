// Live discovery/analysis results, isolated from client configuration
// (HomeView) onto its own nav page -- results browsing is a different task
// from curating a client's search config.
import type { JobEvent, PlatformHealth, PlatformProgress } from "../api/types";
import { ResultsGrid } from "../components/ResultsGrid";

interface Props {
  clientId: string;
  clientName: string;
  platforms: PlatformHealth[];
  discoveryRunning: boolean;
  discoveryLog: JobEvent[];
  discoveryProgress: Record<string, PlatformProgress>;
  analysisRunning: boolean;
  analysisLog: JobEvent[];
  analysisProgress: Record<string, PlatformProgress>;
  onError: (msg: string) => void;
}

export function LiveResultsView({
  clientId,
  clientName,
  platforms,
  discoveryRunning,
  discoveryLog,
  discoveryProgress,
  analysisRunning,
  analysisLog,
  analysisProgress,
  onError,
}: Props) {
  return (
    <div style={{ animation: "fadeUp 0.5s ease" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "22px" }}>
        <div className="home-icon-badge" style={{ width: 48, height: 48, fontSize: 22, margin: 0 }}>
          🛰️
        </div>
        <div>
          <div style={{ fontSize: "22px", fontWeight: 800, letterSpacing: "-0.4px" }}>Live Results</div>
          <div style={{ fontSize: "13px", color: "var(--text-muted)" }}>
            {clientId ? `${clientName || clientId} · ${clientId}` : "Select a client from Clients to see results"}
          </div>
        </div>
      </div>

      {!clientId ? (
        <div className="home-card" style={{ textAlign: "center", color: "var(--text-muted)", padding: "48px 24px" }}>
          No client selected. Head to the <strong>Clients</strong> tab to select or create one, then run a search.
        </div>
      ) : (
        <ResultsGrid
          clientId={clientId}
          platforms={platforms}
          discoveryRunning={discoveryRunning}
          discoveryLog={discoveryLog}
          discoveryProgress={discoveryProgress}
          analysisRunning={analysisRunning}
          analysisLog={analysisLog}
          analysisProgress={analysisProgress}
          onError={onError}
        />
      )}
    </div>
  );
}
