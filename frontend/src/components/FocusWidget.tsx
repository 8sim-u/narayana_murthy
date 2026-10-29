import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowClockwise } from '@phosphor-icons/react';
import {
  Chip, EmptyState, ErrorNotice, Field, Meter, Reveal,
  SectionHead, SkeletonRows, Stat,
} from './ui';

const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

const REFRESH_INTERVAL_MS = 60000;

interface FocusBreakdown {
  score: number;
  focused_blocks: number;
  focus_reward: number;
  app_switches: number;
  switch_free_allowance: number;
  switch_penalty: number;
  short_segments: number;
  fragment_penalty: number;
  idle_fraction: number;
  idle_penalty: number;
  active_minutes: number;
}

interface FocusDaily {
  employee_id: string;
  date: string;
  focus_score: number;
  breakdown: FocusBreakdown;
  break_compliance: {
    required_breaks: number;
    taken_breaks: number;
    compliant: boolean;
  };
}

interface FocusStreaks {
  employee_id: string;
  focus_streak_days: number;
  break_streak_days: number;
  badges: string[];
}

interface ActivityRecord {
  employee_id: string;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Request failed (${res.status}): ${body || res.statusText}`);
  }
  return (await res.json()) as T;
}

function isoDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function scoreColor(score: number): string {
  if (score >= 70) return 'var(--ok)';
  if (score >= 40) return 'var(--warn)';
  return 'var(--bad)';
}

/** 7-day bar chart. Bars grow in on mount (scaleY from the baseline). */
function WeekBars({ trend }: { trend: Array<{ date: string; score: number | null }> }) {
  const W = 560;
  const H = 190;
  const PAD = 40;
  const n = trend.length;
  if (n === 0) return <p style={{ color: 'var(--text-2)' }}>No data for the selected range.</p>;
  const slot = (W - PAD * 2) / n;
  const barW = Math.max(10, Math.min(52, slot * 0.55));
  const base = H - PAD;
  const scale = (H - PAD * 2) / 100;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="Focus score trend over the last 7 days"
      style={{ maxWidth: 680, display: 'block' }}
    >
      {[0.25, 0.5, 0.75, 1].map((f) => {
        const y = base - 100 * scale * f;
        return (
          <g key={f}>
            <line x1={PAD} y1={y} x2={W - 12} y2={y} stroke="var(--rule)" />
            <text x={PAD - 8} y={y + 4} fontSize={10} fill="var(--text-3)" textAnchor="end" fontFamily="var(--font-mono)">
              {Math.round(100 * f)}
            </text>
          </g>
        );
      })}
      {trend.map((point, i) => {
        const h = point.score === null ? 0 : Math.max(3, point.score * scale);
        const x = PAD + i * slot + (slot - barW) / 2;
        return (
          <g key={point.date}>
            {point.score === null ? (
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
                  fill={scoreColor(point.score)}
                  className="bar-grow"
                  style={{ transformOrigin: `${x + barW / 2}px ${base}px`, animationDelay: `${i * 60}ms` }}
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
                  {point.score}
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
              {point.date.slice(5)}
            </text>
          </g>
        );
      })}
      <line x1={PAD} y1={base} x2={W - 12} y2={base} stroke="var(--text-3)" />
    </svg>
  );
}

export default function FocusWidget({ officeId = "" }: { officeId?: string }) {
  const [employees, setEmployees] = useState<string[]>([]);
  const [employeeId, setEmployeeId] = useState('');
  const [daily, setDaily] = useState<FocusDaily | null>(null);
  const [streaks, setStreaks] = useState<FocusStreaks | null>(null);
  const [trend, setTrend] = useState<Array<{ date: string; score: number | null }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadEmployees = useCallback(async () => {
    try {
      const officeParam = officeId ? `&office_id=${encodeURIComponent(officeId)}` : '';
      const res = await fetch(`${API_URL}/activity?limit=1000&offset=0${officeParam}`);
      const records = await handleResponse<ActivityRecord[]>(res);
      const seen = new Set<string>();
      for (const r of records) {
        if (r.employee_id) seen.add(r.employee_id);
      }
      const list = [...seen].sort();
      setEmployees(list);
      setEmployeeId((prev) => prev || list[0] || '');
    } catch {
      // Employee list is a convenience; the widget still works with manual entry.
    }
  }, [officeId]);

  const loadFocus = useCallback(
    async (id: string, showLoading: boolean) => {
      if (!id) return;
      if (showLoading) setLoading(true);
      const officeParam = officeId ? `&office_id=${encodeURIComponent(officeId)}` : '';
      try {
        const dailyRes = await fetch(
          `${API_URL}/focus/daily?employee_id=${encodeURIComponent(id)}${officeParam}`,
        );
        const dailyData = await handleResponse<FocusDaily>(dailyRes);

        const streaksRes = await fetch(
          `${API_URL}/focus/streaks?employee_id=${encodeURIComponent(id)}`,
        );
        const streaksData = await handleResponse<FocusStreaks>(streaksRes);

        const trendResults = await Promise.all(
          [...Array(7)].map((_, i) => {
            const d = isoDate(6 - i);
            return fetch(
              `${API_URL}/focus/daily?employee_id=${encodeURIComponent(id)}&date=${d}${officeParam}`,
            )
              .then((res) => handleResponse<FocusDaily>(res))
              .then((data) => ({ date: d, score: data.focus_score }))
              .catch(() => ({ date: d, score: null }));
          }),
        );

        setDaily(dailyData);
        setStreaks(streaksData);
        setTrend(trendResults);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load focus data');
      } finally {
        setLoading(false);
      }
    },
    [officeId],
  );

  const refresh = useCallback(() => {
    void loadFocus(employeeId, false);
  }, [employeeId, loadFocus]);

  useEffect(() => {
    void loadEmployees();
  }, [loadEmployees]);

  useEffect(() => {
    if (!employeeId) return;
    void loadFocus(employeeId, true);
    const id = window.setInterval(() => void loadFocus(employeeId, false), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [employeeId, loadFocus]);

  const breakdownRows = useMemo(() => {
    if (!daily) return [];
    const b = daily.breakdown;
    return [
      { label: 'Deep-work blocks (25+ min)', value: `${b.focused_blocks} x +2 = +${b.focus_reward}` },
      {
        label: 'App switches',
        value: `${b.app_switches} (${b.switch_free_allowance} free) = -${b.switch_penalty}`,
      },
      { label: 'Short segments', value: `${b.short_segments} = -${b.fragment_penalty}` },
      {
        label: `Idle fraction ${(b.idle_fraction * 100).toFixed(1)}%`,
        value: `-${b.idle_penalty}`,
      },
      { label: 'Active minutes', value: `${b.active_minutes}` },
    ];
  }, [daily]);

  const score = daily?.focus_score ?? 0;

  return (
    <div className="rise">
      <section className="panel" aria-label="Focus controls">
        <div className="section-head">
          <h2 className="section-title">Employee</h2>
          <div className="form-row" style={{ marginLeft: 'auto' }}>
            {employees.length > 0 ? (
              <Field label="Select">
                <select className="select" value={employeeId} onChange={(e) => setEmployeeId(e.target.value)} aria-label="Select employee">
                  {employees.map((e) => (
                    <option key={e} value={e}>
                      {e}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label="Employee ID">
                <input
                  className="input"
                  type="text"
                  placeholder="employee_id"
                  autoComplete="off"
                  spellCheck={false}
                  value={employeeId}
                  onChange={(e) => setEmployeeId(e.target.value)}
                />
              </Field>
            )}
            <button type="button" className="btn" onClick={refresh} disabled={loading}>
              <ArrowClockwise size={15} weight="bold" aria-hidden="true" /> {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        </div>
      </section>

      {error && (
        <ErrorNotice
          message={`Could not load focus data: ${error}`}
          onRetry={() => { void loadFocus(employeeId, true); }}
        />
      )}

      {loading && !daily && <SkeletonRows rows={4} height={64} />}

      {!daily && !loading && !error && (
        <div className="panel">
          <EmptyState
            title="No employee selected"
            body="Pick an employee above to see their focus score, streaks and 7-day trend."
          />
        </div>
      )}

      {daily && (
        <>
          <Reveal>
            <section className="panel" aria-label="Focus score summary">
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 26, flexWrap: 'wrap' }}>
                <Stat
                  label="Today"
                  value={<><span key={score} className="swell" style={{ display: 'inline-block', color: scoreColor(score) }}>{score}</span><span style={{ fontSize: 18, color: 'var(--text-3)' }}> / 100</span></>}
                  sub={`${daily.employee_id} · ${daily.date}`}
                />
                <div style={{ flex: 1, minWidth: 180, paddingBottom: 6 }}>
                  <Meter value={score} color={scoreColor(score)} label={`Focus score ${score} of 100`} />
                </div>
              </div>
              <hr className="divider" />
              <div className="grid-4">
                <Stat label="Focus streak" value={`${streaks?.focus_streak_days ?? 0}d`} mono />
                <Stat label="Break streak" value={`${streaks?.break_streak_days ?? 0}d`} mono />
                <div className="stat">
                  <div className="stat-value mono" style={{ fontSize: 24 }}>
                    <Chip tone={daily.break_compliance.compliant ? 'ok' : 'warn'}>
                      {daily.break_compliance.compliant ? 'Healthy' : 'Needs breaks'}
                    </Chip>
                  </div>
                  <div className="stat-label">Break Compliance</div>
                  <div className="stat-sub">
                    {daily.break_compliance.taken_breaks}/{daily.break_compliance.required_breaks} taken
                  </div>
                </div>
                <Stat label="Active minutes" value={daily.breakdown.active_minutes} mono />
              </div>
              {streaks && streaks.badges.length > 0 && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
                  {streaks.badges.map((badge) => (
                    <Chip key={badge} tone="iris">{badge}</Chip>
                  ))}
                </div>
              )}
            </section>
          </Reveal>

          <div className="grid-2">
            <Reveal>
              <section className="panel" aria-label="Score Breakdown">
                <SectionHead title="Score Breakdown" />
                <div className="table-wrap" style={{ border: 0 }}>
                  <table className="ledger">
                    <tbody>
                      {breakdownRows.map((row, i) => (
                        <tr key={row.label} className="rise-stagger" style={{ '--i': i } as React.CSSProperties}>
                          <td style={{ color: 'var(--text-2)' }}>{row.label}</td>
                          <td className="num" style={{ textAlign: 'right' }}>{row.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </Reveal>
            <Reveal>
              <section className="panel" aria-label="Seven day trend">
                <SectionHead title="Last 7 Days" />
                <WeekBars trend={trend} />
              </section>
            </Reveal>
          </div>

          <p style={{ color: 'var(--text-2)', fontSize: 13, maxWidth: "80ch" }}>
            Formula: start at 100, +2 per 25-min deep-work block (capped at 10), -1 per app
            switch beyond 8/hour free, -2 per short segment beyond 6 free, minus a share
            of idle time. Refreshes every 60 seconds.
          </p>
        </>
      )}
    </div>
  );
}
