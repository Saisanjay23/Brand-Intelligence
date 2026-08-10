import { useState, type CSSProperties, type FC } from "react";
import { sessionsApi } from "../api/sessionsApi";
import type { SessionInfo } from "../api/types";

interface Props {
  sessions: SessionInfo[];
  onChanged: () => void;
}

interface ModalState {
  isOpen: boolean;
  platform?: SessionInfo;
  mode: "create" | "update";
  targetSession?: { id: string; identifier: string; isApiKey?: boolean };
}

function getPlatformIcon(platform: string): string {
  switch (platform) {
    case "facebook": return "📘";
    case "instagram": return "📸";
    case "twitter": return "𝕏";
    case "youtube": return "▶️";
    case "telegram": return "✈️";
    default: return "🌐";
  }
}

function cooldownLabel(rateLimitedUntil: number | undefined): string {
  if (!rateLimitedUntil) return "";
  const remainingMs = rateLimitedUntil * 1000 - Date.now();
  if (remainingMs <= 0) return "";
  const hours = Math.floor(remainingMs / 3600000);
  const mins = Math.round((remainingMs % 3600000) / 60000);
  if (hours > 0) return `cooldown ~${hours}h${mins ? ` ${mins}m` : ""}`;
  return `cooldown ~${Math.max(1, mins)}m`;
}

const embeddedStyles = `
@keyframes modalPopIn {
  from { opacity: 0; transform: scale(0.96) translateY(-6px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes runningPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

.tab-btn {
  transition: all 0.2s ease;
}
.tab-btn:hover {
  background: var(--bg-hover, #344054);
  color: var(--text-primary, #ffffff);
}

.action-btn {
  transition: all 0.2s ease;
}
.action-btn:hover {
  background: var(--primary, #8838dd) !important;
  color: #ffffff !important;
}
`;

export function SessionPanel({ sessions, onChanged }: Props) {
  const [modal, setModal] = useState<ModalState>({ isOpen: false, mode: "create" });
  const [activeTabs, setActiveTabs] = useState<Record<string, "pool" | "controls">>({});
  const [busyPlatform, setBusyPlatform] = useState<string>("");
  const [globalError, setGlobalError] = useState<string>("");

  const handleAction = async (fn: () => Promise<unknown>, platform: string) => {
    setBusyPlatform(platform);
    setGlobalError("");
    try {
      await fn();
      onChanged();
    } catch (e) {
      setGlobalError((e as Error).message);
    } finally {
      setBusyPlatform("");
    }
  };

  const setTab = (platformId: string, tab: "pool" | "controls") => {
    setActiveTabs((prev) => ({ ...prev, [platformId]: tab }));
  };

  return (
    <div style={{ padding: "24px", color: "var(--text-main, #f2f4f7)", position: "relative", maxWidth: "1600px", margin: "0 auto" }}>
      <style>{embeddedStyles}</style>

      {/* Header Area */}
      <div style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary, #fff)", margin: 0, letterSpacing: "-0.3px" }}>
            Platform Credential & Session Pool
          </h1>
          <p style={{ fontSize: "13px", color: "var(--text-muted, #98a2b3)", margin: "4px 0 0 0" }}>
            Manage up to 20 accounts per platform with smart rotation during discovery sweeps.
          </p>
        </div>
        {globalError && (
          <div style={{
            padding: "10px 16px",
            background: "var(--bg-surface-alt, #1d2939)",
            border: "1px solid var(--border-color, #344054)",
            borderRadius: "8px",
            color: "var(--text-primary, #ffffff)",
            fontSize: "13px",
            fontWeight: 500,
            display: "flex",
            alignItems: "center",
            gap: "10px"
          }}>
            <span>⚠️ Notice: {globalError}</span>
            <button
              onClick={() => setGlobalError("")}
              style={{ background: "transparent", border: "none", color: "#fff", cursor: "pointer", fontSize: "14px", fontWeight: 700 }}
            >
              ✕
            </button>
          </div>
        )}
      </div>

      {/* Grid of Uniform Platform Modules */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
        gap: "16px",
        alignItems: "stretch"
      }}>
        {sessions.map((s) => {
          const tab = activeTabs[s.platform] || "pool";
          const poolCount = s.sessions?.length || 0;
          const activeCount = s.sessions?.filter(x => x.status === "ready").length || 0;
          const isAtMax = poolCount >= 20;
          const isBusy = busyPlatform === s.platform;

          return (
            <div
              key={s.platform}
              style={{
                background: "var(--bg-surface, #1e2837)",
                border: "1px solid var(--border-color, #344054)",
                borderRadius: "12px",
                padding: "14px",
                boxShadow: "0 4px 12px rgba(0, 0, 0, 0.35)",
                display: "flex",
                flexDirection: "column",
                height: "380px", /* Compact identical height for all platform modules */
                overflow: "hidden",
                position: "relative"
              }}
            >
              {/* Platform Header */}
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px", flexShrink: 0 }}>
                <div style={{
                  width: "36px",
                  height: "36px",
                  borderRadius: "8px",
                  background: "var(--bg-surface-alt, #1d2939)",
                  border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "20px",
                  flexShrink: 0
                }}>
                  {getPlatformIcon(s.platform)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div title={s.name} style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-primary, #fff)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {s.name}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--text-muted, #98a2b3)", display: "flex", alignItems: "center", gap: "5px", marginTop: "1px" }}>
                    <span>Type: <strong style={{ color: "var(--text-body, #f2f4f7)" }}>{s.kind.toUpperCase()}</strong></span>
                    <span>•</span>
                    <span>Active: <strong style={{ color: "var(--text-body, #ffffff)" }}>{activeCount}</strong></span>
                  </div>
                </div>

                {/* Add Session Button */}
                {s.state !== "missing" || s.kind === "api-key" || s.kind === "cookies" ? (
                  <button
                    disabled={isAtMax || !!busyPlatform}
                    onClick={() => setModal({ isOpen: true, mode: "create", platform: s })}
                    style={{
                      padding: "6px 10px",
                      borderRadius: "6px",
                      background: isAtMax ? "var(--bg-surface-3, #344054)" : "var(--primary, #8838dd)",
                      color: "var(--text-primary, #fff)",
                      border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
                      fontWeight: 600,
                      fontSize: "11px",
                      cursor: isAtMax ? "not-allowed" : "pointer",
                      transition: "all 0.2s ease",
                      display: "flex",
                      alignItems: "center",
                      gap: "4px",
                      flexShrink: 0
                    }}
                    title={isAtMax ? "Pool limit reached (20 accounts)" : "Add account credentials"}
                  >
                    <span style={{ fontSize: "12px", fontWeight: 800 }}>＋</span>
                    <span>Add</span>
                  </button>
                ) : null}
              </div>

              {/* Tab Navigation */}
              <div style={{
                display: "flex",
                background: "var(--bg-app, #101828)",
                padding: "3px",
                borderRadius: "8px",
                marginBottom: "12px",
                border: "1px solid var(--border-color, #344054)",
                flexShrink: 0
              }}>
                <button
                  className="tab-btn"
                  onClick={() => setTab(s.platform, "pool")}
                  style={{
                    flex: 1,
                    padding: "6px 8px",
                    borderRadius: "6px",
                    border: "none",
                    background: tab === "pool" ? "var(--bg-light, #ffffff)" : "transparent",
                    color: tab === "pool" ? "var(--cyan, #8838dd)" : "var(--text-primary, #ffffff)",
                    fontSize: "11px",
                    fontWeight: tab === "pool" ? 700 : 500,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: "5px",
                    boxShadow: tab === "pool" ? "0 2px 6px rgba(0, 0, 0, 0.2)" : "none"
                  }}
                >
                  <span>🗃️ Pool</span>
                  <span style={{
                    padding: "1px 6px",
                    borderRadius: "999px",
                    background: tab === "pool" ? "var(--cyan, #8838dd)" : "var(--bg-surface-3, #344054)",
                    color: "#ffffff",
                    fontSize: "10px",
                    fontWeight: 700
                  }}>
                    {poolCount}/20
                  </span>
                </button>
                <button
                  className="tab-btn"
                  onClick={() => setTab(s.platform, "controls")}
                  style={{
                    flex: 1,
                    padding: "6px 8px",
                    borderRadius: "6px",
                    border: "none",
                    background: tab === "controls" ? "var(--bg-light, #ffffff)" : "transparent",
                    color: tab === "controls" ? "var(--cyan, #8838dd)" : "var(--text-primary, #ffffff)",
                    fontSize: "11px",
                    fontWeight: tab === "controls" ? 700 : 500,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    boxShadow: tab === "controls" ? "0 2px 6px rgba(0, 0, 0, 0.2)" : "none"
                  }}
                >
                  <span>⚡ Verification</span>
                </button>
              </div>

              {/* Tab 1 Content: Session Pool */}
              {tab === "pool" && (
                <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0, gap: "10px" }}>
                  {/* Capacity Gauge */}
                  <div style={{ flexShrink: 0 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--text-muted, #98a2b3)", marginBottom: "3px" }}>
                      <span>Rotation Capacity</span>
                      <span style={{ fontWeight: 600, color: "var(--text-body, #ffffff)" }}>
                        {poolCount}/20 Accounts
                      </span>
                    </div>
                    <div style={{ width: "100%", height: "4px", background: "var(--bg-app, #101828)", borderRadius: "2px", overflow: "hidden" }}>
                      <div style={{
                        height: "100%",
                        width: `${Math.min(100, (poolCount / 20) * 100)}%`,
                        background: "var(--primary, #8838dd)",
                        transition: "width 0.3s ease"
                      }} />
                    </div>
                  </div>

                  {/* Sessions Scroll Area */}
                  <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px", paddingRight: "2px" }}>
                    {(!s.sessions || s.sessions.length === 0) ? (
                      <div style={{
                        padding: "24px 12px",
                        textAlign: "center",
                        background: "var(--bg-surface-alt, #1d2939)",
                        borderRadius: "8px",
                        border: "1px dashed var(--border-color, #344054)",
                        margin: "auto 0"
                      }}>
                        <div style={{ fontSize: "20px", marginBottom: "6px" }}>🛡️</div>
                        <div style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-primary, #fff)" }}>No accounts saved</div>
                        <div style={{ fontSize: "11px", color: "var(--text-muted, #98a2b3)", margin: "3px auto 0 auto" }}>
                          Click <strong>"＋ Add"</strong> above to input cookies or keys.
                        </div>
                      </div>
                    ) : (
                      s.sessions.map((ss, index) => {
                        const isReady = ss.status === "ready";
                        const cooldown = cooldownLabel(ss.rate_limited_until);

                        const running = !!ss.in_use;

                        return (
                          <div
                            key={ss.id}
                            style={{
                              background: running ? "var(--bg-surface-3, #26344a)" : "var(--bg-surface-alt, #1d2939)",
                              border: running ? "1px solid var(--accent, #7c5cff)" : "1px solid var(--border-color, #344054)",
                              boxShadow: running ? "0 0 0 1px var(--accent, #7c5cff) inset" : "none",
                              borderRadius: "6px",
                              padding: "8px 10px",
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              gap: "8px",
                              flexShrink: 0
                            }}
                          >
                            <div style={{ minWidth: 0, flex: 1 }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                                <span style={{
                                  fontSize: "10px",
                                  fontWeight: 700,
                                  color: "var(--text-muted, #98a2b3)",
                                  background: "var(--bg-app, #101828)",
                                  padding: "1px 5px",
                                  borderRadius: "3px",
                                  border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))"
                                }}>
                                  #{index + 1}
                                </span>
                                <span title={ss.identifier || `Account ${index + 1}`} style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary, #fff)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {ss.identifier || `Account ${index + 1}`}
                                </span>
                                {running && (
                                  <span
                                    title="A job is using this session right now"
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "4px",
                                      fontSize: "9px",
                                      fontWeight: 700,
                                      color: "var(--accent, #7c5cff)",
                                      background: "rgba(124, 92, 255, 0.15)",
                                      border: "1px solid var(--accent, #7c5cff)",
                                      borderRadius: "10px",
                                      padding: "1px 7px",
                                      flexShrink: 0
                                    }}
                                  >
                                    <span style={{
                                      width: "6px", height: "6px", borderRadius: "50%",
                                      background: "var(--accent, #7c5cff)",
                                      animation: "runningPulse 1.2s ease-in-out infinite"
                                    }} />
                                    RUNNING NOW
                                  </span>
                                )}
                              </div>
                              <div style={{ fontSize: "11px", color: "var(--text-muted, #98a2b3)", marginTop: "2px", display: "flex", alignItems: "center", gap: "6px" }}>
                                <span>{ss.cookie_count > 0 ? `${ss.cookie_count} cookies` : s.kind === "api-key" || ss.is_api_key ? "API Key" : "Active"}</span>
                                {cooldown && <span style={{ color: "var(--text-secondary, #d8d8d8)" }}>• ⌛ {cooldown}</span>}
                                <span title="Total times this session has been picked for a job">• Used {ss.use_count ?? 0}×</span>
                              </div>
                            </div>

                            {/* Status & Actions Matching Live Discovery Violet & White Theme */}
                            <div style={{ display: "flex", alignItems: "center", gap: "6px", flexShrink: 0 }}>
                              <div style={{
                                padding: "3px 8px",
                                borderRadius: "4px",
                                fontSize: "10px",
                                fontWeight: 700,
                                display: "flex",
                                flexDirection: "column",
                                alignItems: "center",
                                background: isReady ? "var(--success, #12B76A)" : "var(--danger, #F04438)",
                                color: "#ffffff",
                                border: "1px solid var(--border-subtle, rgba(255,255,255,0.1))"
                              }}>
                                <div>LIVE · {isReady ? "ACTIVE" : "EXPIRED"}</div>
                                {ss.last_used > 0 && (
                                  <div style={{ fontSize: "8px", fontWeight: 500, marginTop: "1px", opacity: 0.9 }}>
                                    Last scrape: {new Date(ss.last_used * 1000).toLocaleString()}
                                  </div>
                                )}
                              </div>

                              <button
                                className="action-btn"
                                disabled={!!busyPlatform}
                                onClick={() => setModal({
                                  isOpen: true,
                                  mode: "update",
                                  platform: s,
                                  targetSession: { id: ss.id, identifier: ss.identifier, isApiKey: ss.is_api_key }
                                })}
                                style={{
                                  padding: "4px 8px",
                                  borderRadius: "5px",
                                  background: "var(--bg-surface-3, #344054)",
                                  border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
                                  color: "var(--text-body, #ffffff)",
                                  fontSize: "10px",
                                  fontWeight: 600,
                                  cursor: "pointer"
                                }}
                                title="Update account cookies or keywords"
                              >
                                Edit
                              </button>
                              <button
                                className="action-btn"
                                disabled={!!busyPlatform}
                                onClick={() => handleAction(() => sessionsApi.deleteSessionItem(s.platform, ss.id), s.platform)}
                                style={{
                                  padding: "4px 7px",
                                  borderRadius: "5px",
                                  background: "var(--bg-surface-3, #344054)",
                                  border: "1px solid var(--border-subtle, rgba(255,255,255,0.08))",
                                  color: "var(--text-primary, #ffffff)",
                                  fontSize: "10px",
                                  fontWeight: 600,
                                  cursor: "pointer"
                                }}
                                title="Delete account from pool"
                              >
                                ✕
                              </button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              )}

              {/* Tab 2 Content: Diagnostics & Controls */}
              {tab === "controls" && (
                <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "10px" }}>
                  {s.state === "checkpointed" && (
                    <div style={{ padding: "10px", background: "var(--bg-surface-alt, #1d2939)", borderRadius: "6px", border: "1px solid var(--border-color, #344054)", color: "var(--text-secondary, #d8d8d8)", fontSize: "11px" }}>
                      ⚠️ <strong>Checkpoint:</strong> {s.message || "Platform may require verification."}
                    </div>
                  )}

                  <div style={{ background: "var(--bg-surface-alt, #1d2939)", padding: "10px", borderRadius: "6px", border: "1px solid var(--border-color, #344054)" }}>
                    <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-primary, #fff)", marginBottom: "3px" }}>
                      🔍 Health Verification Status
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--text-muted, #98a2b3)" }}>
                      {s.last_verified ? `Last check: ${new Date(s.last_verified).toLocaleString()}` : "No verification sweep recorded yet."}
                    </div>
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "auto", paddingTop: "6px" }}>
                    {s.state !== "missing" && (
                      <button
                        className="action-btn"
                        disabled={!!busyPlatform}
                        onClick={() => handleAction(() => sessionsApi.checkSessionNow(s.platform), s.platform)}
                        style={{
                          width: "100%",
                          padding: "8px",
                          borderRadius: "6px",
                          background: "var(--bg-surface-3, #344054)",
                          border: "1px solid var(--border-color, #344054)",
                          color: "var(--text-primary, #fff)",
                          fontSize: "12px",
                          fontWeight: 600,
                          cursor: "pointer"
                        }}
                      >
                        {isBusy ? "Checking..." : "🔄 Verify Sweep Now"}
                      </button>
                    )}

                    {s.can_login && (
                      <button
                        className="action-btn"
                        disabled={!!busyPlatform}
                        onClick={() => handleAction(() => sessionsApi.launchLogin(s.platform), s.platform)}
                        style={{
                          width: "100%",
                          padding: "8px",
                          borderRadius: "6px",
                          background: "var(--primary, #8838dd)",
                          color: "var(--text-primary, #fff)",
                          border: "none",
                          fontSize: "12px",
                          fontWeight: 600,
                          cursor: "pointer"
                        }}
                      >
                        {isBusy ? "Launching..." : "🚀 Launch Login"}
                      </button>
                    )}

                    {s.state !== "missing" && (
                      <button
                        className="action-btn"
                        disabled={!!busyPlatform}
                        onClick={() => {
                          if (confirm(`Delete ALL accounts for ${s.name}?`)) {
                            handleAction(() => sessionsApi.deleteSessionPool(s.platform), s.platform);
                          }
                        }}
                        style={{
                          width: "100%",
                          padding: "8px",
                          borderRadius: "6px",
                          background: "var(--bg-surface-3, #344054)",
                          border: "1px solid var(--border-color, #344054)",
                          color: "var(--text-primary, #ffffff)",
                          fontSize: "12px",
                          fontWeight: 600,
                          cursor: "pointer"
                        }}
                      >
                        🗑️ Clear Pool ({poolCount})
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* MODAL DIALOG: CLEAN & SIMPLE TWO-FIELD FORM */}
      {modal.isOpen && modal.platform && (
        <SessionEditModal
          platform={modal.platform}
          mode={modal.mode}
          targetSession={modal.targetSession}
          onClose={() => setModal({ isOpen: false, mode: "create" })}
          onComplete={() => {
            setModal({ isOpen: false, mode: "create" });
            onChanged();
          }}
          onError={(err) => setGlobalError(err)}
        />
      )}
    </div>
  );
}

// Dedicated Modal Component for simple, two-field input
interface ModalProps {
  platform: SessionInfo;
  mode: "create" | "update";
  targetSession?: { id: string; identifier: string; isApiKey?: boolean };
  onClose: () => void;
  onComplete: () => void;
  onError: (msg: string) => void;
}

const SessionEditModal: FC<ModalProps> = ({ platform, mode, targetSession, onClose, onComplete, onError }) => {
  const isUpdate = mode === "update";
  const [identifier, setIdentifier] = useState<string>(targetSession?.identifier || "");
  const [cookieBlob, setCookieBlob] = useState<string>("");
  const [apiKey, setApiKey] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    try {
      if (isUpdate && targetSession) {
        await sessionsApi.updateSessionItem(platform.platform, targetSession.id, {
          identifier: identifier.trim(),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
          ...(cookieBlob.trim() ? { blob: cookieBlob.trim() } : {})
        });
      } else {
        if (platform.platform === "youtube" || platform.kind === "api-key") {
          await sessionsApi.saveApiKey(platform.platform, apiKey.trim(), identifier.trim() || "YouTube API Key");
        } else {
          await sessionsApi.saveCookies(platform.platform, cookieBlob.trim(), identifier.trim() || "Unnamed Account");
        }
      }
      onComplete();
    } catch (err) {
      onError((err as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const isApiKeyType = platform.platform === "youtube" || platform.kind === "api-key" || targetSession?.isApiKey;

  return (
    <div style={{
      position: "fixed",
      inset: 0,
      background: "rgba(8, 15, 30, 0.75)",
      backdropFilter: "blur(8px)",
      zIndex: 9999,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "20px",
      animation: "fadeIn 0.15s ease-out"
    }}>
      <div style={{
        background: "var(--bg-surface, #1e2837)",
        border: "1px solid var(--border-color, #344054)",
        borderRadius: "12px",
        width: "100%",
        maxWidth: "500px",
        padding: "24px",
        boxShadow: "0 20px 50px rgba(0, 0, 0, 0.65)",
        animation: "modalPopIn 0.2s cubic-bezier(0.16, 1, 0.3, 1)"
      }}>
        {/* Modal Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{
              width: "40px",
              height: "40px",
              borderRadius: "8px",
              background: "var(--bg-surface-alt, #1d2939)",
              border: "1px solid var(--border-color, #344054)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "20px"
            }}>
              {getPlatformIcon(platform.platform)}
            </span>
            <div>
              <h3 style={{ margin: 0, fontSize: "17px", fontWeight: 700, color: "var(--text-primary, #fff)" }}>
                {isUpdate ? `Update ${targetSession?.identifier}` : `Add Account — ${platform.name}`}
              </h3>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{ background: "transparent", border: "none", color: "var(--text-muted, #98a2b3)", cursor: "pointer", fontSize: "16px", fontWeight: 700 }}
          >
            ✕
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {/* Field 1: Identifier / Keywords */}
          <div>
            <label style={{ display: "block", fontSize: "13px", fontWeight: 600, color: "var(--text-body, #f2f4f7)", marginBottom: "6px" }}>
              Account Keywords / Identifier
            </label>
            <input
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder="e.g., Marketing Account #1"
              style={modalInputStyle}
              required={!isUpdate}
            />
          </div>

          {/* Field 2: Credentials */}
          <div>
            <label style={{ display: "block", fontSize: "13px", fontWeight: 600, color: "var(--text-body, #f2f4f7)", marginBottom: "6px" }}>
              {isApiKeyType ? "API Key" : "Cookies (JSON Array)"}
            </label>
            {isApiKeyType ? (
              <input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Paste API key here..."
                style={modalInputStyle}
                required={!isUpdate}
              />
            ) : (
              <textarea
                rows={6}
                value={cookieBlob}
                onChange={(e) => setCookieBlob(e.target.value)}
                placeholder={'Paste JSON cookies array e.g. [{"domain": ".facebook.com", ...}]'}
                style={{ ...modalInputStyle, resize: "vertical", fontFamily: "monospace", fontSize: "12px", lineHeight: "1.4" }}
                required={!isUpdate}
              />
            )}
          </div>

          {/* Actions */}
          <div style={{ display: "flex", gap: "10px", marginTop: "8px", justifyContent: "flex-end" }}>
            <button
              type="button"
              onClick={onClose}
              className="action-btn"
              style={{
                padding: "9px 16px",
                borderRadius: "8px",
                background: "var(--bg-surface-3, #344054)",
                border: "1px solid var(--border-color, #344054)",
                color: "var(--text-primary, #fff)",
                fontSize: "13px",
                fontWeight: 600,
                cursor: "pointer"
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="action-btn"
              style={{
                padding: "9px 20px",
                borderRadius: "8px",
                background: "var(--primary, #8838dd)",
                border: "1px solid var(--border-subtle, rgba(255,255,255,0.1))",
                color: "var(--text-primary, #fff)",
                fontSize: "13px",
                fontWeight: 600,
                cursor: "pointer"
              }}
            >
              {isSubmitting ? "Saving..." : "Save Session"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

const modalInputStyle: CSSProperties = {
  width: "100%",
  background: "var(--bg-app, #101828)",
  border: "1px solid var(--border-color, #344054)",
  borderRadius: "8px",
  color: "var(--text-primary, #fff)",
  fontSize: "13px",
  padding: "10px 12px",
  outline: "none",
  boxSizing: "border-box"
};
