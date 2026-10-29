import { useCallback, useEffect, useMemo, useState } from 'react';

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

function barColor(score: number): string {
  if (score >= 70) return '#2f9e44';
  if (score >= 40) return '#f08c00';
  return '#e03131';
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
    <section className="panel">
      <div className="toolbar">
        <h2>Focus score</h2>
        <div className="camera-filter">
          <label className="muted">Employee</label>
          {employees.length > 0 ? (
            <select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}>
              {employees.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder="employee_id"
              value={employeeId}
              onChange={(e) => setEmployeeId(e.target.value)}
            />
          )}
          <button type="button" onClick={refresh} disabled={loading}>
            {loading ? 'Loading' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <p className="error">Could not load focus data: {error}</p>}

      {daily && (
        <>
          <div className="health-row">
            <div className="health-item">
              <span className="health-label">Today</span>
              <span
                className="health-value"
                style={{ fontSize: '2.2rem', fontWeight: 700, color: barColor(score) }}
              >
                {score}
              </span>
              <span className="muted">/ 100</span>
            </div>
            <div className="health-item">
              <span className="health-label">Focus streak</span>
              <span className="health-value" style={{ fontSize: '1.5rem', fontWeight: 700 }}>
                {streaks?.focus_streak_days ?? 0} days
              </span>
            </div>
            <div className="health-item">
              <span className="health-label">Break streak</span>
              <span className="health-value" style={{ fontSize: '1.5rem', fontWeight: 700 }}>
                {streaks?.break_streak_days ?? 0} days
              </span>
            </div>
            <div className="health-item">
              <span className="health-label">Break compliance</span>
              <span className="health-value">
                {daily.break_compliance.compliant ? 'Healthy' : 'Needs breaks'} (
                {daily.break_compliance.taken_breaks}/{daily.break_compliance.required_breaks})
              </span>
            </div>
          </div>

          <div
            aria-label={`Focus score ${score} of 100`}
            style={{
              height: '14px',
              borderRadius: '7px',
              background: '#e9ecef',
              overflow: 'hidden',
              margin: '12px 0',
            }}
          >
            <div
              style={{
                width: `${score}%`,
                height: '100%',
                background: barColor(score),
                transition: 'width 0.3s',
              }}
            />
          </div>

          {streaks && streaks.badges.length > 0 && (
            <div className="health-row" style={{ marginBottom: '8px' }}>
              {streaks.badges.map((badge) => (
                <span key={badge} className="badge badge-ok" style={{ marginRight: '6px' }}>
                  {badge}
                </span>
              ))}
            </div>
          )}

          <h3 className="muted" style={{ margin: '8px 0 4px' }}>
            Score breakdown
          </h3>
          <div className="table-wrap">
            <table>
              <tbody>
                {breakdownRows.map((row) => (
                  <tr key={row.label}>
                    <td className="muted">{row.label}</td>
                    <td>{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="muted" style={{ margin: '12px 0 4px' }}>
            Last 7 days
          </h3>
          <svg
            width="100%"
            height="110"
            viewBox="0 0 350 110"
            role="img"
            aria-label="Focus score trend over the last 7 days"
          >
            {trend.map((point, i) => {
              const barHeight = point.score === null ? 4 : (point.score / 100) * 80;
              const x = i * 50 + 10;
              return (
                <g key={point.date}>
                  <rect
                    x={x}
                    y={90 - barHeight}
                    width="30"
                    height={barHeight}
                    rx="3"
                    fill={point.score === null ? '#ced4da' : barColor(point.score)}
                  />
                  <text x={x + 15} y={102} textAnchor="middle" fontSize="9" fill="#868e96">
                    {point.date.slice(5)}
                  </text>
                  {point.score !== null && (
                    <text x={x + 15} y={86 - barHeight} textAnchor="middle" fontSize="9" fill="#495057">
                      {point.score}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </>
      )}

      {!daily && !loading && !error && (
        <p className="muted">Select an employee to see their focus score.</p>
      )}

      <p className="muted" style={{ marginTop: '8px' }}>
        Formula: start at 100, +2 per 25-min deep-work block (capped at 10), -1 per app
        switch beyond 8/hour free, -2 per short segment beyond 6 free, minus a share
        of idle time. Refreshes every 60 seconds.
      </p>
    </section>
  );
}
