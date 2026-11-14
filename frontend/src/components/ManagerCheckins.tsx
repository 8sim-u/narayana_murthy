import { useState, type FormEvent } from 'react';
import { getApiUrl } from '../api';

type Checkin = { employee_id: string; message: string };
type CheckinResponse = { checkins: Checkin[] };
const API = getApiUrl();

async function loadCheckins(managerToken: string): Promise<Checkin[]> {
  const response = await fetch(`${API}/wellness/manager-checkins`, {
    headers: { 'X-Manager-Token': managerToken },
    cache: 'no-store',
  });
  if (!response.ok) throw new Error(`Manager check-ins unavailable (HTTP ${response.status}).`);
  const result = (await response.json()) as CheckinResponse;
  if (!result || !Array.isArray(result.checkins)) throw new Error('Manager check-ins returned an invalid response.');
  return result.checkins;
}

export default function ManagerCheckins() {
  const [tokenInput, setTokenInput] = useState('');
  const [managerToken, setManagerToken] = useState('');
  const [checkins, setCheckins] = useState<Checkin[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const connect = async (event: FormEvent) => {
    event.preventDefault();
    const candidate = tokenInput.trim();
    if (!candidate) return;
    setLoading(true);
    setError('');
    setNotice('');
    try {
      const items = await loadCheckins(candidate);
      setManagerToken(candidate);
      setTokenInput('');
      setCheckins(items);
      setNotice('Connected to the server-authorized roster. The token is held only for this page session.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load manager check-ins.');
    } finally {
      setLoading(false);
    }
  };

  const refresh = async () => {
    if (!managerToken) return;
    setLoading(true);
    setError('');
    try {
      setCheckins(await loadCheckins(managerToken));
      setNotice('Check-in prompts refreshed.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load manager check-ins.');
    } finally {
      setLoading(false);
    }
  };

  const disconnect = () => {
    setManagerToken('');
    setTokenInput('');
    setCheckins([]);
    setError('');
    setNotice('Disconnected. The manager token was cleared from this page.');
  };

  return (
    <section className="panel" aria-label="Roster-scoped supportive check-ins">
      <div className="section-head">
        <div><p className="page-kicker">Manager-only · roster-scoped</p><h2 className="section-title">Supportive check-ins</h2></div>
        {managerToken && <div className="form-row">
          <button type="button" className="btn" onClick={() => void refresh()} disabled={loading}>Refresh</button>
          <button type="button" className="btn" onClick={disconnect}>Disconnect</button>
        </div>}
      </div>
      <p className="wellness-privacy-note">The server derives your roster from your manager token; this view does not use the browser’s editable team list. Only an employee ID and a generic prompt are shown - never HRV, health trends, activity values, scores, reasons, or employment consequences. Recorded active time is an imperfect work-pattern proxy: it does not establish engagement, identify high performers, or diagnose burnout, and may change for ordinary reasons such as leave, schedule changes, offline work, or data gaps. Use a prompt only to offer supportive help, never for performance or disciplinary decisions.</p>
      {!managerToken && <form className="wellness-connect" onSubmit={(event) => void connect(event)}>
        <label className="field"><span className="field-label">Manager access token</span>
          <input className="input" type="password" autoComplete="off" value={tokenInput} onChange={(event) => setTokenInput(event.target.value)} />
        </label>
        <button className="btn btn-primary" type="submit" disabled={loading || !tokenInput.trim()}>{loading ? 'Connecting…' : 'Open authorized check-ins'}</button>
      </form>}
      {notice && <p className="notice notice-ok" role="status">{notice}</p>}
      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {managerToken && !loading && !error && (checkins.length === 0
        ? <p>No supportive check-in prompts are available for your authorized roster.</p>
        : <ul>{checkins.map((item) => <li key={item.employee_id}>
          <strong>{item.employee_id}</strong><p>{item.message}</p>
        </li>)}</ul>)}
      {loading && <p aria-live="polite">Loading authorized check-ins…</p>}
    </section>
  );
}
