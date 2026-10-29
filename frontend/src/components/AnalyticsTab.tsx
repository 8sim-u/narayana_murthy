import { useEffect, useMemo, useState } from "react";
import {
  Chip, EmptyState, ErrorNotice, Field, Reveal,
  SectionHead, SkeletonRows,
} from "./ui";

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
const CHART_H = 190;
const PAD = 40;

function strainColor(v: number): string {
  if (v >= 70) return "var(--bad)";
  if (v >= 40) return "var(--warn)";
  return "var(--ok)";
}

/** Minimal pure-SVG vertical bar chart. Bars grow in on mount, staggered. */
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
  color: string | ((v: number) => string);
  format: (v: number) => string;
}) {
  const n = values.length;
  if (n === 0) return <p style={{ color: "var(--text-2)" }}>No data for the selected range.</p>;
  const slot = (CHART_W - PAD * 2) / n;
  const barW = Math.max(10, Math.min(52, slot * 0.55));
  const base = CHART_H - PAD;
  const scale = (CHART_H - PAD * 2) / (max || 1);

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      role="img"
      aria-label="Weekly trend bar chart"
      style={{ maxWidth: 680, display: "block" }}
    >
      {[0.25, 0.5, 0.75, 1].map((f) => {
        const y = base - max * scale * f;
        return (
          <g key={f}>
            <line x1={PAD} y1={y} x2={CHART_W - 12} y2={y} stroke="var(--rule)" />
            <text x={PAD - 8} y={y + 4} fontSize={10} fill="var(--text-3)" textAnchor="end" fontFamily="var(--font-mono)">
              {format(max * f)}
            </text>
          </g>
        );
      })}
      {values.map((v, i) => {
        const h = v === null ? 0 : Math.max(3, v * scale);
        const x = PAD + i * slot + (slot - barW) / 2;
        const fill = v === null ? "none" : typeof color === "function" ? color(v) : color;
        return (
          <g key={i}>
            {v === null ? (
              <text x={x + barW / 2} y={base - 10} fontSize={11} fill="var(--text-3)" textAnchor="middle" fontFamily="var(--font-mono)">
                n/a
              </text>
            ) : (
              <>
                <rect
                  x={x}
                  y={base - h}
                  width={barW}
                  height={h}
                  rx={4}
                  fill={fill}
                  className="bar-grow"
                  style={{ transformOrigin: `${x + barW / 2}px ${base}px`, animationDelay: `${i * 55}ms` }}
                />
                <text
                  x={x + barW / 2}
                  y={base - h - 8}
                  fontSize={11}
                  fontWeight={600}
                  fill="var(--ink)"
                  textAnchor="middle"
                  fontFamily="var(--font-display)"
                >
                  {format(v)}
                </text>
              </>
            )}
            <text
              x={x + barW / 2}
              y={base + 18}
              fontSize={10}
              fill="var(--text-3)"
              textAnchor="middle"
              fontFamily="var(--font-mono)"
            >
              {labels[i]}
            </text>
          </g>
        );
      })}
      <line x1={PAD} y1={base} x2={CHART_W - 12} y2={base} stroke="var(--text-3)" />
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
    <div className="rise">
      <Reveal>
        <section className="panel" aria-label="Trend controls">
          <div className="section-head">
            <h2 className="section-title">Range</h2>
            <div className="form-row" style={{ marginLeft: "auto" }}>
              <Field label="Metric">
                <select className="select" value={metric} onChange={(e) => setMetric(e.target.value as Metric)} aria-label="Trend metric">
                  <option value="focus">Focus</option>
                  <option value="strain">Strain</option>
                </select>
              </Field>
              <Field label="Weeks">
                <select className="select" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} aria-label="Weeks of history">
                  {[4, 8, 12, 26].map((w) => (
                    <option key={w} value={w}>
                      {w}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </div>
          <p style={{ color: "var(--text-2)", fontSize: 13, margin: 0, maxWidth: "80ch" }}>
            {metric === "focus"
              ? "Weekly mean of daily focus scores per employee; team value is the mean of employee weekly means. Days with no data are excluded."
              : "Weekly mean strain score per employee with green/amber/red level counts. Strain is a heuristic workload signal, not a medical measure."}
          </p>
        </section>
      </Reveal>

      {error && (
        <ErrorNotice
          message={`Could not load trends: ${error}`}
          onRetry={() => {
            setError(null);
            setLoading(true);
            fetch(`${API_URL}/analytics/trends?metric=${metric}&weeks=${weeks}`)
              .then((res) => {
                if (!res.ok) throw new Error(`Request failed (${res.status})`);
                return res.json() as Promise<TrendsResponse>;
              })
              .then((json) => { setData(json); setLoading(false); })
              .catch((err: unknown) => {
                setError(err instanceof Error ? err.message : "Failed to load trends");
                setLoading(false);
              });
          }}
        />
      )}

      {loading ? (
        <div className="panel"><SkeletonRows rows={3} height={120} /></div>
      ) : data ? (
        <>
          <Reveal>
            <section className="panel" aria-label="Team weekly average">
              <SectionHead title="Team Average per Week" />
              <div key={`${metric}-${weeks}`}>
                {metric === "focus" ? (
                  <BarChart
                    labels={data.team.map((t) => shortWeek(t.week_start))}
                    values={(data.team as TeamFocusRow[]).map((t) => t.average_focus)}
                    max={100}
                    color="var(--iris)"
                    format={(v) => v.toFixed(0)}
                  />
                ) : (
                  <BarChart
                    labels={data.team.map((t) => shortWeek(t.week_start))}
                    values={(data.team as TeamStrainRow[]).map((t) => t.average_strain)}
                    max={100}
                    color={strainColor}
                    format={(v) => v.toFixed(0)}
                  />
                )}
              </div>
            </section>
          </Reveal>

          <Reveal>
            <section className="panel" aria-label="Latest week per employee">
              <SectionHead title={`Latest Week (${latestWeek ?? "n/a"})`} meta={`${latestEmployees.length} employees`} />
              {latestEmployees.length === 0 ? (
                <EmptyState title="No employee data" body="No per-employee rows were returned for the latest week." />
              ) : (
                <div className="table-wrap">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>Employee</th>
                        <th>{metric === "focus" ? "Avg focus" : "Avg strain"}</th>
                        <th>{metric === "focus" ? "Days with data" : "Green / Amber / Red"}</th>
                        <th>Level</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latestEmployees.map((r, i) => {
                        const avg =
                          metric === "focus"
                            ? (r as EmployeeFocusRow).average_focus
                            : (r as EmployeeStrainRow).average_strain;
                        const pct = avg === null ? 0 : Math.max(0, Math.min(100, avg));
                        const barColor =
                          metric === "focus"
                            ? "var(--iris)"
                            : avg === null
                              ? "var(--text-3)"
                              : strainColor(avg);
                        return (
                          <tr key={r.employee_id} className="rise-stagger" style={{ "--i": Math.min(i, 20) } as React.CSSProperties}>
                            <td className="id">{r.employee_id}</td>
                            <td className="num">{avg === null ? "n/a" : avg.toFixed(1)}</td>
                            <td className="num">
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
                            <td style={{ minWidth: 140 }}>
                              <div className="meter" role="img" aria-label={`${r.employee_id} ${pct.toFixed(0)}`}>
                                <i style={{ width: `${pct}%`, background: barColor }} />
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {latestTeam && metric === "strain" && (
                <p style={{ color: "var(--text-2)", fontSize: 13, marginTop: 12 }}>
                  Team this week:{" "}
                  <Chip tone="ok">{(latestTeam as TeamStrainRow).green} green</Chip>{" "}
                  <Chip tone="warn">{(latestTeam as TeamStrainRow).amber} amber</Chip>{" "}
                  <Chip tone="bad">{(latestTeam as TeamStrainRow).red} red</Chip>{" "}
                  across {(latestTeam as TeamStrainRow).employee_count} employees.
                </p>
              )}
            </section>
          </Reveal>
        </>
      ) : null}
    </div>
  );
}
