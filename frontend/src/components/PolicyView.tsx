import { useCallback, useEffect, useState } from 'react';

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

function severityStyle(severity: 'red' | 'amber'): React.CSSProperties {
  return severity === 'red'
    ? { backgroundColor: '#fde8e8', color: '#a31111' }
    : { backgroundColor: '#fff6e0', color: '#8a5a00' };
}

function severityLabel(severity: 'red' | 'amber'): string {
  return severity === 'red' ? 'High' : 'Medium';
}

function fmtClock(value: unknown): string {
  return typeof value === 'string' ? value : String(value ?? '-');
}

export default function PolicyView() {
  const [rulesData, setRulesData] = useState<PolicyRulesResponse | null>(null);
  const [rulesError, setRulesError] = useState<string | null>(null);

  const [employeeId, setEmployeeId] = useState('');
  const [date, setDate] = useState(todayISO());
  const [violations, setViolations] = useState<ViolationsResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const loadRules = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/policy/rules`);
      if (!res.ok) throw new Error(`Rules request failed (${res.status})`);
      setRulesData((await res.json()) as PolicyRulesResponse);
      setRulesError(null);
    } catch (err) {
      setRulesError(err instanceof Error ? err.message : 'Could not load policy rules');
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
    <section className="panel">
      <h2>Policy &amp; Privacy</h2>

      <div className="toolbar">
        <h3 style={{ margin: 0 }}>Working time rules</h3>
        <button onClick={() => void loadRules()}>Reload</button>
      </div>
      {rulesError && <p className="error">{rulesError}</p>}
      {rulesData && (
        <ul>
          <li>
            Work window: {fmtClock(workHours.no_work_before)} to {fmtClock(workHours.no_work_after)}{' '}
            ({String(rules.timezone ?? 'UTC')} local time). Activity outside this window is a high severity
            violation.
          </li>
          <li>
            Lunch break: {fmtClock(lunch.window_start)} to {fmtClock(lunch.window_end)}, at least{' '}
            {String(lunch.min_break_minutes ?? '-')} minutes with no activity. A missed break is medium severity.
          </li>
          <li>
            Max continuous active work: {String(continuous.max_continuous_minutes ?? '-')} minutes without a
            break of {String(continuous.break_reset_gap_minutes ?? '-')} minutes.
          </li>
          <li>Max active time per day: {String(daily.max_active_hours ?? '-')} hours.</li>
        </ul>
      )}

      <div className="toolbar">
        <h3 style={{ margin: 0 }}>Check violations</h3>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <label>
          Employee ID:{' '}
          <input
            value={employeeId}
            onChange={(e) => setEmployeeId(e.target.value)}
            placeholder="employee-001"
          />
        </label>
        <label>
          Date: <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <button onClick={() => void checkViolations()} disabled={checking}>
          {checking ? 'Checking...' : 'Check'}
        </button>
      </div>
      {checkError && <p className="error">{checkError}</p>}
      {violations && (
        <div>
          <p className="muted">
            {violations.segment_count} activity segments evaluated for {violations.employee_id} on{' '}
            {violations.date}.
          </p>
          {violations.violations.length === 0 ? (
            <p className="muted">No policy violations. Clean day.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>Detail</th>
                  <th>Severity</th>
                </tr>
              </thead>
              <tbody>
                {violations.violations.map((v, i) => (
                  <tr key={i}>
                    <td>{v.policy}</td>
                    <td>{v.detail}</td>
                    <td>
                      <span className="badge" style={severityStyle(v.severity)}>
                        {severityLabel(v.severity)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <div className="toolbar">
        <h3 style={{ margin: 0 }}>Privacy controls</h3>
      </div>
      <ul>
        <li>
          Excluded applications (never logged):{' '}
          {rulesData && rulesData.excluded_apps.length > 0
            ? rulesData.excluded_apps.join(', ')
            : 'none configured'}
        </li>
        <li>
          Excluded window-title pattern:{' '}
          {rulesData?.excluded_titles_regex ?? 'none configured'}
        </li>
        <li>
          Data retention: logs older than {rulesData?.retention_days ?? 90} days are deleted automatically.
        </li>
        <li>Only input counts are recorded. Keystroke content is never captured.</li>
        <li>First-run consent is required on the workstation before any logging starts.</li>
      </ul>
    </section>
  );
}
