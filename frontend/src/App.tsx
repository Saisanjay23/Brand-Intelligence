import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Toaster } from "react-hot-toast";
import { clientsApi } from "./api/clientsApi";
import { jobsApi } from "./api/jobsApi";
import type { Client, Job } from "./api/types";
import type { ViewPage } from "./components/Header";
import { AppLayout } from "./layouts/AppLayout";
import { AdminPanel } from "./pages/AdminPanel";
import { HomeView } from "./pages/HomeView";
import { LiveResultsView } from "./pages/LiveResultsView";
import { QuickAnalysisView } from "./pages/QuickAnalysisView";
import { useJobPolling } from "./hooks/useJobPolling";
import { usePlatformState } from "./hooks/usePlatformState";
import { loadRecentClients, rememberClient, forgetClient, type RecentClient } from "./services/recentClients";

const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

export default function App() {
  const [page, setPage] = useState<ViewPage>("home");
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("theme") as "light" | "dark") || "dark"
  );
  const [recentClients, setRecentClients] = useState<RecentClient[]>([]);
  const [allClients, setAllClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [clientName, setClientName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  const refreshAllClients = useCallback(() => {
    clientsApi.listClients().then((res) => setAllClients(res.items)).catch(() => {});
  }, []);

  useEffect(() => {
    refreshAllClients();
  }, [refreshAllClients]);

  useEffect(() => {
    const recents = loadRecentClients();
    setRecentClients(recents);
    if (recents.length) {
      setClientId(recents[0].client_id);
      setClientName(recents[0].name);
    }
  }, []);

  // Platform health and session pools, kept live by one poller and
  // refreshed the instant any panel changes a session, see the hook for
  // why these two have to move together.
  const {
    platforms,
    sessions,
    refresh: refreshPlatformState,
    error: platformStateError,
  } = usePlatformState();

  const onClient = useCallback(
    (id: string, name: string) => {
      setClientId(id);
      setClientName(name);
      if (id) setRecentClients(rememberClient({ client_id: id, name }));
      refreshAllClients();
    },
    [refreshAllClients],
  );

  const onForgetClient = useCallback(
    (id: string) => {
      const nextRecents = forgetClient(id);
      setRecentClients(nextRecents);
      setAllClients((prev) => {
        const remaining = prev.filter((c) => c.client_id !== id);
        if (clientId === id) {
          const nextClient = remaining[0] || nextRecents[0];
          setClientId(nextClient?.client_id || "");
          setClientName(nextClient?.name || "");
        }
        return remaining;
      });
      refreshAllClients();
    },
    [clientId, refreshAllClients],
  );

  // A finished sweep is the other moment session state changes on its own:
  // a job that hit a login wall has just quarantined the session it was
  // holding. Refreshing on finish shows that immediately rather than on
  // whichever poll happens to come next.
  const discoveryJobs = useJobPolling(refreshPlatformState);
  const analysisJobs = useJobPolling(refreshPlatformState);

  // Adopt jobs this browser session did not start.
  //
  // Watching a job is what turns on the live view: the results grid only
  // polls for new rows while `discoveryRunning`/`analysisRunning` is true
  // (ResultsGrid's "live preview polling" effect), and the progress and log
  // panels read from the same place. Until now the ONLY thing that ever
  // called watch() was the Clients page handing back the jobs it had just
  // launched, so an analysis queued any other way was completely
  // invisible here:
  //
  //   - validating a profile queues one server-side (profile_service.py
  //     patch_profile / bulk_patch_profiles), which is the common case
  //   - the round-robin engine queues them continuously
  //   - the scheduler's catch-up sweep queues them
  //
  // For all of those the grid sat still while rows were being written
  // behind it, and only caught up when something else happened to reload
  // it, switching tabs and back, which changes `load`'s identity. Asking
  // the server what is actually running for this client closes that gap at
  // its source, and the log/progress panels light up for background work
  // for the first time as a side effect.
  //
  // Refs, not effect dependencies, for the already-watched check: the hook
  // returns a fresh `jobs` object every render, so depending on it would
  // rebuild this interval constantly. `watch` is stable (useCallback).
  const watchedDiscovery = useRef(discoveryJobs.jobs);
  const watchedAnalysis = useRef(analysisJobs.jobs);
  watchedDiscovery.current = discoveryJobs.jobs;
  watchedAnalysis.current = analysisJobs.jobs;

  const watchDiscovery = discoveryJobs.watch;
  const watchAnalysis = analysisJobs.watch;

  useEffect(() => {
    if (!clientId) return;
    let cancelled = false;

    const adopt = async () => {
      try {
        const { items } = await jobsApi.jobs(clientId, 25);
        if (cancelled) return;
        // Only ACTIVE jobs. Re-watching one already being tracked would
        // reset its event cursor and replay its log; adopting a finished
        // one would fire a completion toast for something that ended
        // before anyone was looking.
        const fresh = items.filter(
          (j) =>
            ACTIVE_JOB_STATUSES.has(j.status) &&
            !watchedDiscovery.current[j.id] &&
            !watchedAnalysis.current[j.id],
        );
        const disc = fresh.filter((j) => j.kind === "discovery");
        const analysis = fresh.filter((j) => j.kind !== "discovery");
        if (disc.length) watchDiscovery(disc);
        if (analysis.length) watchAnalysis(analysis);
      } catch {
        // transient, the next tick tries again
      }
    };

    void adopt();
    const tick = setInterval(adopt, 4000);
    return () => {
      cancelled = true;
      clearInterval(tick);
    };
  }, [clientId, watchDiscovery, watchAnalysis]);

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
      allClients={allClients}
      onClient={onClient}
      onForgetClient={onForgetClient}
      activeJobsCount={activeJobsCount}
      readySessionsCount={readySessionsCount}
      platformCount={platforms.length}
      error={error || platformStateError}
      theme={theme}
      onThemeToggle={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
    >
      {page === "home" && (
        <HomeView
          clientId={clientId}
          clientName={clientName}
          platforms={platforms}
          onClient={onClient}
          onForgetClient={onForgetClient}
          busy={discoveryJobs.running}
          analysisBusy={analysisJobs.running}
          onStopDiscovery={discoveryJobs.cancelAll}
          onStopAnalysis={analysisJobs.cancelAll}
          stoppingDiscovery={discoveryJobs.cancelling}
          stoppingAnalysis={analysisJobs.cancelling}
          onJobs={(jobs) => {
            setError("");
            const disc = jobs.filter((j) => j.kind === "discovery");
            const analysis = jobs.filter((j) => j.kind !== "discovery");
            if (disc.length) discoveryJobs.watch(disc);
            if (analysis.length) analysisJobs.watch(analysis);
            if (jobs.length) setPage("results");
          }}
          onError={setError}
        />
      )}

      {page === "results" && (
        <LiveResultsView
          clientId={clientId}
          platforms={platforms}
          discoveryRunning={discoveryJobs.running}
          discoveryLog={discoveryJobs.log}
          discoveryProgress={discoveryJobs.platformProgress}
          analysisRunning={analysisJobs.running}
          analysisLog={analysisJobs.log}
          analysisProgress={analysisJobs.platformProgress}
          onStopDiscovery={discoveryJobs.cancelAll}
          onStopAnalysis={analysisJobs.cancelAll}
          stoppingDiscovery={discoveryJobs.cancelling}
          stoppingAnalysis={analysisJobs.cancelling}
          onError={setError}
        />
      )}

      {page === "quick-analysis" && <QuickAnalysisView />}

      {page === "admin" && <AdminPanel sessions={sessions} onChanged={refreshPlatformState} />}
      <Toaster 
        position="bottom-right" 
        toastOptions={{ 
          style: { 
            background: 'rgba(16, 24, 40, 0.95)', 
            color: '#fff', 
            backdropFilter: 'blur(10px)',
            border: '1px solid var(--border-subtle)',
            fontSize: '13px',
            fontFamily: 'var(--font-main)'
          }
        }} 
      />
    </AppLayout>
  );
}
