import { useState } from "react";
import { sessionsApi } from "../api/sessionsApi";
import type { SessionInfo } from "../api/types";

interface Props {
  sessions: SessionInfo[];
  onChanged: () => void;
}

export function SessionPanel({ sessions, onChanged }: Props) {
  const [openCookiePlatform, setOpenCookiePlatform] = useState<string>("");
  const [cookieBlob, setCookieBlob] = useState<string>("");
  const [cookieIdentifier, setCookieIdentifier] = useState<string>("");
  const [busyPlatform, setBusyPlatform] = useState<string>("");
  const [errorNote, setErrorNote] = useState<string>("");
  const [openYtKey, setOpenYtKey] = useState<boolean>(false);
  const [ytApiKey, setYtApiKey] = useState<string>("");
  // Per-session proxy input, keyed by session id -- a 100-session pool can't
  // reasonably get one text field each rendered permanently, so this is only
  // populated for whichever session's proxy row is currently being edited.
  const [proxyDraft, setProxyDraft] = useState<Record<string, string>>({});
  const [proxyBusy, setProxyBusy] = useState<string>("");

  const handleAction = async (fn: () => Promise<unknown>, platform: string) => {
    setBusyPlatform(platform);
    setErrorNote("");
    try {
      await fn();
      onChanged();
    } catch (e) {
      setErrorNote((e as Error).message);
    } finally {
      setBusyPlatform("");
    }
  };

  const getPlatformBadge = (s: SessionInfo) => {
    if (s.state === "ready") {
      return {
        label: "ACTIVE",
        bg: "rgba(0, 193, 77, 0.12)",
        color: "var(--success)",
        border: "rgba(0, 193, 77, 0.22)",
      };
    }
    return {
      label: s.state.toUpperCase(),
      bg: "rgba(233, 80, 83, 0.12)",
      color: "var(--danger)",
      border: "rgba(233, 80, 83, 0.22)",
    };
  };

  return (
    <div style={{ animation: "fadeUp 0.4s ease" }}>
      <h2 style={{ fontSize: "22px", fontWeight: 700, marginBottom: "4px" }}>
        🔐 Session &amp; Authentication Manager
      </h2>
      <p
        style={{
          fontSize: "13px",
          color: "var(--text-muted)",
          marginBottom: "22px",
        }}
      >
        Authenticate live platform sessions — choose interactive browser login,
        paste cookie JSON, or enter API credentials.
      </p>

      {errorNote && (
        <div
          style={{
            background: "rgba(233, 80, 83,0.1)",
            border: "1px solid rgba(233, 80, 83,0.25)",
            color: "var(--danger)",
            borderRadius: "10px",
            padding: "10px 14px",
            marginBottom: "16px",
            fontSize: "13px",
          }}
        >
          ⚠️ {errorNote}
        </div>
      )}

      <div className="sessions-grid">
        {sessions.map((s) => {
          const badge = getPlatformBadge(s);
          const isBusy = busyPlatform === s.platform;
          const isCookieOpen = openCookiePlatform === s.platform;

          return (
            <div key={s.platform} className="session-card">
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "11px",
                  marginBottom: "14px",
                }}
              >
                <span
                  style={{
                    width: "36px",
                    height: "36px",
                    borderRadius: "10px",
                    background: "var(--bg-inner)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "18px",
                  }}
                >
                  {s.platform === "facebook"
                    ? "📘"
                    : s.platform === "instagram"
                      ? "📸"
                      : s.platform === "twitter"
                        ? "𝕏"
                        : s.platform === "youtube"
                          ? "▶️"
                          : s.platform === "linkedin"
                            ? "💼"
                            : "✈️"}
                </span>

                <span
                  style={{
                    fontSize: "16px",
                    fontWeight: 700,
                    flex: 1,
                    color: "#fff",
                  }}
                >
                  {s.name}
                </span>

                <span
                  style={{
                    fontSize: "10px",
                    fontWeight: 700,
                    padding: "3px 9px",
                    borderRadius: "999px",
                    background: badge.bg,
                    color: badge.color,
                    border: `1px solid ${badge.border}`,
                  }}
                >
                  {badge.label}
                </span>
              </div>

              {s.state === "ready" && (
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-muted)",
                    marginBottom: "6px",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  🕐 Cookies loaded ({s.cookie_count}) · {(s.sessions?.length || 0)} sessions active
                </div>
              )}
              {s.sessions && s.sessions.length > 0 && (
                <div style={{ marginBottom: "10px", fontSize: "11px", color: "var(--text-dim)" }}>
                  {s.sessions.map(ss => (
                    <div key={ss.id} style={{ marginBottom: "6px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>👤 {ss.identifier}</span>
                        <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                          {ss.proxy_host && (
                            <span title="Proxy assigned" style={{ color: "var(--text-dim)" }}>
                              🌐 {ss.proxy_host}
                            </span>
                          )}
                          <span style={{ color: ss.status === "ready" ? "var(--success)" : "var(--danger)" }}>{ss.status}</span>
                        </span>
                      </div>
                      {s.kind === "cookies" && (
                        <div style={{ display: "flex", gap: "4px", marginTop: "3px" }}>
                          <input
                            value={proxyDraft[ss.id] ?? ""}
                            onChange={(e) =>
                              setProxyDraft((d) => ({ ...d, [ss.id]: e.target.value }))
                            }
                            placeholder="proxy e.g. http://user:pass@host:port"
                            style={{
                              flex: 1,
                              background: "var(--bg-inner)",
                              border: "1px solid var(--border-color)",
                              borderRadius: "6px",
                              color: "var(--text-main)",
                              fontSize: "10px",
                              padding: "4px 6px",
                              outline: "none",
                            }}
                          />
                          <button
                            disabled={!proxyDraft[ss.id]?.trim() || proxyBusy === ss.id}
                            title="Assign this proxy to this one session"
                            onClick={async () => {
                              setProxyBusy(ss.id);
                              setErrorNote("");
                              try {
                                const raw = proxyDraft[ss.id].trim();
                                let server = raw;
                                let username = "";
                                let password = "";
                                const m = raw.match(/^(\w+:\/\/)(?:([^:@]+):([^@]+)@)?(.+)$/);
                                if (m) {
                                  server = `${m[1]}${m[4]}`;
                                  username = m[2] || "";
                                  password = m[3] || "";
                                }
                                await sessionsApi.setSessionProxy(s.platform, ss.id, {
                                  server,
                                  username,
                                  password,
                                });
                                setProxyDraft((d) => ({ ...d, [ss.id]: "" }));
                                onChanged();
                              } catch (e) {
                                setErrorNote((e as Error).message);
                              } finally {
                                setProxyBusy("");
                              }
                            }}
                            style={{
                              padding: "4px 8px",
                              borderRadius: "6px",
                              fontSize: "10px",
                              fontWeight: 600,
                              cursor: "pointer",
                              background: "var(--bg-inner)",
                              color: "var(--text-main)",
                              border: "1px solid var(--border-color)",
                            }}
                          >
                            Set
                          </button>
                          {ss.proxy_host && (
                            <button
                              disabled={proxyBusy === ss.id}
                              title="Clear this session's proxy"
                              onClick={async () => {
                                setProxyBusy(ss.id);
                                setErrorNote("");
                                try {
                                  await sessionsApi.clearSessionProxy(s.platform, ss.id);
                                  onChanged();
                                } catch (e) {
                                  setErrorNote((e as Error).message);
                                } finally {
                                  setProxyBusy("");
                                }
                              }}
                              style={{
                                padding: "4px 8px",
                                borderRadius: "6px",
                                fontSize: "10px",
                                fontWeight: 600,
                                cursor: "pointer",
                                background: "rgba(233, 80, 83,0.08)",
                                color: "var(--danger)",
                                border: "1px solid rgba(233, 80, 83,0.18)",
                              }}
                            >
                              Clear
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {s.state === "checkpointed" && (
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--danger)",
                    marginBottom: "6px",
                  }}
                >
                  ⚠️ The cookies look valid, but a live check found the
                  platform rejected the session
                  {s.message ? ` (${s.message})` : ""}. Re-run the login below.
                </div>
              )}

              {s.last_verified && (
                <div
                  style={{
                    fontSize: "10px",
                    color: "var(--text-dim)",
                    marginBottom: "12px",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  last live-checked: {new Date(s.last_verified).toLocaleString()}
                </div>
              )}

              <div
                style={{ display: "flex", flexDirection: "column", gap: "8px" }}
              >
                {s.state !== "missing" && (
                  <button
                    disabled={!!busyPlatform}
                    onClick={() =>
                      handleAction(
                        () => sessionsApi.checkSessionNow(s.platform),
                        s.platform,
                      )
                    }
                    title="Runs the platform's own live check right now instead of waiting for the periodic sweep"
                    style={{
                      width: "100%",
                      padding: "9px",
                      borderRadius: "9px",
                      fontSize: "12px",
                      fontWeight: 600,
                      cursor: "pointer",
                      background: "var(--bg-inner)",
                      color: "var(--text-main)",
                      border: "1px solid var(--border-color)",
                    }}
                  >
                    {isBusy ? "Checking…" : "🔄 Check Session Now"}
                  </button>
                )}

                {s.can_login && (
                  <button
                    disabled={!!busyPlatform}
                    onClick={() =>
                      handleAction(
                        () => sessionsApi.launchLogin(s.platform),
                        s.platform,
                      )
                    }
                    style={{
                      width: "100%",
                      padding: "10px",
                      borderRadius: "9px",
                      fontSize: "12px",
                      fontWeight: 700,
                      cursor: "pointer",
                      background:
                        "linear-gradient(135deg, var(--cyan), var(--cyan-bright))",
                      color: "#000",
                      border: "none",
                    }}
                  >
                    {isBusy
                      ? "Launching Browser…"
                      : `🚀 Launch Interactive Browser Login`}
                  </button>
                )}

                {s.kind === "cookies" && (
                  <button
                    onClick={() =>
                      setOpenCookiePlatform(isCookieOpen ? "" : s.platform)
                    }
                    style={{
                      width: "100%",
                      padding: "9px",
                      borderRadius: "9px",
                      fontSize: "12px",
                      fontWeight: 600,
                      cursor: "pointer",
                      background: "var(--bg-inner)",
                      color: "var(--text-main)",
                      border: "1px solid var(--border-color)",
                    }}
                  >
                    🍪{" "}
                    {isCookieOpen
                      ? "Close Cookie Importer"
                      : "Paste Cookies JSON"}
                  </button>
                )}

                {s.platform === "youtube" && (
                  <button
                    onClick={() => setOpenYtKey(!openYtKey)}
                    style={{
                      width: "100%",
                      padding: "9px",
                      borderRadius: "9px",
                      fontSize: "12px",
                      fontWeight: 600,
                      cursor: "pointer",
                      background: "var(--bg-inner)",
                      color: "var(--text-main)",
                      border: "1px solid var(--border-color)",
                    }}
                  >
                    🔑 YouTube Data API Key
                  </button>
                )}

                {s.platform === "telegram" && (
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-dim)",
                      padding: "8px 10px",
                      background: "var(--bg-inner)",
                      borderRadius: "9px",
                      border: "1px solid var(--border-color)",
                    }}
                  >
                    📱 Telegram has no in-app login flow on this backend --
                    set TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_PHONE
                    in <code>.env</code> and generate the session file with
                    the CLI, then restart the backend.
                  </div>
                )}

                {s.state !== "missing" && (
                  <button
                    disabled={!!busyPlatform}
                    onClick={() =>
                      handleAction(
                        () => sessionsApi.deleteSessionPool(s.platform),
                        s.platform,
                      )
                    }
                    style={{
                      width: "100%",
                      padding: "8px",
                      borderRadius: "9px",
                      fontSize: "11px",
                      fontWeight: 600,
                      cursor: "pointer",
                      background: "rgba(233, 80, 83,0.08)",
                      color: "var(--danger)",
                      border: "1px solid rgba(233, 80, 83,0.18)",
                    }}
                  >
                    🗑️ Delete Stored Session
                  </button>
                )}
              </div>

              {/* Cookie JSON paste drawer */}
              {isCookieOpen && (
                <div
                  style={{
                    marginTop: "12px",
                    paddingTop: "12px",
                    borderTop: "1px solid var(--border-subtle)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "8px",
                  }}
                >
                  <input
                    value={cookieIdentifier}
                    onChange={(e) => setCookieIdentifier(e.target.value)}
                    placeholder="Session Identifier (e.g. Account123)"
                    style={{
                      width: "100%",
                      background: "var(--bg-inner)",
                      border: "1px solid var(--border-color)",
                      borderRadius: "9px",
                      color: "var(--text-main)",
                      fontSize: "12px",
                      padding: "9px 10px",
                      outline: "none",
                    }}
                  />
                  <textarea
                    rows={4}
                    value={cookieBlob}
                    onChange={(e) => setCookieBlob(e.target.value)}
                    placeholder="Paste cookies JSON array export here…"
                    style={{
                      width: "100%",
                      background: "var(--bg-inner)",
                      border: "1px solid var(--border-color)",
                      borderRadius: "9px",
                      color: "var(--text-main)",
                      fontFamily: "var(--font-mono)",
                      fontSize: "11px",
                      padding: "9px",
                      resize: "vertical",
                      outline: "none",
                    }}
                  />
                  <button
                    disabled={!cookieBlob.trim() || !!busyPlatform}
                    onClick={() =>
                      handleAction(async () => {
                        await sessionsApi.saveCookies(s.platform, cookieBlob, cookieIdentifier);
                        setCookieBlob("");
                        setCookieIdentifier("");
                        setOpenCookiePlatform("");
                      }, s.platform)
                    }
                    style={{
                      width: "100%",
                      marginTop: "8px",
                      padding: "9px",
                      borderRadius: "9px",
                      fontSize: "12px",
                      fontWeight: 700,
                      cursor: "pointer",
                      background:
                        "linear-gradient(135deg, var(--cyan), var(--cyan-bright))",
                      color: "#000",
                      border: "none",
                    }}
                  >
                    💾 Save Cookies Array
                  </button>
                </div>
              )}

              {/* YouTube Key drawer */}
              {s.platform === "youtube" && openYtKey && (
                <div
                  style={{
                    marginTop: "12px",
                    paddingTop: "12px",
                    borderTop: "1px solid var(--border-subtle)",
                  }}
                >
                  <input
                    value={ytApiKey}
                    onChange={(e) => setYtApiKey(e.target.value)}
                    placeholder="Paste YouTube Data API key…"
                    style={{
                      width: "100%",
                      background: "var(--bg-inner)",
                      border: "1px solid var(--border-color)",
                      borderRadius: "9px",
                      color: "var(--text-main)",
                      fontSize: "12px",
                      padding: "9px 10px",
                      outline: "none",
                    }}
                  />
                  <button
                    disabled={!ytApiKey.trim() || !!busyPlatform}
                    onClick={() =>
                      handleAction(async () => {
                        await sessionsApi.saveApiKey("youtube", ytApiKey.trim());
                        setYtApiKey("");
                        setOpenYtKey(false);
                      }, "youtube")
                    }
                    style={{
                      width: "100%",
                      marginTop: "8px",
                      padding: "9px",
                      borderRadius: "9px",
                      fontSize: "12px",
                      fontWeight: 700,
                      cursor: "pointer",
                      background:
                        "linear-gradient(135deg, var(--cyan), var(--cyan-bright))",
                      color: "#000",
                      border: "none",
                    }}
                  >
                    💾 Save YouTube API Key
                  </button>
                </div>
              )}

            </div>
          );
        })}
      </div>
    </div>
  );
}
