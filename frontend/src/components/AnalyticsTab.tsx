import { useEffect, useMemo, useState } from "react";

const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

type Metric = "focus" | "strain";

interface TeamFocusRow {
  week_start: string;
  average_focus: number | null;
  min_focus: number | null;
  max_focus: number | null;
  employee_count: number;
}

interface EmployeeFocusRow {
  week_start: string;
  employee_id: string;
  average_focus: number | null;
  days_with_data: number;
}

interface TeamStrainRow {
  week_start: string;
  average_strain: number | null;
  green: number;
  amber: number;
  red: number;
  unknown: number;
  employee_count: number;
}

interface EmployeeStrainRow {
  week_start: string;
  employee_id: string;
  average_strain: number | null;
  green: number;
  amber: number;
  red: number;
  unknown: number;
  records: number;
}

interface TrendsResponse {
  metric: string;
  weeks: number;
  per_employee: (EmployeeFocusRow | EmployeeStrainRow)[];
  team: (TeamFocusRow | TeamStrainRow)[];
}

const CHART_W = 560;
const CHART_H = 180;
const PAD = 36;

/** Minimal pure-SVG vertical bar chart. No chart library required. */
function BarChart({
  labels,
  values,
  max,
  color,
  format,
}: {
  labels: string[];
  values: (number | null)[];
  max: number;
  color: string;
  format: (v: number) => string;
}) {
  const n = values.length;
  if (n === 0) return <p className="muted">No data for the selected range.</p>;
  const slot = (CHART_W - PAD * 2) / n;
  const barW = Math.max(8, Math.min(48, slot * 0.55));
  const base = CHART_H - PAD;
  const scale = (CHART_H - PAD * 2) / (max || 1);

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      role="img"
      aria-label="Weekly trend bar chart"
      style={{ maxWidth: 640 }}
    >
      {[0.25, 0.5, 0.75, 1].map((f) => {
        const y = base - max * scale * f;
        return (
          <g key={f}>
            <line x1={PAD} y1={y} x2={CHART_W - 12} y2={y} stroke="#e5e7eb" />
            <text x={PAD - 6} y={y + 4} fontSize={10} fill="#6b7280" textAnchor="end">
              {format(max * f)}
            </text>
          </g>
        );
      })}
      {values.map((v, i) => {
        const h = v === null ? 0 : Math.max(2, v * scale);
        const x = PAD + i * slot + (slot - barW) / 2;
        return (
          <g key={i}>
            {v === null ? (
              <text x={x + barW / 2} y={base - 8} fontSize={11} fill="#9ca3af" textAnchor="middle">
                n/a
              </text>
            ) : (
              <>
                <rect x={x} y={base - h} width={barW} height={h} fill={color} rx={3} />
                <text
                  x={x + barW / 2}
                  y={base - h - 6}
                  fontSize={11}
                  fontWeight={600}
                  fill="#374151"
                  textAnchor="middle"
                >
                  {format(v)}
                </text>
              </>
            )}
            <text
              x={x + barW / 2}
              y={base + 16}
              fontSize={10}
              fill="#6b7280"
              textAnchor="middle"
            >
              {labels[i]}
            </text>
          </g>
        );
      })}
      <line x1={PAD} y1={base} x2={CHART_W - 12} y2={base} stroke="#9ca3af" />
    </svg>
  );
}

function shortWeek(iso: string): string {
  return iso.slice(5); // MM-DD
}

export default function AnalyticsTab() {
  const [metric, setMetric] = useState<Metric>("focus");
  const [weeks, setWeeks] = useState(8);
  const [data, setData] = useState<TrendsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/analytics/trends?metric=${metric}&weeks=${weeks}`)
      .then((res) => {
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        return res.json() as Promise<TrendsResponse>;
      })
      .then((json) => {
        if (!cancelled) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load trends");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [metric, weeks]);

  const latestTeam = useMemo(
    () => (data && data.team.length > 0 ? data.team[data.team.length - 1] : null),
    [data]
  );

  const latestWeek = useMemo(
    () => (data && data.team.length > 0 ? data.team[data.team.length - 1].week_start : null),
    [data]
  );

  const latestEmployees = useMemo(() => {
    if (!data || !latestWeek) return [];
    return data.per_employee.filter((r) => r.week_start === latestWeek);
  }, [data, latestWeek]);

  return (
    <section className="panel">
      <div className="toolbar">
        <h2>Trends</h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label>
            Metric{" "}
            <select value={metric} onChange={(e) => setMetric(e.target.value as Metric)}>
              <option value="focus">Focus</option>
              <option value="strain">Strain</option>
            </select>
          </label>
          <label>
            Weeks{" "}
            <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))}>
              {[4, 8, 12, 26].map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <p className="muted" style={{ fontSize: 13 }}>
        {metric === "focus"
          ? "Weekly mean of daily focus scores per employee; team value is the mean of employee weekly means. Days with no data are excluded."
          : "Weekly mean strain score per employee with green/amber/red level counts. Strain is a heuristic workload signal, not a medical measure."}
      </p>

      {loading && <p className="muted">Loading trends...</p>}
      {error && <p className="error">Could not load trends: {error}</p>}

      {data && !loading && (
        <>
          <h3 style={{ fontSize: 14, margin: "12px 0 4px" }}>Team average per week</h3>
          {metric === "focus" ? (
            <BarChart
              labels={data.team.map((t) => shortWeek(t.week_start))}
              values={(data.team as TeamFocusRow[]).map((t) => t.average_focus)}
              max={100}
              color="#2563eb"
              format={(v) => v.toFixed(0)}
            />
          ) : (
            <BarChart
              labels={data.team.map((t) => shortWeek(t.week_start))}
              values={(data.team as TeamStrainRow[]).map((t) => t.average_strain)}
              max={100}
              color="#d97706"
              format={(v) => v.toFixed(0)}
            />
          )}

          <h3 style={{ fontSize: 14, margin: "16px 0 4px" }}>
            Latest week ({latestWeek ?? "n/a"})
          </h3>
          {latestEmployees.length === 0 ? (
            <p className="muted">No employee data this week.</p>
          ) : (
            <table style={{ borderCollapse: "collapse", fontSize: 13, width: "100%", maxWidth: 640 }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #e5e7eb" }}>
                  <th style={{ padding: "6px 8px" }}>Employee</th>
                  <th style={{ padding: "6px 8px" }}>
                    {metric === "focus" ? "Avg focus" : "Avg strain"}
                  </th>
                  {metric === "focus" ? (
                    <th style={{ padding: "6px 8px" }}>Days with data</th>
                  ) : (
                    <th style={{ padding: "6px 8px" }}>Green / Amber / Red</th>
                  )}
                  <th style={{ padding: "6px 8px" }}>Trend</th>
                </tr>
              </thead>
              <tbody>
                {latestEmployees.map((r) => {
                  const avg =
                    metric === "focus"
                      ? (r as EmployeeFocusRow).average_focus
                      : (r as EmployeeStrainRow).average_strain;
                  const pct = avg === null ? 0 : Math.max(0, Math.min(100, avg));
                  const barColor =
                    metric === "focus"
                      ? "#2563eb"
                      : pct >= 70
                        ? "#dc2626"
                        : pct >= 40
                          ? "#d97706"
                          : "#2e9e44";
                  return (
                    <tr key={r.employee_id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                      <td style={{ padding: "6px 8px" }}>{r.employee_id}</td>
                      <td style={{ padding: "6px 8px" }}>
                        {avg === null ? "n/a" : avg.toFixed(1)}
                      </td>
                      <td style={{ padding: "6px 8px" }}>
                        {metric === "focus" ? (
                          (r as EmployeeFocusRow).days_with_data
                        ) : (
                          <>
                            {(r as EmployeeStrainRow).green} /{" "}
                            {(r as EmployeeStrainRow).amber} /{" "}
                            {(r as EmployeeStrainRow).red}
                          </>
                        )}
                      </td>
                      <td style={{ padding: "6px 8px", minWidth: 120 }}>
                        <div
                          style={{
                            height: 8,
                            background: "#f3f4f6",
                            borderRadius: 4,
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              width: `${pct}%`,
                              height: "100%",
                              background: barColor,
                            }}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {latestTeam && metric === "strain" && (
            <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
              Team this week: green {(latestTeam as TeamStrainRow).green}, amber{" "}
              {(latestTeam as TeamStrainRow).amber}, red {(latestTeam as TeamStrainRow).red},{" "}
              unknown {(latestTeam as TeamStrainRow).unknown} across{" "}
              {(latestTeam as TeamStrainRow).employee_count} employees.
            </p>
          )}
        </>
      )}
    </section>
  );
}
