import { useEffect, useMemo, useState } from "react";
import { ArrowClockwise } from "@phosphor-icons/react";
import ExportButton from "./ExportButton";
import {
  Chip, Dot, EmptyState, ErrorNotice, Field, Meter, Reveal,
  SectionHead, SkeletonRows, Stat, strainDot,
} from "./ui";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

interface ActivityRecord {
  id: number;
  machine_id: string;
  employee_id: string;
  event_type: string;
  app_name: string;
  window_title: string;
  process_name: string;
  start_time: string;
  end_time: string;
  keystrokes: number;
  mouse_clicks: number;
  is_idle: boolean;
  strain_score: number | null;
  strain_level: string | null;
}

interface StrainSummary {
  machine_id: string;
  employee_id: string;
  strain_score: number | null;
  strain_level: string;
  last_seen: string | null;
  is_idle: boolean;
  // Not yet served by GET /activity/strain-summary; renders "-" until the backend ships it.
  ml_stress_score?: number | null;
}

const STRAIN_COLORS: Record<string, string> = {
  green: "var(--ok)",
  amber: "var(--warn)",
  red: "var(--bad)",
  unknown: "var(--text-3)",
};

const EVENT_TONE: Record<string, "signal" | "bad" | "warn" | "iris" | "idle"> = {
  activity: "signal",
  alert: "bad",
  break_suggested: "warn",
  fatigue_suspected: "iris",
};

function eventTone(type: string) {
  return EVENT_TONE[type] ?? "idle";
}

function minutesBetween(start: string, end: string): number {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Math.max(0, ms / 60000);
}

export default function EmployeeActivity({ officeId = "" }: { officeId?: string }) {
  const [records, setRecords] = useState<ActivityRecord[]>([]);
  const [strain, setStrain] = useState<StrainSummary[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [machineId, setMachineId] = useState("");
  const [idleThresholdMin, setIdleThresholdMin] = useState(15);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (employeeId) params.set("employee_id", employeeId);
      if (machineId) params.set("machine_id", machineId);
      if (officeId) params.set("office_id", officeId);
      params.set("limit", "500");
      const [actRes, strainRes] = await Promise.all([
        fetch(`${API_URL}/activity?${params.toString()}`),
        fetch(`${API_URL}/activity/strain-summary`),
      ]);
      if (!actRes.ok) throw new Error(`activity: HTTP ${actRes.status}`);
      if (!strainRes.ok) throw new Error(`strain-summary: HTTP ${strainRes.status}`);
      setRecords(await actRes.json());
      setStrain(await strainRes.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load activity data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchData();
    const t = setInterval(() => { void fetchData(); }, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [officeId]);

  const activityRows = useMemo(
    () => records.filter((r) => r.event_type === "activity"),
    [records]
  );

  const appUsage = useMemo(() => {
    const map = new Map<string, number>();
    for (const r of activityRows) {
      if (r.is_idle) continue;
      const name = r.app_name || "(unknown)";
      map.set(name, (map.get(name) || 0) + minutesBetween(r.start_time, r.end_time));
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [activityRows]);

  const maxAppMin = appUsage.length > 0 ? appUsage[0][1] : 1;

  const idleStats = useMemo(() => {
    let idleMin = 0;
    let activeMin = 0;
    for (const r of activityRows) {
      const m = minutesBetween(r.start_time, r.end_time);
      if (r.is_idle) idleMin += m;
      else activeMin += m;
    }
    return { idleMin, activeMin };
  }, [activityRows]);

  const idleAlerts = useMemo(() => {
    const now = Date.now();
    return strain.filter((s) => {
      if (!s.is_idle || !s.last_seen) return false;
      const idleMin = (now - new Date(s.last_seen).getTime()) / 60000;
      return idleMin >= idleThresholdMin;
    });
  }, [strain, idleThresholdMin]);

  const alerts = useMemo(
    () =>
      records.filter(
        (r) => r.event_type === "alert" || r.event_type === "break_suggested" || r.event_type === "fatigue_suspected"
      ),
    [records]
  );

  const timelineRows = useMemo(() => records.slice(0, 100), [records]);

  return (
    <div className="rise">
      <section className="panel" aria-label="Activity filters">
        <div className="section-head">
          <h2 className="section-title">Telemetry Filters</h2>
          <div className="form-row" style={{ marginLeft: "auto" }}>
            <ExportButton
              endpoint="/exports/activity.csv"
              label="Export CSV"
              filters={{
                employee_id: employeeId || undefined,
                machine_id: machineId || undefined,
                limit: 1000,
              }}
              filename="activity.csv"
            />
            <button type="button" className="btn" onClick={() => { void fetchData(); }}>
              <ArrowClockwise size={15} weight="bold" aria-hidden="true" /> Refresh
            </button>
          </div>
        </div>
        <div className="form-row">
          <Field label="Employee ID">
            <input className="input" value={employeeId} onChange={(e) => setEmployeeId(e.target.value)} placeholder="all" autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Machine ID">
            <input className="input" value={machineId} onChange={(e) => setMachineId(e.target.value)} placeholder="all" autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Idle alert threshold (min)">
            <input
              className="input"
              type="number"
              min={1}
              value={idleThresholdMin}
              onChange={(e) => setIdleThresholdMin(Number(e.target.value) || 15)}
              style={{ width: 90 }}
            />
          </Field>
        </div>
        <p style={{ color: "var(--text-2)", fontSize: 13, margin: "14px 0 0", maxWidth: "80ch" }}>
          Strain level is a heuristic workload signal (green &lt; 40, amber 40-69, red &ge; 70).
          It is not a medical measure. ML stress is an experimental model score shown when the
          backend provides it.
        </p>
      </section>

      {error && (
        <ErrorNotice
          message={`Could not load activity data: ${error}`}
          onRetry={() => { void fetchData(); }}
        />
      )}

      {idleAlerts.length > 0 && (
        <div className="notice notice-warn rise" role="alert">
          <p>
            <strong>Idle alert:</strong> {idleAlerts.length} machine(s) idle past {idleThresholdMin} min:{' '}
            {idleAlerts.map((s) => `${s.employee_id}@${s.machine_id}`).join(", ")}
          </p>
        </div>
      )}

      <Reveal>
        <section className="panel" aria-label="Strain per Machine">
          <SectionHead title="Strain per Machine" meta={`${strain.length} machines`} />
          {loading && strain.length === 0 ? (
            <SkeletonRows rows={3} height={56} />
          ) : strain.length === 0 ? (
            <EmptyState title="No strain data" body="Strain summaries appear here once workstation agents start reporting." />
          ) : (
            <ul className="spec-list">
              {strain.map((s, i) => {
                const color = STRAIN_COLORS[(s.strain_level || "unknown").toLowerCase()] ?? "var(--text-3)";
                return (
                  <li key={`${s.machine_id}/${s.employee_id}`} className="rise-stagger" style={{ "--i": Math.min(i, 12) } as React.CSSProperties}>
                    <span className="spec-term" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Dot tone={strainDot(s.strain_level)} />
                      <span className="id" style={{ fontFamily: "var(--font-mono)", fontSize: 12.5 }}>
                        {s.employee_id}@{s.machine_id}
                      </span>
                    </span>
                    <span style={{ flex: 1, minWidth: 140 }}>
                      <Meter value={s.strain_score ?? 0} color={color} label={`Strain ${s.strain_score ?? "unknown"}`} />
                    </span>
                    <span className="num" style={{ fontFamily: "var(--font-mono)", fontSize: 12.5, whiteSpace: "nowrap" }}>
                      {s.strain_score ?? "-"} / {s.strain_level}
                    </span>
                    <Chip tone="iris">ML {s.ml_stress_score ?? "-"}</Chip>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </Reveal>

      <div className="grid-2">
        <Reveal>
          <section className="panel" aria-label="App Usage">
            <SectionHead title="App Usage" meta="active minutes" />
            {loading && appUsage.length === 0 ? (
              <SkeletonRows rows={4} height={40} />
            ) : appUsage.length === 0 ? (
              <EmptyState title="No active segments" body="App Usage builds up as agents report non-idle windows." />
            ) : (
              <ul className="spec-list">
                {appUsage.slice(0, 8).map(([app, mins], i) => (
                  <li key={app} className="rise-stagger" style={{ "--i": Math.min(i, 8) } as React.CSSProperties}>
                    <span className="spec-term" style={{ minWidth: 150 }}>{app}</span>
                    <span style={{ flex: 1, minWidth: 120 }}>
                      <Meter value={mins} max={maxAppMin} color="var(--ice)" label={`${app} ${mins.toFixed(1)} minutes`} />
                    </span>
                    <span className="num" style={{ fontFamily: "var(--font-mono)", fontSize: 12.5 }}>{mins.toFixed(1)} min</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </Reveal>

        <Reveal>
          <section className="panel" aria-label="Idle stats">
            <SectionHead title="Time Split" />
            <div className="grid-2">
              <Stat label="Active" value={`${idleStats.activeMin.toFixed(0)} min`} mono />
              <Stat label="Idle" value={`${idleStats.idleMin.toFixed(0)} min`} mono tone="var(--text-2)" />
            </div>
            {alerts.length > 0 && (
              <>
                <hr className="divider" />
                <h3 className="section-title" style={{ marginBottom: 10, fontSize: 15 }}>Break Reminders and Alerts</h3>
                <ul className="spec-list">
                  {alerts.slice(0, 6).map((a) => (
                    <li key={a.id}>
                      <Chip tone={eventTone(a.event_type)}>{a.event_type}</Chip>
                      <span className="spec-def">
                        {a.employee_id}@{a.machine_id} &middot; {new Date(a.start_time).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </Reveal>
      </div>

      <Reveal>
        <section className="panel" aria-label="Activity timeline">
          <SectionHead title="Timeline" meta="latest first, 100 rows" />
          {loading && timelineRows.length === 0 ? (
            <SkeletonRows rows={6} height={48} />
          ) : timelineRows.length === 0 ? (
            <EmptyState title="No timeline rows" body="Activity records will list here once agents report in." />
          ) : (
            <div className="table-wrap">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Employee</th>
                    <th>Machine</th>
                    <th>App</th>
                    <th>Window</th>
                    <th>Keys</th>
                    <th>Clicks</th>
                    <th>Idle</th>
                    <th>Strain</th>
                  </tr>
                </thead>
                <tbody>
                  {timelineRows.map((r) => {
                    const isEvent = r.event_type !== "activity";
                    return (
                      <tr key={r.id} className={isEvent ? "row-alert" : undefined}>
                        <td className="num">{new Date(r.start_time).toLocaleString()}</td>
                        <td><Chip tone={eventTone(r.event_type)}>{r.event_type}</Chip></td>
                        <td className="id">{r.employee_id}</td>
                        <td className="id">{r.machine_id}</td>
                        <td>{isEvent ? "-" : r.app_name}</td>
                        <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.window_title}</td>
                        <td className="num">{isEvent ? "-" : r.keystrokes}</td>
                        <td className="num">{isEvent ? "-" : r.mouse_clicks}</td>
                        <td>{r.is_idle ? "yes" : "no"}</td>
                        <td>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <Dot tone={strainDot(r.strain_level)} />
                            <span className="num">{r.strain_score ?? "-"}</span>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </Reveal>
    </div>
  );
}
