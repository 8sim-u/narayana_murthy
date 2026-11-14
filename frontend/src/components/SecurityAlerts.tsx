import { useCallback, useEffect, useState } from "react";
import {
  Chip, EmptyState, ErrorNotice, Field, Reveal,
  SectionHead, SkeletonRows,
} from "./ui";

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

function severityTone(severity: string): "bad" | "warn" | "idle" {
  if (severity === "high") return "bad";
  if (severity === "medium") return "warn";
  return "idle";
}

function formatDetail(detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  if (typeof detail.device === "string") return `Device: ${detail.device}`;
  if (typeof detail.bytes_written_mb === "number") {
    return `${detail.bytes_written_mb.toFixed(1)} MB written`;
  }
  return "";
}

export default function SecurityAlerts({ officeId = "" }: { officeId?: string }) {
  const [alerts, setAlerts] = useState<SecurityAlert[]>([]);
  const [machineId, setMachineId] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (machineId.trim()) params.set("machine_id", machineId.trim());
    if (employeeId.trim()) params.set("employee_id", employeeId.trim());
    if (officeId) params.set("office_id", officeId);
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
  }, [machineId, employeeId, officeId]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => {
      void load();
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const highCount = alerts.filter((a) => a.severity === "high").length;

  return (
    <div className="rise">
      <Reveal>
        <section className="panel" aria-label="Security alerts">
          <SectionHead
            title="Anomaly Feed"
            meta={`${alerts.length} alert${alerts.length === 1 ? "" : "s"}${highCount > 0 ? ` · ${highCount} high severity` : ""} · refresh ${REFRESH_MS / 1000}s`}
            actions={
              <div className="form-row">
                <Field label="Machine">
                  <input
                    className="input"
                    type="text"
                    value={machineId}
                    onChange={(e) => setMachineId(e.target.value)}
                    placeholder="e.g. WS-1042"
                    autoComplete="off"
                    spellCheck={false}
                    style={{ width: 150 }}
                  />
                </Field>
                <Field label="Employee">
                  <input
                    className="input"
                    type="text"
                    value={employeeId}
                    onChange={(e) => setEmployeeId(e.target.value)}
                    placeholder="e.g. EMP-007"
                    autoComplete="off"
                    spellCheck={false}
                    style={{ width: 150 }}
                  />
                </Field>
              </div>
            }
          />

          {error && (
            <ErrorNotice
              message={`Could not load security alerts: ${error}`}
              onRetry={() => { void load(); }}
            />
          )}

          {loading && alerts.length === 0 ? (
            <SkeletonRows rows={4} height={52} />
          ) : alerts.length === 0 && !error ? (
            <EmptyState
              title="No security anomalies"
              body="The feed is quiet. USB events, after-hours activity and write bursts will surface here as the workstation agents report them."
            />
          ) : (
            <div className="table-wrap">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Severity</th>
                    <th>Trigger</th>
                    <th>What happened</th>
                    <th>Machine</th>
                    <th>Employee</th>
                    <th>Source detail</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((a, i) => (
                    <tr
                      key={a.id ?? `${a.timestamp}-${i}`}
                      className={a.severity === "high" ? "row-bad" : a.severity === "medium" ? "row-alert" : undefined}
                    >
                      <td className="num">{new Date(a.timestamp).toLocaleString()}</td>
                      <td><Chip tone={severityTone(a.severity)}>{a.severity}</Chip></td>
                      <td className="id">{a.event_type}</td>
                      <td>{a.reason}</td>
                      <td className="id">{a.machine_id}</td>
                      <td className="id">{a.employee_id}</td>
                      <td className="num">
                        {a.id !== null ? `agent event #${a.id}` : "agent event"}
                        {formatDetail(a.detail) ? ` · ${formatDetail(a.detail)}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p style={{ color: "var(--text-2)", fontSize: 13, margin: "16px 0 0", maxWidth: "80ch" }}>
            Every row names its source: the workstation agent event that raised it, with
            metadata such as device label or megabytes written. Signals: USB insert and
            remove (always flagged), after-hours activity (outside WAMSS_WORK_HOURS), and
            file write bursts above WAMSS_DLP_WRITE_THRESHOLD_MB per interval.
            Workstation agents report byte counts only; no file or keystroke content is
            ever captured.
          </p>
        </section>
      </Reveal>
    </div>
  );
}
