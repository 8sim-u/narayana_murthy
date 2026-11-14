import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { getApiUrl, getMyGeofenceEvents } from '../api';
import type { GeofenceEvent } from '../types';

type ActivityDay = { day: string; focus_minutes: number; tasks_completed: number | null };
type AttendanceRecord = { employee_id: string; first_seen: string | null; last_seen: string | null; present_minutes: number; camera_minutes: number; computer_minutes: number; sources: string[] };
type DailyAttendance = { day: string; enabled: boolean; items: AttendanceRecord[] };
type RmssdSource = 'manual' | 'wearable_short_window' | 'overnight' | 'resting';
type WellnessDay = { day: string; rmssd_ms: number | null; rmssd_source: RmssdSource | null; resting_hr_bpm: number | null; sleep_minutes: number | null; readiness: number | null };
type SleepSession = { started_at: string; ended_at: string; duration_minutes: number; source: string };
type TrendHint = { key: string; title: string; reason: string; next_step: string; false_positive: boolean | null };
type Dashboard = {
  employee_id: string;
  day_timezone: string;
  activity: ActivityDay[];
  wellness: {
    consented: boolean;
    daily: WellnessDay[];
    sleep_sessions: SleepSession[];
    live: { heart_rate_bpm: number; rmssd_ms: number | null; observed_at: string } | null;
    hrv_trend: { available: boolean; flag: boolean; decline_percent: number | null; reason: string } | null;
    break_prompt: { show_prompt: boolean; score: number | null; reason: string } | null;
    breathing_sessions: number;
    breaks_today: { suggested: boolean; taken: boolean } | null;
  };
  predictions: TrendHint[];
  false_positive_reports: number;
  data_retention_days: number;
};

const sourceLabel: Record<RmssdSource, string> = { manual: 'Manual entry (source not independently verified)', wearable_short_window: 'Wearable short-window sample (not overnight/resting)', overnight: 'Overnight measurement (source-reported)', resting: 'Resting measurement (source-reported)' };
const API = getApiUrl();
const localToday = (zone = 'UTC') => new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const attendanceSourceLabel: Record<string, string> = { camera: 'Camera sightings', computer: 'Active computer activity' };
const formatAttendanceTime = (timestamp: string | null) => timestamp ? new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' }).format(new Date(timestamp)) : '—';
const formatAttendanceMinutes = (minutes: number) => minutes.toLocaleString(undefined, { maximumFractionDigits: 2 });

async function request<T>(token: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-Employee-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${API}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  if (!res.ok) {
    const message = await res.text();
    throw new Error(`Request failed (${res.status}): ${message || res.statusText}`);
  }
  return (await res.json()) as T;
}

export default function WellnessDashboard() {
  const [tokenInput, setTokenInput] = useState('');
  const [token, setToken] = useState('');
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const [data, setData] = useState<Dashboard | null>(null);
  const [geofenceEvents, setGeofenceEvents] = useState<GeofenceEvent[]>([]);
  const [attendance, setAttendance] = useState<DailyAttendance | null>(null);
  const [attendanceError, setAttendanceError] = useState('');
  const [managerAlertConsent, setManagerAlertConsent] = useState<boolean | null>(null);
  const [managerAlertConsentError, setManagerAlertConsentError] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [geofenceError, setGeofenceError] = useState('');
  const [notice, setNotice] = useState('');
  const [taskCount, setTaskCount] = useState('');
  const [rmssd, setRmssd] = useState('');
  const [rmssdSource, setRmssdSource] = useState<RmssdSource>('manual');
  const [restingHr, setRestingHr] = useState('');
  const [sleepStart, setSleepStart] = useState('');
  const [sleepEnd, setSleepEnd] = useState('');
  const [sleepSource, setSleepSource] = useState<'manual' | 'watch'>('manual');
  const [sessionId, setSessionId] = useState('');
  const [secondsLeft, setSecondsLeft] = useState(60);
  const [sessionMessage, setSessionMessage] = useState('');

  const refresh = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setManagerAlertConsent(null);
    setManagerAlertConsentError('');
    try {
      const dashboard = await request<Dashboard>(token, '/wellness/me');
      if (tokenRef.current !== token) return;
      setData(dashboard);
      try {
        const preference = await request<{ accepted: boolean }>(token, '/wellness/manager-alert-consent');
        if (tokenRef.current !== token) return;
        setManagerAlertConsent(preference.accepted);
        setManagerAlertConsentError('');
      } catch {
        if (tokenRef.current !== token) return;
        setManagerAlertConsent(null);
        setManagerAlertConsentError('Manager check-in preference could not be verified. Manager prompts remain disabled until it can be loaded.');
      }
      setError('');
    } catch (e) {
      if (tokenRef.current !== token) return;
      setManagerAlertConsent(null);
      setManagerAlertConsentError('Manager check-in preference could not be verified. Manager prompts remain disabled until it can be loaded.');
      setError(e instanceof Error ? e.message : 'Could not load your private dashboard');
    } finally {
      setLoading(false);
    }
  }, [token]);

  const refreshGeofenceEvents = useCallback(async () => {
    if (!token) return;
    try {
      const events = await getMyGeofenceEvents(token, 50);
      if (tokenRef.current !== token) return;
      setGeofenceEvents(events);
      setGeofenceError('');
    } catch (e) {
      if (tokenRef.current !== token) return;
      setGeofenceEvents([]);
      setGeofenceError(e instanceof Error ? e.message : 'Could not load your geofence history');
    }
  }, [token]);

  const refreshAttendance = useCallback(async () => {
    if (!token) return;
    try {
      const summary = await request<DailyAttendance>(token, '/attendance/daily');
      if (tokenRef.current !== token) return;
      setAttendance(summary);
      setAttendanceError('');
    } catch (e) {
      if (tokenRef.current !== token) return;
      setAttendance(null);
      setAttendanceError(e instanceof Error ? e.message : 'Could not load your daily attendance summary');
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    void refresh();
    void refreshGeofenceEvents();
    void refreshAttendance();
    const id = window.setInterval(() => {
      void refresh();
      void refreshGeofenceEvents();
      void refreshAttendance();
    }, 30000);
    return () => window.clearInterval(id);
  }, [token, refresh, refreshGeofenceEvents, refreshAttendance]);

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = tokenInput.trim();
    if (!candidate) return;
    setLoading(true);
    setError('');
    setManagerAlertConsent(null);
    setManagerAlertConsentError('');
    try {
      const loaded = await request<Dashboard>(candidate, '/wellness/me');
      let preference: { accepted: boolean } | null = null;
      try {
        preference = await request<{ accepted: boolean }>(candidate, '/wellness/manager-alert-consent');
      } catch {
        setManagerAlertConsentError('Manager check-in preference could not be verified. Manager prompts remain disabled until it can be loaded.');
      }
      tokenRef.current = candidate;
      setGeofenceEvents([]);
      setGeofenceError('');
      setAttendance(null);
      setAttendanceError('');
      setData(loaded);
      setToken(candidate);
      setManagerAlertConsent(preference?.accepted ?? null);
      setTokenInput('');
      setNotice('Connected to your private view. Your token is held only for this page session.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not verify personal token');
    } finally {
      setLoading(false);
    }
  };

  const disconnect = () => {
    tokenRef.current = '';
    setToken('');
    setData(null);
    setManagerAlertConsent(null);
    setManagerAlertConsentError('');
    setGeofenceEvents([]);
    setGeofenceError('');
    setAttendance(null);
    setAttendanceError('');
    setTokenInput('');
    setSessionId('');
    setNotice('Disconnected. The personal token was cleared from this page.');
  };

  const mutate = async (path: string, body: unknown, success: string) => {
    try {
      await request(token, path, body);
      setNotice(success);
      setError('');
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save this update');
    }
  };

  const dailyRows = useMemo(() => [...(data?.wellness.daily ?? [])].reverse(), [data]);
  const latestReadiness = dailyRows.find((row) => row.readiness !== null)?.readiness ?? '—';
  const beginBreathing = async () => {
    try {
      const result = await request<{ session_id: string; duration_seconds: number }>(token, '/wellness/breathing/start', {});
      setSessionId(result.session_id);
      setSecondsLeft(result.duration_seconds);
      setSessionMessage('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start breathing session');
    }
  };

  const finishBreathing = useCallback(async (id: string) => {
    try {
      const result = await request<{ completed: boolean; reason?: string; sensor_readings_available?: boolean }>(token, '/wellness/breathing/complete', { session_id: id });
      setSessionMessage(result.completed
        ? `Session timing completed.${result.sensor_readings_available ? ' Fresh before-and-after RMSSD samples were recorded.' : ` No usable before-and-after RMSSD comparison: ${result.reason ?? 'samples unavailable'}`} This does not establish a breathing effect or a medical result.`
        : `Session not completed. ${result.reason ?? 'Timing did not reach the completion threshold.'}`);
      setError('');
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save completed session');
    } finally {
      setSessionId('');
    }
  }, [token, refresh]);

  useEffect(() => {
    if (!sessionId) return;
    const timer = window.setInterval(() => {
      setSecondsLeft((left) => {
        if (left <= 1) {
          window.clearInterval(timer);
          void finishBreathing(sessionId);
          return 0;
        }
        return left - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [sessionId, finishBreathing]);

  if (!token || !data) {
    return (
      <div className="wellness-stack rise">
        <section className="panel wellness-intro">
          <p className="page-kicker">Employee-only view</p>
          <h2 className="section-title">Your private wellbeing dashboard</h2>
          <p>Use the individual token provided for you. An employee ID is not an access credential. The token is not saved in browser storage and is cleared when you disconnect or leave this page.</p>
          <form onSubmit={connect} className="wellness-connect">
            <label className="field"><span className="field-label">Personal access token</span>
              <input className="input" type="password" autoComplete="off" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} /></label>
            <button className="btn btn-primary" type="submit" disabled={loading || !tokenInput.trim()}>{loading ? 'Connecting…' : 'Open my view'}</button>
          </form>
          {error && <div className="notice notice-error" role="alert">{error}</div>}
          <p className="wellness-privacy-note">Health data is optional and needs separate consent. Managers never see your raw health values, sleep, readiness, breathing details, or task self-reports. Only if you separately opt in may a manager authorized for your roster receive your employee ID and a generic supportive check-in prompt.</p>
        </section>
      </div>
    );
  }

  const consent = data.wellness.consented;
  const todayActivity = data.activity.find((d) => d.day === localToday(data?.day_timezone ?? 'UTC'));
  const attendanceItem = attendance?.items.find((item) => item.employee_id === data.employee_id);
  const liveTime = data.wellness.live ? new Date(data.wellness.live.observed_at).toLocaleTimeString() : 'No fresh reading';

  return (
    <div className="wellness-stack rise">
      <section className="panel">
        <div className="section-head">
          <div><p className="page-kicker">Private to you · {data.employee_id}</p><h2 className="section-title">Your personal view</h2></div>
          <div className="form-row"><button className="btn" type="button" onClick={() => { void refresh(); void refreshGeofenceEvents(); void refreshAttendance(); }} disabled={loading}>Refresh</button><button className="btn" type="button" onClick={disconnect}>Disconnect</button></div>
        </div>
        <p className="wellness-privacy-note">Only aggregates for your own token are returned. The token is held in page memory, never local storage. Trend cards are self-coaching hints, not a diagnosis, medical advice, or performance rating. Managers never see raw health values or trend details; only a separate opt-in can enable a fixed supportive prompt and your employee ID for an authorized roster.</p>
        {notice && <p className="notice notice-ok" role="status">{notice}</p>}
        {error && <p className="notice notice-error" role="alert">{error}</p>}
      </section>

      <section className="panel">
        <div className="section-head"><h2 className="section-title">Workday reflection</h2><span className="section-meta">Recent 28 days · activity totals only</span></div>
        <div className="grid-3">
          <div className="stat"><div className="stat-value mono">{todayActivity ? Math.round(todayActivity.focus_minutes) : '—'}</div><div className="stat-label">Active minutes today</div></div>
          <div className="stat"><div className="stat-value mono">{todayActivity?.tasks_completed ?? '—'}</div><div className="stat-label">Tasks you reported today</div></div>
          <div className="stat"><div className="stat-value mono">{data.false_positive_reports}</div><div className="stat-label">False-positive reports</div></div>
        </div>
        <form className="wellness-connect" onSubmit={(e) => { e.preventDefault(); if (taskCount !== '') void mutate('/wellness/tasks', { day: localToday(data?.day_timezone ?? 'UTC'), tasks_completed: Number(taskCount) }, 'Your task-count self-report was saved.'); }}>
          <label className="field"><span className="field-label">Tasks completed today (optional)</span><input className="input" inputMode="numeric" type="number" min="0" max="1000" value={taskCount} onChange={(e) => setTaskCount(e.target.value)} /></label>
          <button className="btn" type="submit" disabled={taskCount === ''}>Save my count</button>
        </form>
        <div className="table-wrap"><table className="ledger"><thead><tr><th>Day</th><th>Active minutes</th><th>Your task count</th></tr></thead><tbody>
          {[...(data.activity ?? [])].slice(-7).reverse().map((d) => <tr key={d.day}><td>{d.day}</td><td>{Math.round(d.focus_minutes)}</td><td>{d.tasks_completed ?? '—'}</td></tr>)}
        </tbody></table></div>
      </section>

      <section className="panel" aria-labelledby="attendance-heading">
        <div className="section-head"><h2 className="section-title" id="attendance-heading">Your daily attendance</h2>{attendance && <span className="section-meta">UTC calendar day · {attendance.day}</span>}</div>
        <p className="wellness-privacy-note">This personal summary uses your employee-token-scoped attendance route. The day and first/last-seen times follow UTC; total present is the union of camera-dwell and active-computer intervals.</p>
        {attendanceError && <p className="notice notice-error" role="alert">{attendanceError}</p>}
        {!attendanceError && !attendance && <p className="section-meta">Loading your personal attendance summary…</p>}
        {attendance?.enabled === false && <p className="section-meta">Daily attendance summaries are disabled. No attendance totals are shown.</p>}
        {attendance?.enabled && !attendanceItem && <p className="section-meta">No attendance was recorded for this UTC day.</p>}
        {attendance?.enabled && attendanceItem && <>
          <div className="grid-3">
            <div className="stat"><div className="stat-value mono">{formatAttendanceTime(attendanceItem.first_seen)}</div><div className="stat-label">First seen (UTC)</div></div>
            <div className="stat"><div className="stat-value mono">{formatAttendanceTime(attendanceItem.last_seen)}</div><div className="stat-label">Last seen (UTC)</div></div>
            <div className="stat"><div className="stat-value mono">{formatAttendanceMinutes(attendanceItem.present_minutes)}</div><div className="stat-label">Total present (minutes)</div></div>
            <div className="stat"><div className="stat-value mono">{formatAttendanceMinutes(attendanceItem.camera_minutes)}</div><div className="stat-label">Camera minutes</div></div>
            <div className="stat"><div className="stat-value mono">{formatAttendanceMinutes(attendanceItem.computer_minutes)}</div><div className="stat-label">Computer minutes</div></div>
          </div>
          <p className="wellness-privacy-note"><strong>Sources:</strong> {attendanceItem.sources.map((source) => attendanceSourceLabel[source] ?? source).join(' · ') || 'None recorded'}</p>
        </>}
      </section>

      <section className="panel">
        <div className="section-head">
          <h2 className="section-title">Your geofence transitions</h2>
          <button className="btn" type="button" onClick={() => void refreshGeofenceEvents()}>Refresh</button>
        </div>
        <p className="wellness-privacy-note">Only transitions attributed to your employee token are shown. Legacy records without a stored employee owner and other employees' history are excluded. Names appear only in this private history; camera identifiers, exact coordinates, and tracker IDs are omitted.</p>
        {geofenceError && <p className="notice notice-error" role="alert">{geofenceError}</p>}
        {geofenceEvents.length === 0 ? (
          <p className="section-meta">No crossings are available for this token yet.</p>
        ) : (
          <div style={{ display: 'grid', gap: 8, maxHeight: 300, overflowY: 'auto' }}>
            {geofenceEvents.map((event) => (
              <div key={`${event.timestamp}:${event.zone_name}:${event.transition}`} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 10px', borderBottom: '1px solid var(--rule)', padding: '8px 0' }}>
                <span className={`chip ${event.transition === 'entry' ? 'chip-ok' : 'chip-warn'}`}>
                  {event.transition === 'entry' ? 'Entered' : 'Exited'}
                </span>
                <span><strong>{event.person_name}</strong> · {event.zone_name}</span>
                <span className="section-meta" style={{ gridColumn: '2' }}>{new Date(event.timestamp).toLocaleString()}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="section-head"><div><h2 className="section-title">Optional wellness data</h2><span className="section-meta">Separate consent · private to you</span></div></div>
        {!consent ? <div className="notice wellness-consent-box">
          <p><strong>Wellness sharing is off by default.</strong> If you opt in, you may enter date-only HRV/resting heart rate and record sleep sessions manually or import device-provided start/end times; live wearable readings expire from server memory after 90 seconds. Withdrawing consent deletes stored wellness summaries, completed-session measurements and break markers. It does not affect workstation activity consent.</p>
          <button type="button" className="btn btn-primary" onClick={() => void mutate('/wellness/consent', { accepted: true }, 'Optional wellness consent enabled. You can withdraw it here at any time.')}>I choose to opt in</button>
        </div> : <>
          <div className="grid-3">
            <div className="stat"><div className="stat-value mono">{data.wellness.live?.heart_rate_bpm ?? '—'}</div><div className="stat-label">Live heart rate · {liveTime}</div></div>
            <div className="stat"><div className="stat-value mono">{data.wellness.live?.rmssd_ms ?? '—'}</div><div className="stat-label">Live RMSSD (ms)</div></div>
            <div className="stat"><div className="stat-value mono">{latestReadiness}</div><div className="stat-label">Latest morning advisory readiness / 100</div></div>
          </div>
          <p className="wellness-privacy-note">Morning readiness is optional, non-clinical guidance using recorded sleep and available personal HRV/resting-HR comparisons; missing measurements are omitted, never inferred. It is private to you, not an employment score, must never be used to penalize employees, and is never shown to managers.</p>
          <form className="wellness-connect" onSubmit={(e) => { e.preventDefault(); const body: Record<string, unknown> = { day: localToday(data?.day_timezone ?? 'UTC') }; if (rmssd) { body.rmssd_ms = Number(rmssd); body.rmssd_source = rmssdSource; } if (restingHr) body.resting_hr_bpm = Number(restingHr); void mutate('/wellness/daily', body, 'Your available measurements were saved.'); }}>
            <label className="field"><span className="field-label">RMSSD (ms)</span><input className="input" type="number" min="1" max="300" step="0.1" value={rmssd} onChange={(e) => setRmssd(e.target.value)} /></label>
            <label className="field"><span className="field-label">RMSSD sample source</span><select className="input" value={rmssdSource} onChange={(e) => setRmssdSource(e.target.value as RmssdSource)}><option value="manual">Manual entry / unknown source</option><option value="wearable_short_window">Wearable short-window sample</option><option value="overnight">Overnight measurement</option><option value="resting">Resting measurement</option></select></label>
            <label className="field"><span className="field-label">Resting HR (bpm)</span><input className="input" type="number" min="20" max="250" value={restingHr} onChange={(e) => setRestingHr(e.target.value)} /></label>
            <button className="btn" type="submit" disabled={!rmssd && !restingHr}>Save measurements</button>
          </form>
          <section className="wellness-hrv-chart" aria-label="Personal RMSSD trend chart">
            <h3 className="section-title">Your RMSSD trend</h3>
            <p className="wellness-privacy-note">Daily values are shown with their recorded source. Overnight/resting provenance is source-reported and is not inferred; manual/unknown and short-window readings remain distinct.</p>
            {dailyRows.filter((d) => d.rmssd_ms !== null).length < 2 ? <p>At least two RMSSD readings are needed to draw a trend.</p> : <div className="wellness-chart-scroll"><svg role="img" aria-label="Daily RMSSD readings in milliseconds; labels identify each reading source" aria-describedby="wellness-rmssd-flag" viewBox={`0 0 640 220`} className="wellness-chart">
              {(() => { const points = dailyRows.filter((d) => d.rmssd_ms !== null).slice(-14); const vals = points.map((d) => d.rmssd_ms as number); const lo = Math.min(...vals), hi = Math.max(...vals); const y = (v: number) => 165 - (hi === lo ? 0.5 : (v-lo)/(hi-lo))*125; const x = (i: number) => 42 + i * (556 / Math.max(1, points.length-1)); return <>{[0,1,2,3].map(i => <line key={i} x1="38" x2="610" y1={40+i*42} y2={40+i*42} className="wellness-chart-grid"/>)}<polyline points={points.map((d,i)=>`${x(i)},${y(d.rmssd_ms as number)}`).join(' ')} className="wellness-chart-line" fill="none"/>{points.map((d,i)=><g key={d.day}><circle cx={x(i)} cy={y(d.rmssd_ms as number)} r="4" className="wellness-chart-point"><title>{`${d.day}: ${d.rmssd_ms} ms · ${sourceLabel[d.rmssd_source ?? 'manual']}`}</title></circle><text x={x(i)} y="190" textAnchor="middle" className="wellness-chart-label">{d.day.slice(5)}</text></g>)}</>; })()}
            </svg></div>}
            {data.wellness.hrv_trend && <p id="wellness-rmssd-flag" className={`notice${data.wellness.hrv_trend.flag ? ' notice-warn' : ''}`} role="note">
              <strong>{data.wellness.hrv_trend.flag ? 'Sustained decline flag:' : data.wellness.hrv_trend.available ? 'No sustained decline flag:' : 'No flag yet:'}</strong>{' '}
              {data.wellness.hrv_trend.available
                ? `${data.wellness.hrv_trend.decline_percent ?? 0}% decline across adjacent weekly medians.`
                : 'Not enough repeated readings to compare adjacent weeks.'}{' '}
              {data.wellness.hrv_trend.reason}
            </p>}
          </section>
          <form className="wellness-connect" onSubmit={(e) => { e.preventDefault(); if (sleepStart && sleepEnd) void mutate('/wellness/sleep-sessions', { started_at: new Date(sleepStart).toISOString(), ended_at: new Date(sleepEnd).toISOString(), source: sleepSource }, 'Sleep session saved privately.'); }}>
            <label className="field"><span className="field-label">Sleep started</span><input className="input" type="datetime-local" value={sleepStart} onChange={(e) => setSleepStart(e.target.value)} required /></label>
            <label className="field"><span className="field-label">Sleep ended (morning)</span><input className="input" type="datetime-local" value={sleepEnd} onChange={(e) => setSleepEnd(e.target.value)} required /></label>
            <label className="field"><span className="field-label">Entry source</span><select className="input" value={sleepSource} onChange={(e) => setSleepSource(e.target.value as 'manual' | 'watch')}><option value="manual">Manual</option><option value="watch">Watch import (start/end only)</option></select></label>
            <button className="btn" type="submit" disabled={!sleepStart || !sleepEnd}>Save sleep session</button>
          </form>
          <p className="wellness-privacy-note">No watch-health connector is configured here; watch import means entering times supplied by your device. Sessions are private, retained under the wellness policy, and deleted when you withdraw consent.</p>
          <div className="table-wrap"><table className="ledger"><thead><tr><th>Session ended</th><th>Duration (minutes)</th><th>Source</th></tr></thead><tbody>{data.wellness.sleep_sessions.slice(-7).reverse().map((session) => <tr key={`${session.started_at}-${session.ended_at}`}><td>{new Date(session.ended_at).toLocaleString()}</td><td>{session.duration_minutes}</td><td>{session.source}</td></tr>)}</tbody></table></div>
          {data.wellness.break_prompt?.show_prompt && (
            <div className="notice notice-warn">
              <strong>Consider a short pause.</strong> A recent work-pattern and personal HRV signal aligned; this is a private, non-medical nudge.
              <div className="form-row">
                <button className="btn" type="button" onClick={() => void mutate('/wellness/breaks', { event_type: 'suggested' }, 'Prompt noted in your private daily summary.')}>Note prompt</button>
                <button className="btn" type="button" onClick={() => void mutate('/wellness/breaks', { event_type: 'taken' }, 'Break noted in your private daily summary.')}>I took a break</button>
                <a className="btn" href="#wellness-breathing">Open optional breathing guide</a>
              </div>
            </div>
          )}
          <div id="wellness-breathing" className="wellness-breathing" tabIndex={-1}>
            <div><h3 className="section-title">One-minute breathing pause</h3><p>A timing guide for a gentle 4-second inhale / 6-second exhale rhythm. Participation is optional. Timing completion is recorded separately from whether usable RMSSD samples were available; neither establishes an effect or causal change.</p></div>
            {!sessionId ? <button className="btn btn-primary" type="button" onClick={() => void beginBreathing()}>Start session</button> : <div className="breathing-stage"><div className={`breathing-circle ${Math.floor((60 - secondsLeft) % 10) < 4 ? 'is-inhale' : 'is-exhale'}`} aria-hidden="true" /><strong>{Math.floor((60 - secondsLeft) % 10) < 4 ? 'Breathe in' : 'Breathe out'} · {secondsLeft}s left</strong><button className="btn" type="button" onClick={() => { const id = sessionId; setSessionId(''); void mutate('/wellness/breathing/cancel', { session_id: id }, 'Breathing session cancelled.'); }}>Cancel</button></div>}
          </div>
          {sessionMessage && <p className="notice" role="status">{sessionMessage}</p>}
          <div className="table-wrap"><table className="ledger"><thead><tr><th>Morning</th><th>RMSSD (source)</th><th>Resting HR</th><th>Sleep session minutes</th><th>Morning advisory readiness</th></tr></thead><tbody>
            {dailyRows.slice(0, 7).map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.rmssd_ms == null ? '—' : <>{d.rmssd_ms} · {sourceLabel[d.rmssd_source ?? 'manual']}</>}</td><td>{d.resting_hr_bpm ?? '—'}</td><td>{d.sleep_minutes ?? '—'}</td><td>{d.readiness ?? '—'}</td></tr>)}
          </tbody></table></div>
          <button type="button" className="btn btn-danger" onClick={() => { if (window.confirm('Withdraw optional wellness consent and permanently delete your stored wellness summaries, breathing-session measurements, and break markers?')) void mutate('/wellness/consent', { accepted: false }, 'Wellness consent withdrawn and health history deleted.'); }}>Withdraw and delete wellness history</button>
        </>}
      </section>

      <section className="panel" aria-label="Optional manager check-in consent">
        <div className="section-head"><div><h2 className="section-title">Optional manager check-ins</h2><span className="section-meta">Separate, explicit opt-in · off by default</span></div></div>
        <p>Only if both your wellness consent and this separate choice are on, a manager with a server-authorized roster may see your employee ID and one generic supportive prompt when a weekly median RMSSD decrease of at least 10% (with at least four readings per week) coincides with a drop of at least 30% in average non-idle active minutes (with at least four active days per week).</p>
        <p className="wellness-privacy-note">Recorded active time is an imperfect work-pattern proxy, not a measure of engagement or burnout; leave, schedule changes, holidays, offline work, or device/logging gaps can change it. The manager does not see your HRV values, trend direction, activity values, scores, or why the prompt appeared. It is only a request to offer support, never a performance, disciplinary, or other employment decision. You can withdraw this separate choice at any time; withdrawing wellness consent also turns it off.</p>
        {!consent && <p className="notice">Enable optional wellness consent above before you can separately opt in to manager check-ins.</p>}
        {managerAlertConsentError && <p className="notice notice-error" role="alert">{managerAlertConsentError}</p>}
        {managerAlertConsent === null
          ? <p>Consent status is being verified or unavailable, so manager prompts remain disabled.</p>
          : managerAlertConsent
            ? <button type="button" className="btn btn-danger" disabled={loading} onClick={() => void mutate('/wellness/manager-alert-consent', { accepted: false }, 'Your separate manager check-in opt-in was withdrawn.')}>Withdraw manager check-in opt-in</button>
            : <button type="button" className="btn btn-primary" disabled={!consent || loading} onClick={() => void mutate('/wellness/manager-alert-consent', { accepted: true }, 'Your separate manager check-in opt-in is on. You can withdraw it at any time.')}>I separately opt in to supportive manager check-ins</button>}
      </section>

      <section className="panel">
        <div className="section-head"><h2 className="section-title">Private trend checks</h2><span className="section-meta">No manager access · retention {data.data_retention_days} days</span></div>
        <p className="wellness-privacy-note">The activity-only advisory uses recorded active minutes already collected under your workstation consent; it does not need optional health consent. It is shown only in your token-authenticated view. Task-count comparisons are retrospective. Neither is a performance rating, validated forecast, or explanation of cause.</p>
        {data.predictions.length === 0 ? <p>No current hint. The active-time advisory needs at least four active days in each of three completed 7-day windows, plus a 10% week-over-week decline and a 20% drop below the prior-two-week average. A separate task-count comparison needs repeated self-reports.</p> : data.predictions.map((p) => <article className="wellness-prediction" key={p.key}>
          <h3>{p.title}</h3><p>{p.reason}</p><p><strong>Possible next step:</strong> {p.next_step}</p>
          <div className="form-row"><button className="btn" type="button" onClick={() => void mutate('/wellness/prediction-feedback', { prediction_key: p.key, false_positive: true }, 'Thanks - this hint was marked as a false positive.')}>This was a false positive</button><button className="btn" type="button" onClick={() => void mutate('/wellness/prediction-feedback', { prediction_key: p.key, false_positive: false }, 'Feedback saved.')}>Useful / not a false positive</button>{p.false_positive === true && <span className="section-meta">Marked false positive</span>}{p.false_positive === false && <span className="section-meta">Recorded as useful</span>}</div>
        </article>)}
      </section>
    </div>
  );
}
