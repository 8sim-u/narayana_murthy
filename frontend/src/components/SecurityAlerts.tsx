import { useCallback, useEffect, useState } from "react";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

interface SecurityAlert {
  id: number | null;
  event_type: string;
  severity: "high" | "medium" | "low";
  reason: string;
  machine_id: string;
  employee_id: string;
  timestamp: string;
  detail: Record<string, unknown> | null;
}

const REFRESH_MS = 15000;

function severityStyle(eventType: string, severity: string): { background: string; color: string } {
  // USB insert/remove: red. After-hours: amber. Write bursts: orange.
  if (eventType === "usb_insert" || eventType === "usb_remove") {
    return { background: "#fde8e8", color: "#b42318" };
  }
  if (eventType === "after_hours_activity") {
    return { background: "#fef6e0", color: "#935705" };
  }
  if (eventType === "file_write_burst") {
    return { background: "#fff1e6", color: "#b54708" };
  }
  return severity === "high"
    ? { background: "#fde8e8", color: "#b42318" }
    : { background: "#fef6e0", color: "#935705" };
}

function formatDetail(detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  if (typeof detail.device === "string") return `Device: ${detail.device}`;
  if (typeof detail.bytes_written_mb === "number") {
    return `${detail.bytes_written_mb.toFixed(1)} MB written`;
  }
  return "";
}

export default function SecurityAlerts() {
  const [alerts, setAlerts] = useState<SecurityAlert[]>([]);
  const [machineId, setMachineId] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (machineId.trim()) params.set("machine_id", machineId.trim());
    if (employeeId.trim()) params.set("employee_id", employeeId.trim());
    const url = `${API_URL}/security/alerts${params.toString() ? `?${params.toString()}` : ""}`;
    try {
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Request failed (${res.status})`);
      }
      const data = (await res.json()) as SecurityAlert[];
      setAlerts(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load security alerts");
    } finally {
      setLoading(false);
    }
  }, [machineId, employeeId]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => {
      void load();
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const highCount = alerts.filter((a) => a.event_type === "usb_insert" || a.event_type === "usb_remove").length;

  return (
    <section className="panel" aria-label="Security alerts">
      <div className="panel-header">
        <h2>Security Alerts</h2>
        <span className="alert-count">
          {alerts.length} alert{alerts.length === 1 ? "" : "s"}
          {highCount > 0 ? ` (${highCount} USB)` : ""}
        </span>
      </div>

      <div className="filters">
        <label>
          Machine
          <input
            type="text"
            value={machineId}
            onChange={(e) => setMachineId(e.target.value)}
            placeholder="e.g. WS-1042"
          />
        </label>
        <label>
          Employee
          <input
            type="text"
            value={employeeId}
            onChange={(e) => setEmployeeId(e.target.value)}
            placeholder="e.g. EMP-007"
          />
        </label>
      </div>

      {loading && <p className="muted">Loading alerts...</p>}
      {error && <p className="error">{error}</p>}
      {!loading && !error && alerts.length === 0 && (
        <p className="muted">No security anomalies in the current window.</p>
      )}

      <ul className="alert-list">
        {alerts.map((a, i) => {
          const style = severityStyle(a.event_type, a.severity);
          return (
            <li key={a.id ?? `${a.timestamp}-${i}`} className="alert-item" style={{ borderLeftColor: style.color }}>
              <span className="alert-badge" style={{ background: style.background, color: style.color }}>
                {a.event_type.replace(/_/g, " ").toUpperCase()}
              </span>
              <div className="alert-body">
                <p className="alert-reason">{a.reason}</p>
                <p className="alert-meta">
                  {a.machine_id} &middot; {a.employee_id} &middot;{" "}
                  {new Date(a.timestamp).toLocaleString()} {formatDetail(a.detail)}
                </p>
              </div>
            </li>
          );
        })}
      </ul>

      <p className="muted thresholds-note">
        Signals: USB insert/remove (always flagged), after-hours activity (outside
        WAMSS_WORK_HOURS), and file write bursts above WAMSS_DLP_WRITE_THRESHOLD_MB
        per interval. Workstation agents report byte counts only; no file or keystroke
        content is ever captured. Refreshes every 15s.
      </p>
    </section>
  );
}
