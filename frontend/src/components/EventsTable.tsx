import type { DetectionEvent } from '../types';
import { snapshotUrl } from '../api';
import { Chip, EmptyState, Meter, SkeletonRows } from './ui';

export function isRestrictedAlert(event: DetectionEvent): boolean {
  return event.decision === 'REVIEW_REQUIRED' || event.alerts.includes('RESTRICTED_ZONE_DETECTED');
}

// Short, sortable clock for the ledger: time only for today, date and time
// otherwise. Kept in Commit Mono via the .num cell class.
export function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  return d.toLocaleString([], { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function isKnownPerson(name: string | null | undefined): boolean {
  return !!name && name.trim() !== '' && name.trim().toLowerCase() !== 'unknown';
}

export default function EventsTable({ events, loading }: { events: DetectionEvent[]; loading: boolean }) {
  if (loading && events.length === 0) {
    return <SkeletonRows rows={6} height={52} />;
  }

  if (events.length === 0) {
    return (
      <EmptyState
        title="No events yet"
        body="No detections have arrived from any camera. Either no cameras are connected to the backend, or the vision pipeline has not produced detections yet. Real events will appear here the moment cameras report."
      />
    );
  }

  return (
    <div className="table-wrap ledger-scroll">
      <table className="ledger">
        <thead>
          <tr>
            <th>ID</th>
            <th>Time</th>
            <th>Camera</th>
            <th>Track</th>
            <th>Person</th>
            <th>Snapshot</th>
            <th>Objects</th>
            <th>Confidence</th>
            <th>Zone</th>
            <th>Decision</th>
            <th>Alerts</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const alert = isRestrictedAlert(e);
            const unknown = !isKnownPerson(e.person_name);
            return (
              // Keyed by the event id only: live refresh updates cells in
              // place and never remounts or reorders rows under the cursor.
              <tr
                key={e.id}
                className={alert ? 'row-alert' : undefined}
                style={
                  unknown && !alert ? { backgroundColor: 'var(--cloud)' } : undefined
                }
              >
                <td className="num">{e.id}</td>
                <td className="num">{formatTimestamp(e.timestamp)}</td>
                <td className="id">{e.camera_id}</td>
                <td className="id">{e.track_id}</td>
                <td>
                  {isKnownPerson(e.person_name) ? (
                    <Chip tone="signal">
                      {e.person_name}
                      {typeof e.person_name_confidence === 'number' &&
                        Number.isFinite(e.person_name_confidence) && (
                          <span className="num" style={{ marginLeft: 6 }}>
                            {e.person_name_confidence.toFixed(2)}
                          </span>
                        )}
                    </Chip>
                  ) : (
                    <Chip tone="idle">Unknown</Chip>
                  )}
                </td>
                <td>
                  {e.snapshot_path ? (
                    <a
                      href={snapshotUrl(e.snapshot_path)}
                      target="_blank"
                      rel="noreferrer"
                      title="Open full snapshot"
                    >
                      <img
                        src={snapshotUrl(e.snapshot_path)}
                        alt={`Snapshot for event ${e.id}`}
                        loading="lazy"
                        style={{
                          width: 64,
                          height: 40,
                          objectFit: 'cover',
                          borderRadius: 4,
                          border: '1px solid var(--rule)',
                          display: 'block',
                        }}
                      />
                    </a>
                  ) : (
                    <span style={{ color: 'var(--text-3)' }}>-</span>
                  )}
                </td>
                <td>{e.objects.join(', ')}</td>
                <td>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                    <Meter value={e.confidence} max={1} color="var(--ink)" label={`Confidence ${e.confidence.toFixed(2)}`} />
                    <span className="num">{e.confidence.toFixed(2)}</span>
                  </span>
                </td>
                <td>
                  <Chip tone={e.zone === 'RESTRICTED' ? 'bad' : 'idle'}>{e.zone}</Chip>
                </td>
                <td>
                  {/* Colour means state here: amber only for review, quiet
                      neutral for the ordinary no-rule case. */}
                  <Chip tone={e.decision === 'REVIEW_REQUIRED' ? 'warn' : 'idle'}>{e.decision}</Chip>
                </td>
                <td className="id">{e.alerts.length > 0 ? e.alerts.join(', ') : '-'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
