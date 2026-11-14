// Identity review panels for the Room Map tab: unknown sightings awaiting
// enrollment, and the registry of known people. The identity endpoints
// (/identities, /unknowns, POST /faces/enroll, DELETE /faces/{name}) land
// separately from this UI, so a 404 resolves to an empty list and the
// panels render a graceful empty state instead of an error.
import { useCallback, useEffect, useState } from 'react';
import { deleteIdentity, enrollFace, getIdentities, getUnknowns, snapshotUrl } from '../api';
import type { Identity, UnknownSighting } from '../types';
import { Chip, EmptyState, ErrorNotice, SectionHead, SkeletonRows, formatClock } from './ui';

const POLL_MS = 10000;

function sightingKey(u: UnknownSighting, index: number): string {
  if (u.id !== undefined && u.id !== null) return `id-${u.id}`;
  if (u.snapshot_path) return `snap-${u.snapshot_path}`;
  if (u.track_id) return `track-${u.track_id}`;
  return `row-${index}`;
}

function rowStyle(): React.CSSProperties {
  return {
    display: 'flex',
    gap: 12,
    alignItems: 'flex-start',
    padding: '12px 0',
    borderTop: '1px solid var(--rule)',
  };
}

/* --------------------------- unknown people ------------------------------ */

export function UnknownPeoplePanel() {
  const [unknowns, setUnknowns] = useState<UnknownSighting[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [enrollingKey, setEnrollingKey] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [enrolledNote, setEnrolledNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getUnknowns();
      setUnknowns(Array.isArray(data) ? data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load unknown sightings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => { void load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const enroll = async (item: UnknownSighting, key: string) => {
    const name = (names[key] ?? '').trim();
    if (!name) {
      setFormError('Type a name before enrolling.');
      return;
    }
    if (!item.snapshot_path) {
      setFormError('This sighting has no snapshot, so it cannot be enrolled.');
      return;
    }
    if (!item.camera_id) {
      setFormError('This sighting has no camera ID, so consent cannot be checked.');
      return;
    }
    setFormError(null);
    setEnrollingKey(key);
    try {
      await enrollFace(name, item.snapshot_path, item.camera_id);
      setUnknowns((prev) => prev.filter((u, i) => sightingKey(u, i) !== key));
      setNames((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setEnrolledNote(`Enrolled ${name}.`);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Enrollment failed.');
    } finally {
      setEnrollingKey(null);
    }
  };

  return (
    <section className="panel" aria-label="Unknown people">
      <SectionHead
        title="Unknown People"
        meta={`${unknowns.length} to review · refresh ${POLL_MS / 1000}s`}
      />
      {error && (
        <ErrorNotice
          message={`Could not load unknown sightings: ${error}`}
          onRetry={() => { setLoading(true); void load(); }}
        />
      )}
      {enrolledNote && (
        <div className="notice notice-info" role="status">
          <p>{enrolledNote}</p>
        </div>
      )}
      {formError && (
        <div className="notice notice-error" role="alert">
          <p>{formError}</p>
        </div>
      )}
      {loading && unknowns.length === 0 ? (
        <SkeletonRows rows={3} height={64} />
      ) : unknowns.length === 0 ? (
        <EmptyState
          title="No unknown people"
          body="Unknown sightings will appear here for review once cameras report unrecognized faces. If the identity endpoints are not deployed yet, this panel stays empty."
        />
      ) : (
        <div>
          {unknowns.map((u, i) => {
            const key = sightingKey(u, i);
            const when = u.timestamp ?? u.last_seen ?? null;
            return (
              <div key={key} style={rowStyle()}>
                {u.snapshot_path ? (
                  <a
                    href={snapshotUrl(u.snapshot_path)}
                    target="_blank"
                    rel="noreferrer"
                    title="Open full snapshot"
                  >
                    <img
                      src={snapshotUrl(u.snapshot_path)}
                      alt={`Unknown person seen on ${u.camera_id ?? 'unknown camera'}`}
                      loading="lazy"
                      style={{
                        width: 96,
                        height: 60,
                        objectFit: 'cover',
                        borderRadius: 4,
                        border: '1px solid var(--rule)',
                        display: 'block',
                        flex: 'none',
                      }}
                    />
                  </a>
                ) : (
                  <span
                    style={{
                      width: 96,
                      height: 60,
                      borderRadius: 4,
                      border: '1px solid var(--rule)',
                      background: 'var(--cloud)',
                      color: 'var(--text-3)',
                      fontSize: 12,
                      display: 'grid',
                      placeItems: 'center',
                      flex: 'none',
                    }}
                  >
                    No snapshot
                  </span>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <Chip tone="idle">Unknown</Chip>
                    {u.track_id && <span className="id" style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--text-2)' }}>{u.track_id}</span>}
                  </div>
                  <p style={{ margin: '6px 0 10px', color: 'var(--text-2)', fontSize: 13 }}>
                    {u.camera_id ?? 'Camera unknown'} · {formatClock(when)}
                  </p>
                  <div className="form-row" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <label className="field" style={{ margin: 0, flex: '1 1 140px' }}>
                      <span className="field-label">Enroll as</span>
                      <input
                        className="input"
                        type="text"
                        placeholder="Type a name"
                        value={names[key] ?? ''}
                        onChange={(e) => setNames((prev) => ({ ...prev, [key]: e.target.value }))}
                        aria-label={`Name to enroll for sighting ${u.track_id ?? key}`}
                        disabled={enrollingKey === key}
                      />
                    </label>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={enrollingKey === key}
                      onClick={() => { void enroll(u, key); }}
                    >
                      {enrollingKey === key ? 'Enrolling...' : 'Enroll'}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/* ---------------------------- known people ------------------------------- */

export function KnownPeopleList() {
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removingName, setRemovingName] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [removedNote, setRemovedNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getIdentities();
      setIdentities(Array.isArray(data) ? data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load known people');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => { void load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const remove = async (name: string) => {
    if (!window.confirm(`Remove ${name} from known people?`)) return;
    setDeleteError(null);
    setRemovingName(name);
    try {
      await deleteIdentity(name);
      setIdentities((prev) => prev.filter((p) => p.name !== name));
      setRemovedNote(`Removed ${name}.`);
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Removal failed.');
    } finally {
      setRemovingName(null);
    }
  };

  return (
    <section className="panel" aria-label="Known people">
      <SectionHead
        title="Known People"
        meta={`${identities.length} enrolled · refresh ${POLL_MS / 1000}s`}
      />
      {error && (
        <ErrorNotice
          message={`Could not load known people: ${error}`}
          onRetry={() => { setLoading(true); void load(); }}
        />
      )}
      {removedNote && (
        <div className="notice notice-info" role="status">
          <p>{removedNote}</p>
        </div>
      )}
      {deleteError && (
        <div className="notice notice-error" role="alert">
          <p>{deleteError}</p>
        </div>
      )}
      {loading && identities.length === 0 ? (
        <SkeletonRows rows={3} height={52} />
      ) : identities.length === 0 ? (
        <EmptyState
          title="No known people yet"
          body="Enrolled people will be listed here with their last seen time and sighting count. If the identity endpoints are not deployed yet, this list stays empty."
        />
      ) : (
        <div>
          {identities.map((p) => (
            <div key={p.name} style={rowStyle()}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 15 }}>
                  {p.name}
                </div>
                <p style={{ margin: '4px 0 0', color: 'var(--text-2)', fontSize: 13 }}>
                  Last seen {formatClock(p.last_seen ?? null)}
                  {' · '}
                  {typeof p.sightings === 'number' ? `${p.sightings} sighting${p.sightings === 1 ? '' : 's'}` : 'sightings unknown'}
                  {' · '}
                  {p.cameras && p.cameras.length > 0 ? p.cameras.join(', ') : 'cameras unknown'}
                </p>
                {p.enrolled_at && (
                  <p style={{ margin: '2px 0 0', color: 'var(--text-3)', fontSize: 12.5 }}>
                    Enrolled {formatClock(p.enrolled_at)}
                  </p>
                )}
              </div>
              <button
                type="button"
                className="btn"
                disabled={removingName === p.name}
                onClick={() => { void remove(p.name); }}
              >
                {removingName === p.name ? 'Removing...' : 'Remove'}
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
