// TeamView - team-level overview for WAMSS.
//
// One per-person table (who, how much, any flags) backed by the same
// per-employee endpoints the old panels used, plus a summary row of the
// aggregates a manager actually needs. Every panel fetch degrades to a
// quiet "-" cell, so one missing endpoint never breaks the others.
//
// Uses VITE_API_URL (or localhost:8000) and VITE_TEAM_EMPLOYEES
// (comma-separated default employee ids) from env, plus a text field for
// ad-hoc employee lists.
import { useCallback, useEffect, useState } from 'react';
import { ArrowClockwise } from '@phosphor-icons/react';
import {
  Chip, Dot, EmptyState, Field, Meter, Reveal,
  SectionHead, SkeletonRows,
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
  anomaly?: boolean;
}

// Per-person row assembled from four endpoints. Each cell carries its own
// state so one unavailable endpoint shows "-" instead of failing the row.
interface TeamRow {
  employee_id: string;
  focus: number | null;
  focusError: boolean;
  strainLevel: string | null; // green | amber | red | unknown | null (unavailable)
  strainHeuristic: boolean;
  breakCompliant: boolean | null;
  breakError: boolean;
  presence: string | null;
  presenceAnomaly: boolean;
  presenceError: boolean;
}

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

function strainTone(level: string | null): 'ok' | 'warn' | 'bad' | 'idle' | null {
  if (level === null) return null;
  if (level === 'green') return 'ok';
  if (level === 'amber') return 'warn';
  if (level === 'red') return 'bad';
  return 'idle';
}

function focusColor(score: number): string {
  if (score >= 70) return 'var(--ok)';
  if (score >= 40) return 'var(--warn)';
  return 'var(--bad)';
}

// ---------------------------------------------------------------- fetch

async function loadTeamRow(employeeId: string, officeId: string): Promise<TeamRow> {
  const row: TeamRow = {
    employee_id: employeeId,
    focus: null,
    focusError: false,
    strainLevel: null,
    strainHeuristic: false,
    breakCompliant: null,
    breakError: false,
    presence: null,
    presenceAnomaly: false,
    presenceError: false,
  };
  const q = encodeURIComponent(employeeId);

  try {
    const rows = asArray<FocusDailyRow>(
      await getJson(withOffice(`/focus/daily?employee_id=${q}`, officeId)),
    );
    for (const r of rows) {
      const s = pickFocusScore(r);
      if (s !== null) {
        row.focus = s;
        break;
      }
    }
    if (row.focus === null) row.focusError = true;
  } catch {
    row.focusError = true;
  }

  // Break compliance: primary /focus/streaks, fallback to /focus/daily.
  try {
    const rows = asArray<StreakRow>(await getJson(`/focus/streaks?employee_id=${q}`));
    const latest = rows[rows.length - 1];
    row.breakCompliant = latest ? pickCompliant(latest) : null;
  } catch {
    try {
      const rows = asArray<StreakRow>(
        await getJson(withOffice(`/focus/daily?employee_id=${q}`, officeId)),
      );
      const latest = rows[rows.length - 1];
      row.breakCompliant = latest ? pickCompliant(latest) : null;
    } catch {
      // leave null
    }
  }
  if (row.breakCompliant === null) row.breakError = true;

  // Strain: primary strain-summary, heuristic idle-ratio fallback.
  try {
    const items = asArray<StrainSummaryItem>(await getJson('/activity/strain-summary'));
    const mine = items.filter(
      (it) => String(it.employee_id).toLowerCase() === employeeId.toLowerCase(),
    );
    if (mine.length > 0) {
      // Worst level wins: red over amber over green over unknown.
      const rank: Record<string, number> = { red: 3, amber: 2, green: 1, unknown: 0 };
      let worst = mine[0].strain_level.toLowerCase();
      for (const it of mine.slice(1)) {
        const lvl = ['green', 'amber', 'red'].includes(it.strain_level.toLowerCase())
          ? it.strain_level.toLowerCase()
          : 'unknown';
        if ((rank[lvl] ?? 0) > (rank[worst] ?? 0)) worst = lvl;
      }
      row.strainLevel = worst;
    }
  } catch {
    try {
      const items = asArray<ActivityItem>(
        await getJson(withOffice(`/activity?employee_id=${q}&limit=100`, officeId)),
      );
      if (items.length > 0) {
        const idle = items.filter((i) => i.is_idle).length / items.length;
        row.strainLevel = idle >= 0.5 ? 'red' : idle >= 0.25 ? 'amber' : 'green';
        row.strainHeuristic = true;
      }
    } catch {
      // leave null
    }
  }

  try {
    const rows = asArray<PresenceRow>(await getJson(withOffice('/presence', officeId)));
    const mine = rows.find(
      (r) => String(r.employee_id ?? '').toLowerCase() === employeeId.toLowerCase(),
    );
    if (mine) {
      row.presence = pickStatus(mine);
      row.presenceAnomaly = !!mine.anomaly;
    }
  } catch {
    row.presenceError = true;
  }

  return row;
}

// ---------------------------------------------------------------- view

function presenceTone(status: string | null): 'ok' | 'warn' | 'bad' | 'idle' | null {
  if (status === null) return null;
  if (status.includes('active') && !status.includes('absent')) return 'ok';
  if (status.includes('absent')) return 'bad';
  if (status.includes('idle')) return 'warn';
  return 'idle';
}

export default function TeamView({ officeId = "" }: { officeId?: string }) {
  const [draft, setDraft] = useState(DEFAULT_EMPLOYEES.join(', '));
  const [employees, setEmployees] = useState<string[]>(DEFAULT_EMPLOYEES);
  const [rows, setRows] = useState<TeamRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const apply = useCallback(() => {
    setEmployees(parseEmployees(draft));
    setRefreshKey((k) => k + 1);
  }, [draft]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (employees.length === 0) {
        setRows([]);
        return;
      }
      setLoading(true);
      const results = await Promise.all(
        employees.map((id) => loadTeamRow(id, officeId)),
      );
      if (!cancelled) {
        setRows(results);
        setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [employees, officeId, refreshKey]);

  // Summary aggregates computed from the same rows the table shows, so the
  // totals and the per-person cells can never disagree.
  const focusScores = rows.filter((r) => r.focus !== null).map((r) => r.focus as number);
  const avgFocus = focusScores.length > 0
    ? focusScores.reduce((a, b) => a + b, 0) / focusScores.length
    : null;
  const compliantCount = rows.filter((r) => r.breakCompliant === true).length;
  const complianceTotal = rows.filter((r) => r.breakCompliant !== null).length;
  const strainCounts = { green: 0, amber: 0, red: 0 };
  for (const r of rows) {
    if (r.strainLevel === 'green') strainCounts.green += 1;
    if (r.strainLevel === 'amber') strainCounts.amber += 1;
    if (r.strainLevel === 'red') strainCounts.red += 1;
  }
  const presentNow = rows.filter((r) => {
    const tone = presenceTone(r.presence);
    return tone === 'ok';
  }).length;
  const flagged = rows.filter(
    (r) => r.strainLevel === 'red' || r.presenceAnomaly ||
      (r.breakCompliant === false),
  ).length;

  const anyEndpointError = rows.some(
    (r) => r.focusError || r.breakError || r.presenceError,
  );

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
            Defaults come from VITE_TEAM_EMPLOYEES. Each cell loads from its own
            endpoint; a missing endpoint shows a quiet dash, never a wrong number.
          </p>
        </section>
      </Reveal>

      <Reveal>
        <section className="panel" aria-label="Team summary">
          <SectionHead title="Summary" meta={`${rows.length} people`} />
          {loading && rows.length === 0 ? (
            <SkeletonRows rows={1} height={64} />
          ) : employees.length === 0 ? (
            <EmptyState title="No employees selected" body="Enter employee IDs above and load the team to see aggregates." />
          ) : (
            <div className="grid-4">
              <div className="stat">
                <div className="stat-value mono" style={{ color: avgFocus !== null ? focusColor(avgFocus) : undefined }}>
                  {avgFocus !== null ? avgFocus.toFixed(1) : '-'}
                </div>
                <div className="stat-label">Average focus</div>
                <div className="stat-sub">{focusScores.length} of {rows.length} people have a score</div>
              </div>
              <div className="stat">
                <div className="stat-value mono">
                  {complianceTotal > 0 ? `${Math.round((100 * compliantCount) / complianceTotal)}%` : '-'}
                </div>
                <div className="stat-label">Break compliant</div>
                <div className="stat-sub">{compliantCount} of {complianceTotal} measured</div>
              </div>
              <div className="stat">
                <div className="stat-value mono" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Dot tone="ok" />{strainCounts.green}</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Dot tone="warn" />{strainCounts.amber}</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Dot tone="bad" />{strainCounts.red}</span>
                </div>
                <div className="stat-label">Strain levels</div>
                <div className="stat-sub">green, amber, red per person</div>
              </div>
              <div className="stat">
                <div className="stat-value mono">{presentNow}</div>
                <div className="stat-label">Present and active now</div>
                <div className="stat-sub">{flagged} flagged for attention</div>
              </div>
            </div>
          )}
          <div style={{ marginTop: 14 }}>
            {avgFocus !== null && (
              <Meter value={avgFocus} color={focusColor(avgFocus)} label={`Team average focus ${avgFocus.toFixed(1)}`} />
            )}
          </div>
        </section>
      </Reveal>

      <Reveal>
        <section className="panel" aria-label="Per person">
          <SectionHead title="Per person" meta="who, how much, any flags" />
          {loading && rows.length === 0 ? (
            <SkeletonRows rows={4} height={52} />
          ) : employees.length === 0 ? (
            <EmptyState title="No employees selected" body="Load a roster to see per-person focus, strain, break compliance and presence." />
          ) : (
            <>
              {anyEndpointError && (
                <div className="notice notice-warn" role="note">
                  <p>
                    Some endpoints did not answer for every person; those cells show a
                    dash. The dashboard never substitutes an estimate for a missing value.
                  </p>
                </div>
              )}
              <div className="table-wrap">
                <table className="ledger">
                  <thead>
                    <tr>
                      <th>Employee</th>
                      <th>Focus</th>
                      <th>Strain</th>
                      <th>Break compliance</th>
                      <th>Presence now</th>
                      <th>Flag</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const sTone = strainTone(r.strainLevel);
                      const pTone = presenceTone(r.presence);
                      const isFlagged =
                        r.strainLevel === 'red' || r.presenceAnomaly || r.breakCompliant === false;
                      return (
                        <tr key={r.employee_id} className={isFlagged ? 'row-alert' : undefined}>
                          <td className="id">{r.employee_id}</td>
                          <td className="num" style={{ color: r.focus !== null ? focusColor(r.focus) : undefined }}>
                            {r.focus !== null ? r.focus.toFixed(1) : '-'}
                          </td>
                          <td>
                            {r.strainLevel === null
                              ? '-'
                              : <Chip tone={sTone ?? 'idle'}>{r.strainLevel}{r.strainHeuristic ? ' (heuristic)' : ''}</Chip>}
                          </td>
                          <td>
                            {r.breakCompliant === null
                              ? '-'
                              : <Chip tone={r.breakCompliant ? 'ok' : 'warn'}>{r.breakCompliant ? 'compliant' : 'needs break'}</Chip>}
                          </td>
                          <td>
                            {r.presence === null
                              ? '-'
                              : <Chip tone={pTone ?? 'idle'}>{r.presence}</Chip>}
                          </td>
                          <td>
                            {isFlagged
                              ? <Chip tone="bad">{r.presenceAnomaly ? 'presence anomaly' : r.strainLevel === 'red' ? 'high strain' : 'needs break'}</Chip>
                              : '-'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      </Reveal>
    </div>
  );
}
