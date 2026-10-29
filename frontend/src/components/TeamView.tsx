// TeamView - team-level dashboard view for WAMSS.
//
// Aggregates team metrics from the sibling routers (activity, focus,
// presence). Every panel fetches independently and degrades to a graceful
// "no data / endpoint unavailable" state if its endpoint is missing, so one
// failed panel never breaks the others.
//
// Uses VITE_API_URL (or localhost:8000) and VITE_TEAM_EMPLOYEES
// (comma-separated default employee ids) from env, plus a text field for
// ad-hoc employee lists.
import { useCallback, useEffect, useState } from 'react';
import { ArrowClockwise } from '@phosphor-icons/react';
import {
  Chip, Dot, EmptyState, ErrorNotice, Field, Meter, Reveal,
  SectionHead, SkeletonRows, Stat,
} from './ui';

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
    <section className="panel" aria-label={props.title}>
      <SectionHead title={props.title} meta={props.endpoint} />
      {props.children}
    </section>
  );
}

function scoreColor(score: number): string {
  if (score >= 70) return 'var(--ok)';
  if (score >= 40) return 'var(--warn)';
  return 'var(--bad)';
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
    <PanelShell title="Team Focus Score" endpoint="GET /focus/daily?employee_id=...">
      {state.status === 'loading' && <SkeletonRows rows={2} height={56} />}
      {state.status === 'ready' && state.data && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 28, flexWrap: 'wrap' }}>
          <Stat label="Average focus" value={state.data.avg.toFixed(1)} tone={scoreColor(state.data.avg)} />
          <div style={{ flex: 1, minWidth: 160 }}>
            <Meter value={state.data.avg} color={scoreColor(state.data.avg)} label={`Team average focus ${state.data.avg.toFixed(1)}`} />
          </div>
          <Stat label="Samples" value={state.data.n} mono />
        </div>
      )}
      {state.status === 'empty' && (
        <EmptyState title="No focus data" body={state.detail ?? 'Select employees to aggregate their focus scores.'} />
      )}
      {state.status === 'unavailable' && (
        <ErrorNotice message={`Focus endpoint unavailable: ${state.detail}. This panel needs the /focus router.`} />
      )}
    </PanelShell>
  );
}

/** Strain Distribution across the team (GET /activity/strain-summary, heuristic fallback). */
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
    <PanelShell title="Strain Distribution" endpoint="GET /activity/strain-summary (fallback: /activity idle ratio)">
      {state.status === 'loading' && <SkeletonRows rows={2} height={52} />}
      {state.status === 'ready' && counts && (
        <>
          {state.data?.heuristic && (
            <div className="notice notice-warn" role="note">
              <p>Heuristic estimate from idle-time ratios, not real strain scores. Treat as approximate.</p>
            </div>
          )}
          <div className="grid-4">
            <div className="stat">
              <div className="stat-value mono" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Dot tone="ok" /> {counts.green}
              </div>
              <div className="stat-label">Green</div>
            </div>
            <div className="stat">
              <div className="stat-value mono" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Dot tone="warn" /> {counts.amber}
              </div>
              <div className="stat-label">Amber</div>
            </div>
            <div className="stat">
              <div className="stat-value mono" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Dot tone="bad" /> {counts.red}
              </div>
              <div className="stat-label">Red</div>
            </div>
            <Stat label="Employees" value={total} mono />
          </div>
        </>
      )}
      {state.status === 'empty' && (
        <EmptyState title="No strain data" body={state.detail ?? 'Nothing to aggregate for these employees.'} />
      )}
      {state.status === 'unavailable' && (
        <ErrorNotice message={`Endpoints unavailable: ${state.detail}. Tried /activity/strain-summary and /activity.`} />
      )}
    </PanelShell>
  );
}

/** Break Compliance % across the team (GET /focus/streaks per employee). */
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
    <PanelShell title="Break Compliance" endpoint="GET /focus/streaks?employee_id=... (fallback: /focus/daily)">
      {state.status === 'loading' && <SkeletonRows rows={2} height={56} />}
      {state.status === 'ready' && state.data && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 28, flexWrap: 'wrap' }}>
          <Stat label="Break compliant" value={`${state.data.pct.toFixed(0)}%`} tone={state.data.pct >= 70 ? 'var(--ok)' : 'var(--warn)'} />
          <div style={{ flex: 1, minWidth: 160 }}>
            <Meter value={state.data.pct} color={state.data.pct >= 70 ? 'var(--ok)' : 'var(--warn)'} label={`Break Compliance ${state.data.pct.toFixed(1)} percent`} />
          </div>
          <Stat label="Compliant" value={`${state.data.compliant} of ${state.data.n}`} mono />
        </div>
      )}
      {state.status === 'empty' && (
        <EmptyState title="No streak data" body={state.detail ?? 'Nothing to aggregate for these employees.'} />
      )}
      {state.status === 'unavailable' && (
        <ErrorNotice message={`Focus endpoint unavailable: ${state.detail}. This panel needs the /focus router.`} />
      )}
    </PanelShell>
  );
}

/** Team Presence snapshot (GET /presence). */
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
    <PanelShell title="Team Presence" endpoint="GET /presence">
      {state.status === 'loading' && <SkeletonRows rows={3} height={44} />}
      {state.status === 'ready' && state.data && (
        <div className="table-wrap" style={{ border: 0 }}>
          <table className="ledger">
            <tbody>
              {state.data.list.map((r) => (
                <tr key={r.id}>
                  <td className="id">{r.id}</td>
                  <td style={{ textAlign: 'right' }}>
                    <Chip tone={r.status.includes('active') && !r.status.includes('absent') ? 'ok' : r.status.includes('absent') ? 'bad' : 'idle'}>
                      {r.status}
                    </Chip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {state.status === 'empty' && (
        <EmptyState title="No presence data" body={state.detail ?? 'Nothing to aggregate for these employees.'} />
      )}
      {state.status === 'unavailable' && (
        <ErrorNotice message={`Presence endpoint unavailable: ${state.detail}. This panel needs the /presence router.`} />
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
    <div className="rise">
      <Reveal>
        <section className="panel" aria-label="Team selection">
          <div className="section-head">
            <h2 className="section-title">Team Roster</h2>
            <div className="form-row" style={{ marginLeft: 'auto' }}>
              <Field label="Employee IDs">
                <input
                  className="input"
                  id="team-employees"
                  type="text"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="E001, E002, E003"
                  autoComplete="off"
                  spellCheck={false}
                  style={{ minWidth: '16rem', fontFamily: 'var(--font-mono)', fontSize: 13 }}
                />
              </Field>
              <button type="button" className="btn btn-primary" onClick={apply}>Load Team</button>
              <button type="button" className="btn" onClick={() => setRefreshKey((k) => k + 1)}>
                <ArrowClockwise size={15} weight="bold" aria-hidden="true" /> Refresh
              </button>
            </div>
          </div>
          <p style={{ color: 'var(--text-2)', fontSize: 13, margin: 0 }}>
            Defaults come from VITE_TEAM_EMPLOYEES. Each panel below is independent; if a
            backend endpoint is missing, only that panel reports it.
          </p>
        </section>
      </Reveal>

      <div key={refreshKey}>
        <Reveal><FocusPanel employees={employees} officeId={officeId} /></Reveal>
        <Reveal><StrainPanel employees={employees} officeId={officeId} /></Reveal>
        <Reveal><BreakCompliancePanel employees={employees} officeId={officeId} /></Reveal>
        <Reveal><PresencePanel employees={employees} officeId={officeId} /></Reveal>
      </div>
    </div>
  );
}
