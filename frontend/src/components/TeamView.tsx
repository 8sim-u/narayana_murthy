// TeamView.tsx (Agent K) - team-level dashboard view for WAMSS.
//
// Aggregates team metrics from the sibling routers (activity, focus,
// presence). Every panel fetches independently and degrades to a graceful
// "no data / endpoint unavailable" state if its endpoint is missing, so one
// failed panel never breaks the others.
//
// Self-contained: imports React only. Uses VITE_API_URL (or localhost:8000)
// and VITE_TEAM_EMPLOYEES (comma-separated default employee ids) from env,
// plus a text field for ad-hoc employee lists.
import { useCallback, useEffect, useState } from 'react';

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

// ---------------------------------------------------------------- types

/** Expected shape of GET /activity/strain-summary items (confirmed in backend/app/activity.py). */
interface StrainSummaryItem {
  machine_id: string;
  employee_id: string;
  strain_level: string; // green | amber | red | unknown
  last_seen: string | null;
  is_idle: boolean;
}

/** Expected shape of GET /activity items used by the heuristic strain fallback. */
interface ActivityItem {
  employee_id: string;
  is_idle: boolean;
}

/** Flexible focus row: GET /focus/daily per employee returns FocusDailyOut
 *  {employee_id, date, focus_score, breakdown, break_compliance:{compliant}}. */
interface FocusDailyRow {
  employee_id?: string;
  focus_score?: number | null;
  score?: number | null;
  value?: number | null;
}

/** Flexible streak row: GET /focus/streaks per employee returns FocusStreaksOut
 *  {employee_id, focus_streak_days, break_streak_days, badges}. */
interface StreakRow {
  employee_id?: string;
  break_compliant?: boolean;
  compliant?: boolean;
  break_streak_days?: number;
}

/** Flexible presence row: GET /presence returns PresenceStateOut[] with
 *  {machine_id, zone, state, anomaly, person_present, active, employee_id}. */
interface PresenceRow {
  employee_id?: string;
  status?: string;
  state?: string;
}

type PanelState<T> = {
  status: 'idle' | 'loading' | 'ready' | 'empty' | 'unavailable';
  data: T | null;
  detail: string | null; // endpoint tried, or the error
};

// ---------------------------------------------------------------- helpers

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GET ${path} -> ${res.status}: ${body || res.statusText}`);
  }
  return (await res.json()) as T;
}

/** Append office_id to a path when an office filter is active. Only used
 *  with endpoints that support the filter (/activity, /presence,
 *  /focus/daily, /security/alerts). */
function withOffice(path: string, officeId: string): string {
  if (!officeId) return path;
  const sep = path.includes('?') ? '&' : '?';
  return `${path}${sep}office_id=${encodeURIComponent(officeId)}`;
}

function asArray<T>(v: unknown): T[] {
  if (Array.isArray(v)) return v as T[];
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    for (const k of ['items', 'results', 'rows', 'data']) {
      if (Array.isArray(o[k])) return o[k] as T[];
    }
    return [v as T]; // single-object response, e.g. FocusDailyOut
  }
  return [];
}

function pickFocusScore(row: FocusDailyRow): number | null {
  const v = row.focus_score ?? row.score ?? row.value;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function pickCompliant(row: StreakRow): boolean | null {
  if (typeof row.break_compliant === 'boolean') return row.break_compliant;
  if (typeof row.compliant === 'boolean') return row.compliant;
  if (typeof row.break_streak_days === 'number') return row.break_streak_days > 0;
  const bc = (row as { break_compliance?: { compliant?: boolean } }).break_compliance;
  if (bc && typeof bc.compliant === 'boolean') return bc.compliant;
  return null;
}

function pickStatus(row: PresenceRow): string {
  return String(row.status ?? row.state ?? 'unknown').toLowerCase();
}

function parseEmployees(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(',')) {
    const id = part.trim();
    if (id) seen.add(id);
  }
  return [...seen];
}

const DEFAULT_EMPLOYEES = parseEmployees(
  (import.meta.env.VITE_TEAM_EMPLOYEES as string | undefined) ?? '',
);

// ---------------------------------------------------------------- panels

function PanelShell(props: { title: string; endpoint: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h2>{props.title}</h2>
      <p className="muted">Source: {props.endpoint}</p>
      {props.children}
    </section>
  );
}

function PanelMessage({ text, kind }: { text: string; kind: 'error' | 'muted' }) {
  return <p className={kind}>{text}</p>;
}

/** Average focus score across the team (GET /focus/daily per employee). */
function FocusPanel({ employees, officeId }: { employees: string[]; officeId: string }) {
  const [state, setState] = useState<PanelState<{ avg: number; n: number }>>({
    status: 'idle', data: null, detail: null,
  });

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (employees.length === 0) {
        setState({ status: 'empty', data: null, detail: 'no employees selected' });
        return;
      }
      setState({ status: 'loading', data: null, detail: null });
      const scores: number[] = [];
      const errors: string[] = [];
      await Promise.all(
        employees.map(async (id) => {
          try {
            const rows = asArray<FocusDailyRow>(
              await getJson(withOffice(`/focus/daily?employee_id=${encodeURIComponent(id)}`, officeId)),
            );
            for (const r of rows) {
              const s = pickFocusScore(r);
              if (s !== null) scores.push(s);
            }
          } catch (err) {
            errors.push(err instanceof Error ? err.message : String(err));
          }
        }),
      );
      if (cancelled) return;
      if (scores.length > 0) {
        setState({
          status: 'ready',
          data: { avg: scores.reduce((a, b) => a + b, 0) / scores.length, n: scores.length },
          detail: null,
        });
      } else if (errors.length > 0) {
        setState({ status: 'unavailable', data: null, detail: errors[0] });
      } else {
        setState({ status: 'empty', data: null, detail: 'no focus rows returned' });
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [employees, officeId]);

  return (
    <PanelShell title="Team focus score" endpoint="GET /focus/daily?employee_id=...">
      {state.status === 'loading' && <PanelMessage text="Loading focus scores..." kind="muted" />}
      {state.status === 'ready' && state.data && (
        <div className="health-row">
          <div className="health-item">
            <span className="health-label">Average focus score</span>
            <span className="health-value">{state.data.avg.toFixed(1)}</span>
          </div>
          <div className="health-item">
            <span className="health-label">Samples</span>
            <span className="health-value">{state.data.n}</span>
          </div>
        </div>
      )}
      {state.status === 'empty' && <PanelMessage text={`No focus data: ${state.detail ?? 'nothing to show'}.`} kind="muted" />}
      {state.status === 'unavailable' && (
        <PanelMessage text={`Endpoint unavailable: ${state.detail}. This panel needs the /focus router.`} kind="error" />
      )}
    </PanelShell>
  );
}

/** Strain distribution across the team (GET /activity/strain-summary, heuristic fallback). */
function StrainPanel({ employees, officeId }: { employees: string[]; officeId: string }) {
  const [state, setState] = useState<PanelState<{ counts: Record<string, number>; heuristic: boolean }>>({
    status: 'idle', data: null, detail: null,
  });

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setState({ status: 'loading', data: null, detail: null });
      // Primary: strain-summary from the activity router.
      try {
        const items = asArray<StrainSummaryItem>(await getJson('/activity/strain-summary'));
        const wanted = new Set(employees.map((e) => e.toLowerCase()));
        const counts: Record<string, number> = { green: 0, amber: 0, red: 0, unknown: 0 };
        let matched = 0;
        for (const it of items) {
          if (wanted.size > 0 && !wanted.has(String(it.employee_id).toLowerCase())) continue;
          const lvl = ['green', 'amber', 'red'].includes(String(it.strain_level).toLowerCase())
            ? String(it.strain_level).toLowerCase()
            : 'unknown';
          counts[lvl] += 1;
          matched += 1;
        }
        if (cancelled) return;
        if (matched > 0) {
          setState({ status: 'ready', data: { counts, heuristic: false }, detail: null });
        } else {
          setState({ status: 'empty', data: null, detail: 'strain summary returned no rows for these employees' });
        }
        return;
      } catch {
        // fall through to heuristic fallback
      }
      // Heuristic fallback: derive a rough workload signal from /activity idle ratios.
      // Clearly labeled as heuristic, not a medical measure.
      if (employees.length === 0) {
        if (!cancelled) setState({ status: 'empty', data: null, detail: 'no employees selected' });
        return;
      }
      try {
        const counts: Record<string, number> = { green: 0, amber: 0, red: 0, unknown: 0 };
        let matched = 0;
        await Promise.all(
          employees.map(async (id) => {
            const items = asArray<ActivityItem>(
              await getJson(withOffice(`/activity?employee_id=${encodeURIComponent(id)}&limit=100`, officeId)),
            );
            if (items.length === 0) return;
            const idle = items.filter((i) => i.is_idle).length / items.length;
            const lvl = idle >= 0.5 ? 'red' : idle >= 0.25 ? 'amber' : 'green';
            counts[lvl] += 1;
            matched += 1;
          }),
        );
        if (cancelled) return;
        if (matched > 0) {
          setState({ status: 'ready', data: { counts, heuristic: true }, detail: null });
        } else {
          setState({ status: 'empty', data: null, detail: 'no activity rows for these employees' });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            status: 'unavailable',
            data: null,
            detail: err instanceof Error ? err.message : String(err),
          });
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [employees, officeId]);

  const counts = state.data?.counts;
  const total = counts ? counts.green + counts.amber + counts.red + counts.unknown : 0;

  return (
    <PanelShell title="Strain distribution" endpoint="GET /activity/strain-summary (fallback: /activity idle ratio)">
      {state.status === 'loading' && <PanelMessage text="Loading strain data..." kind="muted" />}
      {state.status === 'ready' && counts && (
        <>
          {state.data?.heuristic && (
            <p className="muted">
              Heuristic estimate from idle-time ratios, not real strain scores. Treat as approximate.
            </p>
          )}
          <div className="health-row">
            <div className="health-item"><span className="dot ok" /><span className="health-label">Green</span><span className="health-value">{counts.green}</span></div>
            <div className="health-item"><span className="health-label">Amber</span><span className="health-value">{counts.amber}</span></div>
            <div className="health-item"><span className="dot down" /><span className="health-label">Red</span><span className="health-value">{counts.red}</span></div>
            <div className="health-item"><span className="health-label">Unknown</span><span className="health-value">{counts.unknown}</span></div>
            <div className="health-item"><span className="health-label">Employees</span><span className="health-value">{total}</span></div>
          </div>
        </>
      )}
      {state.status === 'empty' && <PanelMessage text={`No strain data: ${state.detail ?? 'nothing to show'}.`} kind="muted" />}
      {state.status === 'unavailable' && (
        <PanelMessage text={`Endpoints unavailable: ${state.detail}. Tried /activity/strain-summary and /activity.`} kind="error" />
      )}
    </PanelShell>
  );
}

/** Break compliance % across the team (GET /focus/streaks per employee). */
function BreakCompliancePanel({ employees, officeId }: { employees: string[]; officeId: string }) {
  const [state, setState] = useState<PanelState<{ pct: number; compliant: number; n: number }>>({
    status: 'idle', data: null, detail: null,
  });

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (employees.length === 0) {
        setState({ status: 'empty', data: null, detail: 'no employees selected' });
        return;
      }
      setState({ status: 'loading', data: null, detail: null });
      let compliant = 0;
      let n = 0;
      let firstError: string | null = null;
      await Promise.all(
        employees.map(async (id) => {
          const q = encodeURIComponent(id);
          // Primary: /focus/streaks (FocusStreaksOut per employee).
          try {
            const rows = asArray<StreakRow>(await getJson(`/focus/streaks?employee_id=${q}`));
            const latest = rows[rows.length - 1];
            const c = latest ? pickCompliant(latest) : null;
            if (c !== null) {
              n += 1;
              if (c) compliant += 1;
              return;
            }
          } catch (err) {
            if (!firstError) firstError = err instanceof Error ? err.message : String(err);
          }
          // Fallback: today's break_compliance dict inside /focus/daily.
          try {
            const rows = asArray<StreakRow>(await getJson(withOffice(`/focus/daily?employee_id=${q}`, officeId)));
            const latest = rows[rows.length - 1];
            const c = latest ? pickCompliant(latest) : null;
            if (c !== null) {
              n += 1;
              if (c) compliant += 1;
            }
          } catch {
            // keep firstError from the primary attempt
          }
        }),
      );
      if (cancelled) return;
      if (n > 0) {
        setState({ status: 'ready', data: { pct: (100 * compliant) / n, compliant, n }, detail: null });
      } else if (firstError) {
        setState({ status: 'unavailable', data: null, detail: firstError });
      } else {
        setState({ status: 'empty', data: null, detail: 'no streak rows returned' });
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [employees, officeId]);

  return (
    <PanelShell title="Break compliance" endpoint="GET /focus/streaks?employee_id=... (fallback: /focus/daily)">
      {state.status === 'loading' && <PanelMessage text="Loading streak data..." kind="muted" />}
      {state.status === 'ready' && state.data && (
        <div className="health-row">
          <div className="health-item">
            <span className="health-label">Break compliant</span>
            <span className="health-value">{state.data.pct.toFixed(1)}%</span>
          </div>
          <div className="health-item">
            <span className="health-label">Compliant employees</span>
            <span className="health-value">{state.data.compliant} of {state.data.n}</span>
          </div>
        </div>
      )}
      {state.status === 'empty' && <PanelMessage text={`No streak data: ${state.detail ?? 'nothing to show'}.`} kind="muted" />}
      {state.status === 'unavailable' && (
        <PanelMessage text={`Endpoint unavailable: ${state.detail}. This panel needs the /focus router.`} kind="error" />
      )}
    </PanelShell>
  );
}

/** Team presence snapshot (GET /presence). */
function PresencePanel({ employees, officeId }: { employees: string[]; officeId: string }) {
  const [state, setState] = useState<PanelState<{ counts: Record<string, number>; list: { id: string; status: string }[] }>>({
    status: 'idle', data: null, detail: null,
  });

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setState({ status: 'loading', data: null, detail: null });
      try {
        const rows = asArray<PresenceRow>(await getJson(withOffice('/presence', officeId)));
        const wanted = new Set(employees.map((e) => e.toLowerCase()));
        const counts: Record<string, number> = {};
        const list: { id: string; status: string }[] = [];
        for (const r of rows) {
          const id = String(r.employee_id ?? '?');
          if (wanted.size > 0 && !wanted.has(id.toLowerCase())) continue;
          const s = pickStatus(r);
          counts[s] = (counts[s] ?? 0) + 1;
          list.push({ id, status: s });
        }
        if (cancelled) return;
        if (list.length > 0) {
          setState({ status: 'ready', data: { counts, list }, detail: null });
        } else {
          setState({ status: 'empty', data: null, detail: '/presence returned no rows for these employees' });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            status: 'unavailable',
            data: null,
            detail: err instanceof Error ? err.message : String(err),
          });
        }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [employees, officeId]);

  return (
    <PanelShell title="Team presence" endpoint="GET /presence">
      {state.status === 'loading' && <PanelMessage text="Loading presence..." kind="muted" />}
      {state.status === 'ready' && state.data && (
        <>
          <div className="health-row">
            {Object.entries(state.data.counts).map(([s, c]) => (
              <div className="health-item" key={s}>
                <span className="health-label">{s}</span>
                <span className="health-value">{c}</span>
              </div>
            ))}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Employee</th><th>Status</th></tr>
              </thead>
              <tbody>
                {state.data.list.map((r) => (
                  <tr key={r.id}><td>{r.id}</td><td>{r.status}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {state.status === 'empty' && <PanelMessage text={`No presence data: ${state.detail ?? 'nothing to show'}.`} kind="muted" />}
      {state.status === 'unavailable' && (
        <PanelMessage text={`Endpoint unavailable: ${state.detail}. This panel needs the /presence router.`} kind="error" />
      )}
    </PanelShell>
  );
}

// ---------------------------------------------------------------- view

export default function TeamView({ officeId = "" }: { officeId?: string }) {
  const [draft, setDraft] = useState(DEFAULT_EMPLOYEES.join(', '));
  const [employees, setEmployees] = useState<string[]>(DEFAULT_EMPLOYEES);
  const [refreshKey, setRefreshKey] = useState(0);

  const apply = useCallback(() => {
    setEmployees(parseEmployees(draft));
    setRefreshKey((k) => k + 1);
  }, [draft]);

  return (
    <section>
      <header className="app-header">
        <h1>Team view</h1>
        <p className="muted">Aggregates focus, strain, break compliance and presence per employee.</p>
      </header>

      <div className="panel">
        <div className="toolbar">
          <label htmlFor="team-employees">Employee ids (comma-separated)</label>
          <input
            id="team-employees"
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="E001, E002, E003"
            style={{ minWidth: '16rem' }}
          />
          <button type="button" onClick={apply}>Load team</button>
          <button type="button" onClick={() => setRefreshKey((k) => k + 1)}>Refresh</button>
        </div>
        <p className="muted">
          Defaults come from VITE_TEAM_EMPLOYEES. Each panel below is independent; if a
          backend endpoint is missing, only that panel reports it.
        </p>
      </div>

      <div key={refreshKey}>
        <FocusPanel employees={employees} officeId={officeId} />
        <StrainPanel employees={employees} officeId={officeId} />
        <BreakCompliancePanel employees={employees} officeId={officeId} />
        <PresencePanel employees={employees} officeId={officeId} />
      </div>
    </section>
  );
}
