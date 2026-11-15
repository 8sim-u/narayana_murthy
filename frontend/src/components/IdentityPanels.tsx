// Identity tab panels: unknown sightings awaiting enrollment, and the
// registry of known people. The identity endpoints (/identities, /unknowns,
// POST /faces/enroll, DELETE /faces/{name}) land separately from this UI, so
// a 404 resolves to an empty list and the panels render a graceful empty
// state instead of an error. The registry carries no confidence score, so
// none is shown here; wherever the vision pipeline reports a confidence
// (events, room map), it is displayed next to the name.
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { deleteIdentity, enrollFace, enrollFacePhoto, fetchEvents, getIdentities, getUnknowns, snapshotUrl } from '../api';
import type { Identity, UnknownSighting } from '../types';
import { Chip, EmptyState, ErrorNotice, Field, SectionHead, SkeletonRows, formatClock } from './ui';

const POLL_MS = 10000;

function sightingKey(u: UnknownSighting, index: number): string {
  if (u.id !== undefined && u.id !== null) return `id-${u.id}`;
  if (u.snapshot_path) return `snap-${u.snapshot_path}`;
  if (u.track_id) return `track-${u.track_id}`;
  return `row-${index}`;
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

  // One action per row: type the name and press Enter (or click Enroll).
  // The backend verifies active consent for the originating camera before
  // the face is stored; the UI never bypasses that check.
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
          body="People the cameras cannot name will queue here with their snapshot, track ID and camera. Enrolling a face requires consent on the camera that saw it."
        />
      ) : (
        <div>
          {unknowns.map((u, i) => {
            const key = sightingKey(u, i);
            const when = u.timestamp ?? u.last_seen ?? null;
            return (
              <form
                key={key}
                onSubmit={(e) => { e.preventDefault(); void enroll(u, key); }}
                style={{
                  display: 'flex',
                  gap: 12,
                  alignItems: 'flex-start',
                  padding: '12px 0',
                  borderTop: '1px solid var(--rule)',
                }}
              >
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
                    {u.track_id && (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--text-2)' }}>
                        {u.track_id}
                      </span>
                    )}
                  </div>
                  <p style={{ margin: '6px 0 10px', color: 'var(--text-2)', fontSize: 13 }}>
                    {u.camera_id ?? 'Camera unknown'} · {formatClock(when)}
                  </p>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input
                      className="input"
                      type="text"
                      placeholder="Type a name"
                      value={names[key] ?? ''}
                      onChange={(e) => setNames((prev) => ({ ...prev, [key]: e.target.value }))}
                      aria-label={`Name to enroll for sighting ${u.track_id ?? key}`}
                      disabled={enrollingKey === key}
                      style={{ flex: '1 1 140px', minWidth: 140 }}
                    />
                    <button
                      type="submit"
                      className="btn btn-primary"
                      disabled={enrollingKey === key}
                    >
                      {enrollingKey === key ? 'Enrolling...' : 'Enroll'}
                    </button>
                  </div>
                </div>
              </form>
            );
          })}
        </div>
      )}
    </section>
  );
}

/* ------------------------- enroll by photo ------------------------------- */

function EnrollPhotoPanel() {
  const [name, setName] = useState('');
  const [cameraId, setCameraId] = useState('');
  const [photo, setPhoto] = useState<File | null>(null);
  const [enrolling, setEnrolling] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [enrolledNote, setEnrolledNote] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  // Camera choices come from the live event ledger, the same source the
  // header camera filter uses. No hardcoded camera ids.
  const [cameras, setCameras] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await fetchEvents(undefined, 100);
        if (!alive) return;
        const seen = new Set<string>();
        for (const e of data) seen.add(e.camera_id);
        setCameras([...seen].sort());
      } catch {
        // Camera choices are a convenience; the backend consent check stays
        // the authority, so a failed lookup just leaves the list stale.
      }
    };
    void load();
    const id = window.setInterval(() => { void load(); }, 15000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  useEffect(() => {
    if (!cameraId && cameras.length > 0) setCameraId(cameras[0]);
  }, [cameras, cameraId]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError('Type a name before enrolling.');
      return;
    }
    if (!photo) {
      setFormError('Choose a clear front-facing photo first.');
      return;
    }
    if (!cameraId) {
      setFormError('Choose the camera this person will be watched by.');
      return;
    }
    setFormError(null);
    setEnrolling(true);
    try {
      await enrollFacePhoto(trimmed, cameraId, photo);
      setEnrolledNote(`Enrolled ${trimmed} for ${cameraId}. They are named on this camera from now on.`);
      setName('');
      setPhoto(null);
      if (fileInput.current) fileInput.current.value = '';
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Enrollment failed.');
    } finally {
      setEnrolling(false);
    }
  };

  return (
    <section className="panel" aria-label="Enroll by photo">
      <SectionHead title="Enroll by Photo" meta="one clear front-facing photo" />
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
      <form onSubmit={(e) => { void submit(e); }} style={{ display: 'grid', gap: 12, maxWidth: 460 }}>
        <Field label="Name">
          <input
            className="input"
            type="text"
            placeholder="Who is this person?"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Name to enroll"
            disabled={enrolling}
          />
        </Field>
        <Field label="Camera">
          <select
            className="select"
            value={cameraId}
            onChange={(e) => setCameraId(e.target.value)}
            aria-label="Camera to enroll the person on"
            disabled={enrolling || cameras.length === 0}
          >
            {cameras.length === 0 ? (
              <option value="">No cameras seen yet</option>
            ) : (
              cameras.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))
            )}
          </select>
        </Field>
        <Field label="Photo">
          <input
            ref={fileInput}
            className="input"
            type="file"
            accept="image/*"
            onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
            aria-label="Front-facing photo to enroll"
            disabled={enrolling}
          />
        </Field>
        <div>
          <button type="submit" className="btn btn-primary" disabled={enrolling}>
            {enrolling ? 'Enrolling...' : 'Enroll'}
          </button>
        </div>
        <p style={{ margin: 0, color: 'var(--text-3)', fontSize: 12.5 }}>
          The backend stores the face only when the person has active camera consent, so enrollment fails without it.
        </p>
      </form>
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
          body="Enroll someone from the unknowns queue and they will be listed here with their last seen time, sighting count and cameras."
        />
      ) : (
        <div className="table-wrap">
          <table className="ledger">
            <thead>
              <tr>
                <th>Person</th>
                <th>Last seen</th>
                <th>Sightings</th>
                <th>Cameras</th>
                <th>Enrolled</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {identities.map((p) => (
                <tr key={p.name}>
                  <td>
                    <Chip tone="signal">{p.name}</Chip>
                  </td>
                  <td className="num">{formatClock(p.last_seen ?? null)}</td>
                  <td className="num">
                    {typeof p.sightings === 'number' ? p.sightings : '-'}
                  </td>
                  <td className="id">
                    {p.cameras && p.cameras.length > 0 ? p.cameras.join(', ') : '-'}
                  </td>
                  <td className="num">{p.enrolled_at ? formatClock(p.enrolled_at) : '-'}</td>
                  <td>
                    <button
                      type="button"
                      className="btn"
                      disabled={removingName === p.name}
                      onClick={() => { void remove(p.name); }}
                    >
                      {removingName === p.name ? 'Removing...' : 'Remove'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Identity tab: the unknowns queue, the enroll-by-photo form, and the
 * known people list. */
export default function IdentityPanels() {
  return (
    <div className="rise">
      <div className="grid-2">
        <UnknownPeoplePanel />
        <div style={{ display: 'grid', gap: 16 }}>
          <EnrollPhotoPanel />
          <KnownPeopleList />
        </div>
      </div>
    </div>
  );
}
