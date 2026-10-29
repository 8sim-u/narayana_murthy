import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchEvents } from './api';
import type { DetectionEvent } from './types';
import MastStatus from './components/MastStatus';
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

const TABS: { id: Tab; label: string; kicker: string; blurb: string }[] = [
  { id: 'events', label: 'Vision Events', kicker: 'Camera network', blurb: 'Detection feed from every camera zone, refreshed live.' },
  { id: 'activity', label: 'Activity', kicker: 'Workstation telemetry', blurb: 'Input telemetry, strain signals and app usage per machine.' },
  { id: 'presence', label: 'Presence', kicker: 'Floor state', blurb: 'Camera sightings matched against workstation input.' },
  { id: 'focus', label: 'Focus', kicker: 'Deep work', blurb: 'Daily deep-work scoring, streaks and break compliance.' },
  { id: 'security', label: 'Security', kicker: 'Anomaly feed', blurb: 'USB, after-hours and write-burst anomaly feed.' },
  { id: 'policy', label: 'Policy', kicker: 'Rules and privacy', blurb: 'Working-time rules, violation checks and privacy controls.' },
  { id: 'team', label: 'Team', kicker: 'Aggregates', blurb: 'Focus, strain, break compliance and presence per team.' },
  { id: 'analytics', label: 'Trends', kicker: 'Week over week', blurb: 'Weekly focus and strain movement.' },
];

const PAGE_TITLES: Record<Tab, string> = {
  events: 'Vision Events',
  activity: 'Workstation Activity',
  presence: 'Presence',
  focus: 'Focus Score',
  security: 'Security Alerts',
  policy: 'Policy and Privacy',
  team: 'Team view',
  analytics: 'Trends',
};

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function App() {
  const [tab, setTab] = useState<Tab>('events');
  const [events, setEvents] = useState<DetectionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cameraFilter, setCameraFilter] = useState('');
  const [officeId, setOfficeId] = useState(''); // "" = all offices
  const clock = useClock();

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

  const active = TABS.find((t) => t.id === tab)!;

  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="masthead">
        <div className="masthead-inner">
          <div className="brand" aria-label="WAMSS console home">
            <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
              <rect x="2" y="2" width="28" height="28" rx="7" fill="none" stroke="currentColor" strokeWidth="2.5" />
              <circle cx="16" cy="16" r="5.5" fill="currentColor" />
              <circle cx="16" cy="16" r="9.5" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.45" />
            </svg>
            <span>
              <span className="brand-name">WAMSS</span>
              <span className="brand-tag" style={{ display: 'block' }}>Operations console</span>
            </span>
          </div>
          <div className="masthead-right">
            <div className="office-dark">
              <OfficeSelector value={officeId} onChange={setOfficeId} />
            </div>
            <MastStatus />
            <span className="mast-clock" aria-label="Local time">{clock}</span>
          </div>
        </div>
      </header>

      <nav className="tabnav" aria-label="Dashboard sections">
        <div className="tabnav-inner">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className="tabnav-item"
              aria-current={tab === t.id ? 'page' : undefined}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </nav>

      <div className="mobile-office">
        <OfficeSelector value={officeId} onChange={setOfficeId} />
      </div>

      <main className="content" id="main">
        <header className="page-head rise" key={`head-${tab}`}>
          <div>
            <p className="page-kicker">{active.kicker}</p>
            <h1 className="page-title">{PAGE_TITLES[tab]}</h1>
            <p className="page-sub">{active.blurb}</p>
          </div>
        </header>

        <div key={`body-${tab}`}>
          {tab === 'events' && (
            <>
              <section className="panel rise" aria-label="Vision events">
                <div className="section-head">
                  <h2 className="section-title">Event Ledger</h2>
                  <span className="section-meta">
                    {events.length} events &middot; refresh {POLL_INTERVAL_MS / 1000}s
                  </span>
                  <div className="form-row" style={{ marginLeft: 'auto' }}>
                    <CameraFilter cameras={cameras} selected={cameraFilter} onChange={setCameraFilter} />
                    <ExportButton
                      endpoint="/exports/events.csv"
                      label="Export CSV"
                      filters={{ camera_id: cameraFilter || undefined, limit: 1000 }}
                      filename="events.csv"
                    />
                  </div>
                </div>
                {error && (
                  <div className="notice notice-error" role="alert">
                    <p>Could not load events: {error}</p>
                    <button type="button" className="btn" onClick={() => { setLoading(true); void loadEvents(); }}>
                      Retry
                    </button>
                  </div>
                )}
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
        </div>

        <footer className="footer-note">
          Events refresh every {POLL_INTERVAL_MS / 1000}s &middot; Rows in amber or red need review
        </footer>
      </main>
    </>
  );
}
