import { useCallback, useEffect, useMemo, useState } from "react";
import { healthApi } from "./api/healthApi";
import { sessionsApi } from "./api/sessionsApi";
import type { Job, PlatformHealth, SessionInfo } from "./api/types";
import { ResultsGrid } from "./components/ResultsGrid";
import type { ViewPage } from "./components/Header";
import { AppLayout } from "./layouts/AppLayout";
import { DashboardView } from "./pages/DashboardView";
import { HomeView } from "./pages/HomeView";
import { SessionPanel } from "./pages/SessionPanel";
import { useJobPolling } from "./hooks/useJobPolling";
import { loadRecentClients, rememberClient, forgetClient, type RecentClient } from "./services/recentClients";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

export default function App() {
  const [page, setPage] = useState<ViewPage>("home");
  const [platforms, setPlatforms] = useState<PlatformHealth[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [recentClients, setRecentClients] = useState<RecentClient[]>([]);
  const [clientId, setClientId] = useState("");
  const [clientName, setClientName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const recents = loadRecentClients();
    setRecentClients(recents);
    if (recents.length) {
      setClientId(recents[0].client_id);
      setClientName(recents[0].name);
    }
  }, []);

  useEffect(() => {
    healthApi
      .platformsHealth()
      .then((res) => setPlatforms(res.items))
      .catch((e) => setError(e.message));
  }, []);

  const refreshSessions = useCallback(() => {
    if (!platforms.length) return;
    Promise.all(platforms.map((p) => sessionsApi.sessionStatus(p.platform).catch(() => null)))
      .then((results) => setSessions(results.filter((s): s is SessionInfo => s !== null)))
      .catch(() => {});
  }, [platforms]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const onClient = useCallback((id: string, name: string) => {
    setClientId(id);
    setClientName(name);
    setRecentClients(rememberClient({ client_id: id, name }));
  }, []);

  const onForgetClient = useCallback(
    (id: string) => {
      const next = forgetClient(id);
      setRecentClients(next);
      if (clientId === id) {
        setClientId(next[0]?.client_id || "");
        setClientName(next[0]?.name || "");
      }
    },
    [clientId],
  );

  const discoveryJobs = useJobPolling();
  const analysisJobs = useJobPolling();

  const activeJobsCount = useMemo(() => {
    const isActive = (j: Job) => ACTIVE_JOB_STATUSES.has(j.status);
    return (
      Object.values(discoveryJobs.jobs).filter(isActive).length +
      Object.values(analysisJobs.jobs).filter(isActive).length
    );
  }, [discoveryJobs.jobs, analysisJobs.jobs]);

  const readySessionsCount = sessions.filter((s) => s.state === "ready").length;

  return (
    <AppLayout
      page={page}
      onPage={setPage}
      clientId={clientId}
      clientName={clientName}
      recentClients={recentClients}
      onClient={onClient}
      onForgetClient={onForgetClient}
      activeJobsCount={activeJobsCount}
      readySessionsCount={readySessionsCount}
      platformCount={platforms.length}
      error={error}
    >
      {page === "home" && (
        <>
          <HomeView
            clientId={clientId}
            clientName={clientName}
            onClient={onClient}
            busy={discoveryJobs.running}
            analysisBusy={analysisJobs.running}
            onJobs={(jobs) => {
              setError("");
              const disc = jobs.filter((j) => j.kind === "discovery");
              const analysis = jobs.filter((j) => j.kind !== "discovery");
              if (disc.length) discoveryJobs.watch(disc);
              if (analysis.length) analysisJobs.watch(analysis);
            }}
            onError={setError}
          />
          <div style={{ marginTop: "32px", animation: "fadeUp 0.6s ease" }}>
            <ResultsGrid
              clientId={clientId}
              platforms={platforms}
              discoveryRunning={discoveryJobs.running}
              discoveryLog={discoveryJobs.log}
              analysisRunning={analysisJobs.running}
              analysisLog={analysisJobs.log}
              onError={setError}
            />
          </div>
        </>
      )}

      {page === "dashboard" && (
        <DashboardView
          clientId={clientId}
          activeJobsCount={activeJobsCount}
          platforms={platforms}
          sessions={sessions}
          logs={[...discoveryJobs.log, ...analysisJobs.log]}
        />
      )}

      {page === "sessions" && <SessionPanel sessions={sessions} onChanged={refreshSessions} />}
    </AppLayout>
  );
}
