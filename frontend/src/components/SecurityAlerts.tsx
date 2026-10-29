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

function eventTone(eventType: string, severity: string): "bad" | "warn" | "idle" {
  // USB insert/remove: red. After-hours: amber. Write bursts: orange.
  if (eventType === "usb_insert" || eventType === "usb_remove") return "bad";
  if (eventType === "after_hours_activity") return "warn";
  if (eventType === "file_write_burst") return "warn";
  return severity === "high" ? "bad" : "warn";
}

function railClass(eventType: string, severity: string): string {
  const t = eventTone(eventType, severity);
  return t === "bad" ? "alert-rail-bad" : t === "warn" ? "alert-rail-warn" : "alert-rail-idle";
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

  const highCount = alerts.filter((a) => a.event_type === "usb_insert" || a.event_type === "usb_remove").length;

  return (
    <div className="rise">
      <Reveal>
        <section className="panel" aria-label="Security alerts">
          <SectionHead
            title="Anomaly Feed"
            meta={`${alerts.length} alert${alerts.length === 1 ? "" : "s"}${highCount > 0 ? ` · ${highCount} USB` : ""} · refresh ${REFRESH_MS / 1000}s`}
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
            <SkeletonRows rows={4} height={72} />
          ) : alerts.length === 0 && !error ? (
            <EmptyState
              title="No security anomalies"
              body="The feed is quiet. USB events, after-hours activity and write bursts will surface here."
            />
          ) : (
            <ul className="alert-feed">
              {alerts.map((a, i) => (
                <li
                  key={a.id ?? `${a.timestamp}-${i}`}
                  className={`alert-row-card ${railClass(a.event_type, a.severity)} rise-stagger`}
                  style={{ "--i": Math.min(i, 16) } as React.CSSProperties}
                >
                  <Chip tone={eventTone(a.event_type, a.severity)}>
                    {a.event_type.replace(/_/g, " ").toUpperCase()}
                  </Chip>
                  <div className="alert-row-body">
                    <p className="alert-row-reason">{a.reason}</p>
                    <p className="alert-row-meta">
                      <span className="id">{a.machine_id}</span> &middot;{' '}
                      <span className="id">{a.employee_id}</span> &middot;{' '}
                      {new Date(a.timestamp).toLocaleString()}
                      {formatDetail(a.detail) ? ` · ${formatDetail(a.detail)}` : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <p style={{ color: "var(--text-2)", fontSize: 13, margin: "16px 0 0", maxWidth: "80ch" }}>
            Signals: USB insert/remove (always flagged), after-hours activity (outside
            WAMSS_WORK_HOURS), and file write bursts above WAMSS_DLP_WRITE_THRESHOLD_MB
            per interval. Workstation agents report byte counts only; no file or keystroke
            content is ever captured.
          </p>
        </section>
      </Reveal>
    </div>
  );
}
