// Backend health readout for the dark masthead.
// Same data as before: GET /health and GET /health/db, 10s poll.
import { useEffect, useState } from 'react';
import { fetchDbHealth, fetchHealth, getApiUrl } from '../api';
import type { DbHealthStatus, HealthStatus } from '../types';

export default function MastStatus() {
  const [api, setApi] = useState<HealthStatus | null>(null);
  const [db, setDb] = useState<DbHealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [h, d] = await Promise.all([fetchHealth(), fetchDbHealth()]);
        if (cancelled) return;
        setApi(h);
        setDb(d);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Health check failed');
        setApi(null);
        setDb(null);
      }
    };
    void load();
    const id = window.setInterval(load, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const apiOk = api != null && (api.status === 'online' || api.status === 'ok');
  const dbOk = db != null && (db.status === 'ok' || db.database === 'connected');
  const ok = apiOk && dbOk && !error;

  const detail = error
    ? `Unreachable at ${getApiUrl()}: ${error}`
    : `API ${api ? api.status : '?'} (${api?.environment ?? '?'}) / DB ${db ? db.database : '?'}`;

  return (
    <span className="mast-status" title={detail} aria-live="polite">
      <span className={`signal-dot${ok ? '' : ' down'}`} aria-hidden="true" />
      <span>{ok ? 'LIVE' : 'DOWN'}</span>
    </span>
  );
}
