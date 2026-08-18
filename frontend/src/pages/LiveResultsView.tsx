// Live discovery/analysis results, isolated from client configuration
// (HomeView) onto its own nav page, results browsing is a different task
// from curating a client's search config.
import type { JobEvent, PlatformHealth, PlatformProgress } from "../api/types";
import { ResultsGrid } from "../components/ResultsGrid";

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
  stoppingDiscovery?: boolean;
  stoppingAnalysis?: boolean;
  onError: (msg: string) => void;
}

export function LiveResultsView({
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
  stoppingDiscovery,
  stoppingAnalysis,
  onError,
}: Props) {
  return (
    <div style={{ animation: "fadeUp 0.5s ease" }}>


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
          onStopDiscovery={onStopDiscovery}
          onStopAnalysis={onStopAnalysis}
          stoppingDiscovery={stoppingDiscovery}
          stoppingAnalysis={stoppingAnalysis}
          onError={onError}
        />
      )}
    </div>
  );
}
