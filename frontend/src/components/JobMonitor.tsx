import type { Job, JobEvent } from "../api";

interface Props {
  job: Job;
  log: JobEvent[];
  onCancel: () => void;
}

export function JobMonitor({ job, log, onCancel }: Props) {
  const pct = job.total
    ? Math.min(100, Math.round((job.found / job.total) * 100))
    : 0;
  const running = !["done", "failed", "cancelled"].includes(job.status);
  const incomplete = job.message.includes("INCOMPLETE");

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-glow)",
        borderRadius: "14px",
        padding: "16px 20px",
        marginBottom: "20px",
        boxShadow: "0 8px 30px rgba(136, 56, 221,0.1)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            padding: "4px 10px",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 700,
            fontFamily: "var(--font-mono)",
            background: running
              ? "rgba(136, 56, 221, 0.15)"
              : job.status === "done"
                ? "rgba(0, 193, 77, 0.15)"
                : "rgba(233, 80, 83, 0.15)",
            color: running
              ? "var(--cyan-bright)"
              : job.status === "done"
                ? "var(--success)"
                : "var(--danger)",
          }}
        >
          {job.kind.toUpperCase()} · {job.status.toUpperCase()}
        </span>

        <span
          style={{
            fontSize: "13px",
            color: "var(--text-muted)",
            flex: 1,
            fontFamily: "var(--font-mono)",
          }}
        >
          {job.message}
        </span>

        {running && (
          <button
            onClick={onCancel}
            style={{
              background: "rgba(233, 80, 83,0.1)",
              border: "1px solid rgba(233, 80, 83,0.25)",
              color: "var(--danger)",
              padding: "6px 12px",
              borderRadius: "8px",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            ✕ Cancel Job
          </button>
        )}
      </div>

      {running && (
        <div
          style={{
            height: "6px",
            background: "var(--bg-inner)",
            borderRadius: "999px",
            overflow: "hidden",
            marginTop: "12px",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${pct || 10}%`,
              background: "linear-gradient(90deg, var(--cyan), var(--purple))",
              transition: "width 0.4s ease",
            }}
          />
        </div>
      )}

      {incomplete && (
        <div
          style={{
            fontSize: "12px",
            color: "var(--warning)",
            marginTop: "8px",
          }}
        >
          ⚠️ Sweep reached maximum budget before completing. More profiles may
          exist.
        </div>
      )}

      {job.error && (
        <div
          style={{ fontSize: "12px", color: "var(--danger)", marginTop: "8px" }}
        >
          ❌ {job.error}
        </div>
      )}

      {log.length > 0 && (
        <details
          style={{
            marginTop: "12px",
            fontSize: "12px",
            color: "var(--text-muted)",
          }}
        >
          <summary
            style={{ cursor: "pointer", fontFamily: "var(--font-mono)" }}
          >
            View {log.length} live stream events
          </summary>
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              marginTop: "8px",
              maxHeight: "160px",
              overflowY: "auto",
              fontFamily: "var(--font-mono)",
              fontSize: "11px",
            }}
          >
            {log.slice(-30).map((e, i) => (
              <li
                key={i}
                style={{
                  padding: "3px 0",
                  borderBottom: "1px solid var(--border-subtle)",
                  display: "flex",
                  gap: "8px",
                }}
              >
                <span style={{ color: "var(--cyan)" }}>[{e.type}]</span>
                <span style={{ color: "var(--text-main)" }}>{e.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
