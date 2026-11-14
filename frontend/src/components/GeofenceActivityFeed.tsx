import type { GeofenceEventRecord, TransitionType } from '../types';
import { Chip, SectionHead, formatClock } from './ui';

interface GeofenceActivityFeedProps {
  events: GeofenceEventRecord[];
}

function transitionTone(type: TransitionType | string): 'ok' | 'warn' | 'bad' | 'idle' {
  switch (type) {
    case 'ZONE_ENTER':
      return 'warn';
    case 'ZONE_EXIT':
      return 'idle';
    case 'DWELL_EXCEEDED':
      return 'bad';
    default:
      return 'ok';
  }
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds <= 0) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

export default function GeofenceActivityFeed({ events }: GeofenceActivityFeedProps) {
  return (
    <section className="panel" aria-label="Geofence transition activity">
      <SectionHead
        title="Geofence Activity Stream"
        meta="Real-time zone transitions and dwell alerts"
      />

      {events.length === 0 ? (
        <p style={{ color: 'var(--text-2)', fontSize: 13.5, margin: '10px 0 0' }}>
          No geofence events recorded yet. Zone transitions and dwell alerts will appear here live.
        </p>
      ) : (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Transition</th>
                <th>Zone</th>
                <th>Person</th>
                <th>Dwell Time</th>
                <th>Camera</th>
                <th>Alert Status</th>
              </tr>
            </thead>
            <tbody>
              {events.slice(0, 50).map((ev) => {
                const name = ev.identity_name || ev.person_label;
                const isAnonymous = !ev.identity_name;

                return (
                  <tr key={ev.id}>
                    <td style={{ whiteSpace: 'nowrap', fontSize: 12.5, fontFamily: 'var(--font-mono)' }}>
                      {formatClock(ev.timestamp)}
                    </td>
                    <td>
                      <Chip tone={transitionTone(ev.transition_type)}>
                        {ev.transition_type.replace('_', ' ')}
                      </Chip>
                    </td>
                    <td>
                      <strong style={{ fontSize: 13 }}>{ev.zone_name}</strong>
                      <span style={{ fontSize: 11.5, color: 'var(--text-3)', display: 'block' }}>
                        {ev.zone_type}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: 13, fontWeight: isAnonymous ? 400 : 600 }}>
                        {name}
                      </span>
                      {ev.track_id ? (
                        <span style={{ fontSize: 11.5, color: 'var(--text-3)', display: 'block', fontFamily: 'var(--font-mono)' }}>
                          track #{ev.track_id}
                        </span>
                      ) : null}
                    </td>
                    <td style={{ fontSize: 12.5, fontFamily: 'var(--font-mono)' }}>
                      {formatDuration(ev.dwell_seconds)}
                    </td>
                    <td style={{ fontSize: 12.5, fontFamily: 'var(--font-mono)' }}>
                      {ev.camera_id || 'All'}
                    </td>
                    <td>
                      {ev.alert_triggered ? (
                        <span className="chip chip-bad" style={{ fontSize: 11.5 }}>
                          Alert Dispatched
                        </span>
                      ) : (
                        <span className="chip chip-idle" style={{ fontSize: 11.5 }}>
                          Logged
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
