// Lets a non-technical admin configure where incident/session-expiry alert
// emails go, without touching a .env file on the server. Reads/writes
// backend/api/settings_routes.py; persistence goes through write_env() on
// the backend so a value entered here survives a process restart.
import { useEffect, useState } from "react";
import { settingsApi } from "../api/settingsApi";

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bg-inner, #101828)",
  border: "1px solid var(--border-color, #344054)",
  borderRadius: "10px",
  padding: "10px 12px",
  color: "var(--text-main, #f2f4f7)",
  fontSize: "13px",
  outline: "none",
};

const labelStyle: React.CSSProperties = {
  fontSize: "12px",
  fontWeight: 600,
  color: "var(--text-muted, #98a2b3)",
  marginBottom: "6px",
  display: "block",
};

export function MailPanel() {
  const [loading, setLoading] = useState(true);
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPass, setSmtpPass] = useState("");
  const [passSet, setPassSet] = useState(false);
  const [emailsText, setEmailsText] = useState("");
  const [alertFrom, setAlertFrom] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = () => {
    setLoading(true);
    settingsApi
      .getMailSettings()
      .then((s) => {
        setSmtpHost(s.smtp_host);
        setSmtpPort(s.smtp_port);
        setSmtpUser(s.smtp_user);
        setPassSet(s.smtp_pass_set);
        setEmailsText(s.alert_emails.join(", "));
        setAlertFrom(s.alert_from);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const save = async () => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const emails = emailsText.split(/[,\n]/).map((e) => e.trim()).filter(Boolean);
      const updated = await settingsApi.updateMailSettings({
        smtp_host: smtpHost.trim(),
        smtp_port: smtpPort,
        smtp_user: smtpUser.trim(),
        smtp_pass: smtpPass,
        alert_emails: emails,
        alert_from: alertFrom.trim(),
      });
      setPassSet(updated.smtp_pass_set);
      setSmtpPass("");
      setNotice("Saved. Alert emails will use these settings immediately -- no restart needed.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    setTesting(true);
    setError("");
    setNotice("");
    try {
      const res = await settingsApi.sendTestEmail();
      if (res.sent) setNotice(`✅ Test email sent -- ${res.detail}`);
      else setError(res.detail || "Test email failed");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return <div style={{ padding: "24px", color: "var(--text-dim)" }}>Loading mail settings…</div>;
  }

  return (
    <div style={{ padding: "24px", color: "var(--text-main, #f2f4f7)", maxWidth: "640px", margin: "0 auto" }}>
      <div style={{ marginBottom: "20px" }}>
        <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary, #fff)", margin: 0, letterSpacing: "-0.3px" }}>
          📧 Mail Alerts
        </h1>
        <p style={{ fontSize: "13px", color: "var(--text-muted, #98a2b3)", margin: "4px 0 0 0" }}>
          Where the tool sends an email when a platform session expires, a scraper breaks, or the
          automatic engine has to pause. No .env editing required -- saving here takes effect right away.
        </p>
      </div>

      {error && (
        <div style={{
          padding: "10px 16px", background: "rgba(233, 80, 83,0.1)", border: "1px solid rgba(233, 80, 83,0.25)",
          color: "var(--danger)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
        }}>
          ⚠️ {error}
        </div>
      )}
      {notice && (
        <div style={{
          padding: "10px 16px", background: "rgba(54, 181, 160,0.1)", border: "1px solid rgba(54, 181, 160,0.25)",
          color: "var(--success, #36b5a0)", borderRadius: "10px", marginBottom: "16px", fontSize: "13px",
        }}>
          {notice}
        </div>
      )}

      <div style={{
        background: "var(--bg-surface, #1e2837)", border: "1px solid var(--border-color, #344054)",
        borderRadius: "12px", padding: "20px", display: "flex", flexDirection: "column", gap: "16px",
      }}>
        <div>
          <label style={labelStyle}>Notify these email addresses (comma or line separated)</label>
          <textarea
            value={emailsText}
            onChange={(e) => setEmailsText(e.target.value)}
            placeholder="ops@yourcompany.com, analyst@yourcompany.com"
            rows={2}
            style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }}
          />
        </div>

        <div>
          <label style={labelStyle}>Send-from address</label>
          <input value={alertFrom} onChange={(e) => setAlertFrom(e.target.value)} placeholder="alerts@yourcompany.com" style={inputStyle} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "12px" }}>
          <div>
            <label style={labelStyle}>SMTP host</label>
            <input value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.gmail.com" style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Port</label>
            <input
              type="number"
              value={smtpPort}
              onChange={(e) => setSmtpPort(Number(e.target.value) || 587)}
              style={inputStyle}
            />
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
          <div>
            <label style={labelStyle}>SMTP username</label>
            <input value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>
              SMTP password {passSet && <span style={{ color: "var(--text-dim)" }}>(saved — leave blank to keep it)</span>}
            </label>
            <input
              type="password"
              value={smtpPass}
              onChange={(e) => setSmtpPass(e.target.value)}
              placeholder={passSet ? "••••••••" : ""}
              style={inputStyle}
            />
          </div>
        </div>

        <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
          <button
            onClick={save}
            disabled={saving}
            className="btn-cyber-primary"
            style={{ flex: 1, padding: "10px", borderRadius: "10px", cursor: saving ? "wait" : "pointer" }}
          >
            {saving ? "Saving…" : "💾 Save"}
          </button>
          <button
            onClick={sendTest}
            disabled={testing}
            style={{
              flex: 1, padding: "10px", borderRadius: "10px", cursor: testing ? "wait" : "pointer",
              background: "var(--bg-inner)", border: "1px solid var(--border-color)", color: "var(--text-main)",
              fontSize: "13px", fontWeight: 600,
            }}
          >
            {testing ? "Sending…" : "✉️ Send test email"}
          </button>
        </div>
      </div>
    </div>
  );
}
