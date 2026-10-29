import type { DetectionEvent } from '../types';

export function isRestrictedAlert(event: DetectionEvent): boolean {
  return event.decision === 'REVIEW_REQUIRED' || event.alerts.includes('RESTRICTED_ZONE_DETECTED');
}

export function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function EventsTable({ events, loading }: { events: DetectionEvent[]; loading: boolean }) {
  return (
    <div>
      {loading && <p className="muted">Refreshing...</p>}
      {events.length === 0 && !loading ? (
        <p className="muted">No events yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Time</th>
                <th>Camera</th>
                <th>Track</th>
                <th>Objects</th>
                <th>Conf.</th>
                <th>Zone</th>
                <th>Decision</th>
                <th>Alerts</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className={isRestrictedAlert(e) ? 'alert-row' : ''}>
                  <td>{e.id}</td>
                  <td>{formatTimestamp(e.timestamp)}</td>
                  <td>{e.camera_id}</td>
                  <td>{e.track_id}</td>
                  <td>{e.objects.join(', ')}</td>
                  <td>{e.confidence.toFixed(2)}</td>
                  <td>{e.zone}</td>
                  <td>
                    <span className={`badge ${e.decision === 'REVIEW_REQUIRED' ? 'badge-review' : 'badge-ok'}`}>
                      {e.decision}
                    </span>
                  </td>
                  <td>{e.alerts.length > 0 ? e.alerts.join(', ') : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
