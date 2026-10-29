import { useEffect, useMemo, useState } from "react";
import ExportButton from "./ExportButton";

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
  green: "#2e9e44",
  amber: "#d97706",
  red: "#dc2626",
  unknown: "#9ca3af",
};

// Distinct badge colors for the activity-events feed on the timeline.
const EVENT_BADGE: Record<string, { bg: string; fg: string }> = {
  activity: { bg: "#e0f2fe", fg: "#0369a1" },
  alert: { bg: "#fee2e2", fg: "#dc2626" },
  break_suggested: { bg: "#fef3c7", fg: "#d97706" },
  fatigue_suspected: { bg: "#ede9fe", fg: "#7c3aed" },
};

function eventBadge(type: string) {
  return EVENT_BADGE[type] ?? { bg: "#f3f4f6", fg: "#6b7280" };
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
    } catch (e: any) {
      setError(e?.message || "Failed to load activity data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, 60000);
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
    <div style={{ padding: 24, fontFamily: "system-ui, sans-serif", maxWidth: 1100 }}>
      <h2>Employee Workstation Activity</h2>
      <div style={{ marginBottom: 12 }}>
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
      </div>
      <p style={{ color: "#555", fontSize: 13 }}>
        Strain level is a heuristic workload signal (green &lt; 40, amber 40-69, red &ge; 70).
        It is not a medical measure. ML stress is an experimental model score shown when the
        backend provides it.
      </p>

      <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <label>
          Employee ID{" "}
          <input value={employeeId} onChange={(e) => setEmployeeId(e.target.value)} placeholder="all" />
        </label>
        <label>
          Machine ID{" "}
          <input value={machineId} onChange={(e) => setMachineId(e.target.value)} placeholder="all" />
        </label>
        <label>
          Idle alert threshold (min){" "}
          <input
            type="number"
            min={1}
            value={idleThresholdMin}
            onChange={(e) => setIdleThresholdMin(Number(e.target.value) || 15)}
            style={{ width: 70 }}
          />
        </label>
        <button onClick={fetchData}>Refresh</button>
      </div>

      {error && (
        <div style={{ background: "#fee2e2", padding: 12, borderRadius: 6, marginBottom: 16 }}>
          Error: {error}
        </div>
      )}

      {idleAlerts.length > 0 && (
        <div
          style={{
            background: "#fef3c7",
            border: "1px solid #d97706",
            padding: 12,
            borderRadius: 6,
            marginBottom: 16,
          }}
        >
          <strong>Idle alert:</strong> {idleAlerts.length} machine(s) idle past {idleThresholdMin} min:{" "}
          {idleAlerts.map((s) => `${s.employee_id}@${s.machine_id}`).join(", ")}
        </div>
      )}

      {loading && <p>Loading...</p>}

      <h3>Strain level per machine</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        {strain.map((s) => (
          <div
            key={`${s.machine_id}/${s.employee_id}`}
            style={{
              border: "1px solid #ddd",
              borderRadius: 6,
              padding: "8px 12px",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: STRAIN_COLORS[s.strain_level] || "#9ca3af",
                display: "inline-block",
              }}
            />
            <span>
              {s.employee_id}@{s.machine_id}
            </span>
            <span style={{ color: "#666", fontSize: 12 }}>
              strain {s.strain_score ?? "-"} ({s.strain_level})
            </span>
            <span style={{ color: "#666", fontSize: 12 }}>
              ML stress {(s.ml_stress_score ?? null) ?? "-"}
            </span>
          </div>
        ))}
        {strain.length === 0 && !loading && <span style={{ color: "#888" }}>No data yet.</span>}
      </div>

      <h3>App usage breakdown</h3>
      {appUsage.length === 0 ? (
        <p style={{ color: "#888" }}>No active segments yet.</p>
      ) : (
        <div className="table-wrap">
        <table style={{ borderCollapse: "collapse", width: "100%", marginBottom: 16 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ddd" }}>
              <th style={{ padding: 6 }}>Application</th>
              <th style={{ padding: 6 }}>Minutes</th>
            </tr>
          </thead>
          <tbody>
            {appUsage.map(([app, mins]) => (
              <tr key={app} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: 6 }}>{app}</td>
                <td style={{ padding: 6 }}>{mins.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      <h3>Idle stats</h3>
      <p>
        Active: <strong>{idleStats.activeMin.toFixed(1)} min</strong> | Idle:{" "}
        <strong>{idleStats.idleMin.toFixed(1)} min</strong>
      </p>

      {alerts.length > 0 && (
        <>
          <h3>Break reminders &amp; alerts</h3>
          <ul>
            {alerts.map((a) => {
              const b = eventBadge(a.event_type);
              return (
                <li key={a.id}>
                  <span
                    style={{
                      display: "inline-block",
                      background: b.bg,
                      color: b.fg,
                      borderRadius: 4,
                      padding: "1px 8px",
                      fontSize: 12,
                      fontWeight: 600,
                      marginRight: 6,
                    }}
                  >
                    {a.event_type}
                  </span>
                  {a.employee_id}@{a.machine_id} - {a.window_title} (
                  {new Date(a.start_time).toLocaleString()})
                </li>
              );
            })}
          </ul>
        </>
      )}

      <h3>Timeline (latest first)</h3>
      <div className="table-wrap">
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "2px solid #ddd" }}>
            <th style={{ padding: 6 }}>Time</th>
            <th style={{ padding: 6 }}>Type</th>
            <th style={{ padding: 6 }}>Employee</th>
            <th style={{ padding: 6 }}>Machine</th>
            <th style={{ padding: 6 }}>App</th>
            <th style={{ padding: 6 }}>Window</th>
            <th style={{ padding: 6 }}>Keys</th>
            <th style={{ padding: 6 }}>Clicks</th>
            <th style={{ padding: 6 }}>Idle</th>
            <th style={{ padding: 6 }}>Strain</th>
          </tr>
        </thead>
        <tbody>
          {timelineRows.map((r) => {
            const isEvent = r.event_type !== "activity";
            const b = eventBadge(r.event_type);
            return (
              <tr
                key={r.id}
                style={{
                  borderBottom: "1px solid #eee",
                  background: isEvent ? b.bg : undefined,
                }}
              >
                <td style={{ padding: 6, fontSize: 12 }}>{new Date(r.start_time).toLocaleString()}</td>
                <td style={{ padding: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      background: b.bg,
                      color: b.fg,
                      border: `1px solid ${b.fg}`,
                      borderRadius: 4,
                      padding: "1px 8px",
                      fontSize: 12,
                      fontWeight: 600,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {r.event_type}
                  </span>
                </td>
                <td style={{ padding: 6 }}>{r.employee_id}</td>
                <td style={{ padding: 6 }}>{r.machine_id}</td>
                <td style={{ padding: 6 }}>{isEvent ? "-" : r.app_name}</td>
                <td style={{ padding: 6, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {r.window_title}
                </td>
                <td style={{ padding: 6 }}>{isEvent ? "-" : r.keystrokes}</td>
                <td style={{ padding: 6 }}>{isEvent ? "-" : r.mouse_clicks}</td>
                <td style={{ padding: 6 }}>{r.is_idle ? "yes" : "no"}</td>
                <td style={{ padding: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: STRAIN_COLORS[r.strain_level || "unknown"],
                      marginRight: 4,
                    }}
                  />
                  {r.strain_score ?? "-"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
}
