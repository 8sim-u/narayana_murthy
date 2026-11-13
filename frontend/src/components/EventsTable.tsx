import { useRef } from 'react';
import type { DetectionEvent } from '../types';
import { snapshotUrl } from '../api';
import { Chip, EmptyState, Meter, SkeletonRows } from './ui';

export function isRestrictedAlert(event: DetectionEvent): boolean {
  return event.decision === 'REVIEW_REQUIRED' || event.alerts.includes('RESTRICTED_ZONE_DETECTED');
}

export function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function confidenceTone(conf: number): string {
  if (conf >= 0.8) return 'var(--ok)';
  if (conf >= 0.5) return 'var(--warn)';
  return 'var(--bad)';
}

export function isKnownPerson(name: string | null | undefined): boolean {
  return !!name && name.trim() !== '' && name.trim().toLowerCase() !== 'unknown';
}

export default function EventsTable({ events, loading }: { events: DetectionEvent[]; loading: boolean }) {
  // Only play the sheet-row entrance once per data batch, not on every poll.
  const batchRef = useRef<string>('');
  const key = events.length > 0 ? String(events[0].id) : 'empty';
  if (batchRef.current !== key) batchRef.current = key;

  if (loading && events.length === 0) {
    return <SkeletonRows rows={6} height={52} />;
  }

  if (events.length === 0) {
    return (
      <EmptyState
        title="No events yet"
        body="Vision events will stream in here as cameras report detections. Use the injector below to send a test event."
      />
    );
  }

  return (
    <div className="table-wrap">
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
          {events.map((e, i) => {
            const alert = isRestrictedAlert(e);
            const unknown = !isKnownPerson(e.person_name);
            return (
              <tr
                key={`${batchRef.current}-${e.id}`}
                className={`sheet-row${alert ? ' row-alert' : ''}`}
                style={
                  {
                    '--i': Math.min(i, 24),
                    ...(unknown && !alert ? { backgroundColor: 'var(--cloud)' } : {}),
                  } as React.CSSProperties
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
                    <Meter value={e.confidence} max={1} color={confidenceTone(e.confidence)} label={`Confidence ${e.confidence.toFixed(2)}`} />
                    <span className="num">{e.confidence.toFixed(2)}</span>
                  </span>
                </td>
                <td><Chip tone={e.zone === 'RESTRICTED' ? 'bad' : 'idle'}>{e.zone}</Chip></td>
                <td>
                  <Chip tone={e.decision === 'REVIEW_REQUIRED' ? 'warn' : 'ok'}>{e.decision}</Chip>
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
