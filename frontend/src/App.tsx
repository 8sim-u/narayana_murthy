import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchEvents } from './api';
import type { DetectionEvent } from './types';
import HealthPanel from './components/HealthPanel';
import EventsTable from './components/EventsTable';
import CameraFilter from './components/CameraFilter';
import TestEventForm from './components/TestEventForm';
import EmployeeActivity from './components/EmployeeActivity';
import PresenceView from './components/PresenceView';
import FocusWidget from './components/FocusWidget';
import SecurityAlerts from './components/SecurityAlerts';
import PolicyView from './components/PolicyView';
import TeamView from './components/TeamView';
import AnalyticsTab from './components/AnalyticsTab';
import OfficeSelector from './components/OfficeSelector';
import ExportButton from './components/ExportButton';

const POLL_INTERVAL_MS = 5000;
const POLL_LIMIT = 100;

type Tab = 'events' | 'activity' | 'presence' | 'focus' | 'security' | 'policy' | 'team' | 'analytics';

const TABS: { id: Tab; label: string }[] = [
  { id: 'events', label: 'Vision Events' },
  { id: 'activity', label: 'Workstation Activity' },
  { id: 'presence', label: 'Presence' },
  { id: 'focus', label: 'Focus Score' },
  { id: 'security', label: 'Security' },
  { id: 'policy', label: 'Policy' },
  { id: 'team', label: 'Team' },
  { id: 'analytics', label: 'Trends' },
];

export default function App() {
  const [tab, setTab] = useState<Tab>('events');
  const [events, setEvents] = useState<DetectionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cameraFilter, setCameraFilter] = useState('');
  const [officeId, setOfficeId] = useState(''); // "" = all offices

  const loadEvents = useCallback(async () => {
    try {
      const data = await fetchEvents(cameraFilter || undefined, POLL_LIMIT);
      setEvents(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load events');
    } finally {
      setLoading(false);
    }
  }, [cameraFilter]);

  useEffect(() => {
    if (tab !== 'events') return;
    setLoading(true);
    void loadEvents();
    const id = window.setInterval(loadEvents, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [loadEvents, tab]);

  const cameras = useMemo(() => {
    const seen = new Set<string>();
    for (const e of events) {
      seen.add(e.camera_id);
    }
    return [...seen].sort();
  }, [events]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>WAMSS Dashboard</h1>
        <p className="muted">Workplace Activity Monitoring &amp; Surveillance System</p>
        <OfficeSelector value={officeId} onChange={setOfficeId} />
      </header>

      <nav className="tab-bar" aria-label="Dashboard sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? 'tab active' : 'tab'}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main>
        <HealthPanel />

        {tab === 'events' && (
          <>
            <section className="panel">
              <div className="toolbar">
                <h2>Events ({events.length})</h2>
                <CameraFilter cameras={cameras} selected={cameraFilter} onChange={setCameraFilter} />
                <ExportButton
                  endpoint="/exports/events.csv"
                  label="Export CSV"
                  filters={{ camera_id: cameraFilter || undefined, limit: 1000 }}
                  filename="events.csv"
                />
              </div>
              {error && <p className="error">Could not load events: {error}</p>}
              <EventsTable events={events} loading={loading} />
            </section>

            <TestEventForm onSent={loadEvents} />
          </>
        )}

        {tab === 'activity' && <EmployeeActivity officeId={officeId} />}
        {tab === 'presence' && <PresenceView officeId={officeId} />}
        {tab === 'focus' && <FocusWidget officeId={officeId} />}
        {tab === 'security' && <SecurityAlerts officeId={officeId} />}
        {tab === 'policy' && <PolicyView />}
        {tab === 'team' && <TeamView officeId={officeId} />}
        {tab === 'analytics' && <AnalyticsTab />}
      </main>

      <footer className="muted">
        Events refresh every {POLL_INTERVAL_MS / 1000} seconds. Rows highlighted red/amber need review.
      </footer>
    </div>
  );
}
