import { useCallback, useEffect, useState } from 'react';
import {
  Chip, EmptyState, ErrorNotice, Field, Reveal,
  SectionHead, SkeletonRows,
} from './ui';

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

interface PolicyViolation {
  policy: string;
  detail: string;
  severity: 'red' | 'amber';
}

interface PolicyRulesResponse {
  policy_file: string;
  rules: Record<string, unknown>;
  excluded_apps: string[];
  excluded_titles_regex: string | null;
  retention_days: number;
}

interface ViolationsResponse {
  employee_id: string;
  date: string;
  violations: PolicyViolation[];
  segment_count: number;
}

function todayISO(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function fmtClock(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '-');
}

export default function PolicyView() {
  const [rulesData, setRulesData] = useState<PolicyRulesResponse | null>(null);
  const [rulesError, setRulesError] = useState<string | null>(null);
  const [rulesLoading, setRulesLoading] = useState(true);

  const [employeeId, setEmployeeId] = useState('');
  const [date, setDate] = useState(todayISO());
  const [violations, setViolations] = useState<ViolationsResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const loadRules = useCallback(async () => {
    setRulesLoading(true);
    try {
      const res = await fetch(`${API_URL}/policy/rules`);
      if (!res.ok) throw new Error(`Rules request failed (${res.status})`);
      setRulesData((await res.json()) as PolicyRulesResponse);
      setRulesError(null);
    } catch (err) {
      setRulesError(err instanceof Error ? err.message : 'Could not load policy rules');
    } finally {
      setRulesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  const checkViolations = useCallback(async () => {
    if (!employeeId.trim()) {
      setCheckError('Enter an employee ID first.');
      return;
    }
    setChecking(true);
    setCheckError(null);
    try {
      const params = new URLSearchParams({ employee_id: employeeId.trim(), date });
      const res = await fetch(`${API_URL}/policy/violations?${params.toString()}`);
      if (!res.ok) throw new Error(`Violations request failed (${res.status})`);
      setViolations((await res.json()) as ViolationsResponse);
    } catch (err) {
      setCheckError(err instanceof Error ? err.message : 'Could not check violations');
    } finally {
      setChecking(false);
    }
  }, [employeeId, date]);

  const rules = rulesData?.rules ?? {};
  const workHours = (rules.work_hours as Record<string, unknown>) ?? {};
  const lunch = (rules.lunch_break as Record<string, unknown>) ?? {};
  const continuous = (rules.continuous_work as Record<string, unknown>) ?? {};
  const daily = (rules.daily as Record<string, unknown>) ?? {};

  return (
    <div className="rise">
      <Reveal>
        <section className="panel" aria-label="Working Time Rules">
          <SectionHead
            title="Working Time Rules"
            meta={rulesData ? rulesData.policy_file : undefined}
            actions={
              <button type="button" className="btn" onClick={() => { void loadRules(); }}>
                Reload
              </button>
            }
          />
          {rulesError && (
            <ErrorNotice message={rulesError} onRetry={() => { void loadRules(); }} />
          )}
          {rulesLoading && !rulesData ? (
            <SkeletonRows rows={4} height={44} />
          ) : rulesData ? (
            <ul className="spec-list">
              <li>
                <span className="spec-term">Work Window</span>
                <span className="spec-def">
                  {fmtClock(workHours.no_work_before)} to {fmtClock(workHours.no_work_after)}{' '}
                  ({String(rules.timezone ?? 'UTC')} local time). Activity outside this window is a high severity violation.
                </span>
              </li>
              <li>
                <span className="spec-term">Lunch Break</span>
                <span className="spec-def">
                  {fmtClock(lunch.window_start)} to {fmtClock(lunch.window_end)}, at least{' '}
                  {String(lunch.min_break_minutes ?? '-')} minutes with no activity. A missed break is medium severity.
                </span>
              </li>
              <li>
                <span className="spec-term">Continuous Work</span>
                <span className="spec-def">
                  Max {String(continuous.max_continuous_minutes ?? '-')} minutes without a break of{' '}
                  {String(continuous.break_reset_gap_minutes ?? '-')} minutes.
                </span>
              </li>
              <li>
                <span className="spec-term">Daily Cap</span>
                <span className="spec-def">Max {String(daily.max_active_hours ?? '-')} active hours per day.</span>
              </li>
            </ul>
          ) : null}
        </section>
      </Reveal>

      <Reveal>
        <section className="panel" aria-label="Check violations">
          <SectionHead title="Violation Check" />
          <div className="form-row">
            <Field label="Employee ID">
              <input
                className="input"
                value={employeeId}
                onChange={(e) => setEmployeeId(e.target.value)}
                placeholder="employee-001"
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
            <Field label="Date">
              <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <button type="button" className="btn btn-primary" onClick={() => { void checkViolations(); }} disabled={checking}>
              {checking ? 'Checking…' : 'Check'}
            </button>
          </div>

          {checkError && (
            <div className="notice notice-error rise" role="alert" style={{ marginTop: 14 }}>
              <p>{checkError}</p>
            </div>
          )}

          {violations && (
            <div className="rise" style={{ marginTop: 16 }}>
              <p style={{ color: 'var(--text-2)', fontSize: 13 }}>
                {violations.segment_count} activity segments evaluated for{' '}
                <span className="id" style={{ fontFamily: 'var(--font-mono)' }}>{violations.employee_id}</span> on{' '}
                {violations.date}.
              </p>
              {violations.violations.length === 0 ? (
                <EmptyState title="Clean day" body="No policy violations found for this employee and date." />
              ) : (
                <div className="table-wrap">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>Policy</th>
                        <th>Detail</th>
                        <th>Severity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {violations.violations.map((v, i) => (
                        <tr key={i} className={v.severity === 'red' ? 'row-bad' : 'row-alert'}>
                          <td style={{ fontWeight: 600 }}>{v.policy}</td>
                          <td>{v.detail}</td>
                          <td>
                            <Chip tone={v.severity === 'red' ? 'bad' : 'warn'}>
                              {v.severity === 'red' ? 'High' : 'Medium'}
                            </Chip>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </section>
      </Reveal>

      <Reveal>
        <section className="panel" aria-label="Privacy Controls">
          <SectionHead title="Privacy Controls" />
          <ul className="spec-list">
            <li>
              <span className="spec-term">Excluded Applications</span>
              <span className="spec-def">
                {rulesData && rulesData.excluded_apps.length > 0
                  ? rulesData.excluded_apps.join(', ')
                  : 'none configured'}
              </span>
            </li>
            <li>
              <span className="spec-term">Excluded Title Pattern</span>
              <span className="spec-def" style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>
                {rulesData?.excluded_titles_regex ?? 'none configured'}
              </span>
            </li>
            <li>
              <span className="spec-term">Data Retention</span>
              <span className="spec-def">
                Logs older than {rulesData?.retention_days ?? 90} days are deleted automatically.
              </span>
            </li>
            <li>
              <span className="spec-term">Capture Limits</span>
              <span className="spec-def">
                Only input counts are recorded. Keystroke content is never captured. First-run consent
                is required on the workstation before any logging starts.
              </span>
            </li>
          </ul>
        </section>
      </Reveal>
    </div>
  );
}
