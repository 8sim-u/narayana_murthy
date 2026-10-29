import { useEffect, useState } from 'react';
import { fetchDbHealth, fetchHealth, getApiUrl } from '../api';
import type { DbHealthStatus, HealthStatus } from '../types';

type Indicator = 'ok' | 'down';

function StatusDot({ state }: { state: Indicator }) {
  return <span className={`dot ${state}`} aria-label={state} />;
}

export default function HealthPanel() {
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

  const apiState: Indicator = api && (api.status === 'online' || api.status === 'ok') ? 'ok' : 'down';
  const dbState: Indicator = db && (db.status === 'ok' || db.database === 'connected') ? 'ok' : 'down';

  return (
    <section className="panel health-panel">
      <h2>Backend health</h2>
      {error && <p className="error">Could not reach backend at {getApiUrl()}: {error}</p>}
      <div className="health-row">
        <div className="health-item">
          <StatusDot state={apiState} />
          <span className="health-label">API</span>
          <span className="health-value">
            {api ? `${api.status} (${api.service}, env: ${api.environment})` : 'unreachable'}
          </span>
        </div>
        <div className="health-item">
          <StatusDot state={dbState} />
          <span className="health-label">Database</span>
          <span className="health-value">{db ? db.database : 'unreachable'}</span>
        </div>
      </div>
    </section>
  );
}
