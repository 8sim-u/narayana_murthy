import { useCallback, useEffect, useMemo, useState, type MouseEvent } from 'react';
import { createGeofence, deleteGeofence, getGeofences } from '../api';
import type { GeofencePoint, GeofenceZone, PersonPosition } from '../types';
import { EmptyState, ErrorNotice, SectionHead } from './ui';

const MAP_W = 1000;
const MAP_H = 620;

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function pointString(point: GeofencePoint): string {
  return `${clamp01(point[0]) * MAP_W},${clamp01(point[1]) * MAP_H}`;
}

function isKnownName(name: string | null | undefined): boolean {
  return !!name && name.trim() !== '' && name.trim().toLowerCase() !== 'unknown';
}

export default function GeofenceManager({ positions }: { positions: PersonPosition[] }) {
  const [zones, setZones] = useState<GeofenceZone[]>([]);
  const [selectedCamera, setSelectedCamera] = useState('');
  const [cameraDraft, setCameraDraft] = useState('');
  const [zoneName, setZoneName] = useState('');
  const [draftPoints, setDraftPoints] = useState<GeofencePoint[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [loadingZones, setLoadingZones] = useState(true);
  const [saving, setSaving] = useState(false);
  const [zoneError, setZoneError] = useState<string | null>(null);

  const cameraOptions = useMemo(() => {
    const cameras = new Set<string>();
    for (const position of positions) cameras.add(position.camera_id);
    for (const zone of zones) cameras.add(zone.camera_id);
    if (selectedCamera) cameras.add(selectedCamera);
    return [...cameras].sort();
  }, [positions, zones, selectedCamera]);

  const selectedZones = useMemo(
    () => zones.filter((zone) => zone.camera_id === selectedCamera),
    [zones, selectedCamera],
  );
  const selectedPositions = useMemo(
    () => positions.filter((position) => position.camera_id === selectedCamera),
    [positions, selectedCamera],
  );

  const loadZones = useCallback(async () => {
    try {
      setZones(await getGeofences());
      setZoneError(null);
    } catch (error) {
      setZoneError(error instanceof Error ? error.message : 'Could not load geofence zones');
    } finally {
      setLoadingZones(false);
    }
  }, []);

  useEffect(() => {
    void loadZones();
    const refresh = window.setInterval(() => { void loadZones(); }, 30000);
    return () => window.clearInterval(refresh);
  }, [loadZones]);

  useEffect(() => {
    if (!selectedCamera && cameraOptions.length > 0) setSelectedCamera(cameraOptions[0]);
  }, [cameraOptions, selectedCamera]);

  const handleMapClick = (event: MouseEvent<SVGSVGElement>) => {
    if (!drawing) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = clamp01((event.clientX - rect.left) / rect.width);
    const y = clamp01((event.clientY - rect.top) / rect.height);
    setDraftPoints((points) => [...points, [x, y]]);
  };

  const startDrawing = () => {
    if (!selectedCamera) {
      setZoneError('Choose or add a camera ID before drawing a zone.');
      return;
    }
    setDraftPoints([]);
    setZoneError(null);
    setDrawing(true);
  };

  const cancelDrawing = () => {
    setDraftPoints([]);
    setDrawing(false);
  };

  const saveZone = async () => {
    const name = zoneName.trim();
    if (!selectedCamera || !name || draftPoints.length < 3) return;
    setSaving(true);
    try {
      await createGeofence({ camera_id: selectedCamera, name, polygon: draftPoints });
      setZoneName('');
      cancelDrawing();
      await loadZones();
    } catch (error) {
      setZoneError(error instanceof Error ? error.message : 'Could not save this geofence');
    } finally {
      setSaving(false);
    }
  };

  const removeZone = async (zoneId: number) => {
    try {
      await deleteGeofence(zoneId);
      await loadZones();
    } catch (error) {
      setZoneError(error instanceof Error ? error.message : 'Could not delete this geofence');
    }
  };

  const useCameraDraft = () => {
    const camera = cameraDraft.trim();
    if (!camera) return;
    setSelectedCamera(camera);
    setCameraDraft('');
  };

  return (
    <section className="panel" aria-label="Geofence zones" style={{ marginTop: 20 }}>
      <SectionHead
        title="Geofence Zones"
        meta="per-camera polygons · entry and exit alerts"
      />

      {zoneError && <ErrorNotice message={zoneError} />}

      <div className="form-row" style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap', marginBottom: 14 }}>
        <label className="field" style={{ margin: 0, minWidth: 180 }}>
          <span className="field-label">Camera</span>
          <select
            className="input"
            value={selectedCamera}
            onChange={(event) => { setSelectedCamera(event.target.value); cancelDrawing(); }}
            aria-label="Camera for geofence zones"
          >
            <option value="">Choose camera</option>
            {cameraOptions.map((camera) => <option key={camera} value={camera}>{camera}</option>)}
          </select>
        </label>
        <label className="field" style={{ margin: 0, minWidth: 180 }}>
          <span className="field-label">Add camera ID</span>
          <input
            className="input"
            value={cameraDraft}
            maxLength={64}
            placeholder="e.g. CAM_01"
            onChange={(event) => setCameraDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') useCameraDraft(); }}
          />
        </label>
        <button type="button" className="btn" onClick={useCameraDraft} disabled={!cameraDraft.trim()}>
          Use camera
        </button>
        <button type="button" className={drawing ? 'btn' : 'btn btn-primary'} onClick={drawing ? cancelDrawing : startDrawing}>
          {drawing ? 'Cancel drawing' : 'Draw zone'}
        </button>
      </div>

      {drawing && (
        <div className="form-row" style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap', marginBottom: 14 }}>
          <label className="field" style={{ margin: 0, minWidth: 200 }}>
            <span className="field-label">Zone name</span>
            <input
              className="input"
              value={zoneName}
              maxLength={80}
              placeholder="e.g. Reception"
              onChange={(event) => setZoneName(event.target.value)}
            />
          </label>
          <span className="section-meta" aria-live="polite">Click at least 3 points on the map ({draftPoints.length} added).</span>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => { void saveZone(); }}
            disabled={saving || !zoneName.trim() || draftPoints.length < 3}
          >
            {saving ? 'Saving…' : 'Save polygon'}
          </button>
          <button type="button" className="btn" onClick={() => setDraftPoints([])} disabled={draftPoints.length === 0}>
            Clear points
          </button>
        </div>
      )}

      <figure style={{ margin: 0 }}>
        <svg
          viewBox={`0 0 ${MAP_W} ${MAP_H}`}
          role="img"
          aria-label={drawing ? 'Click to add geofence polygon points to the camera map' : 'Camera map with saved geofence zones and live positions'}
          onClick={handleMapClick}
          style={{
            width: '100%',
            height: 'auto',
            display: 'block',
            background: 'var(--paper)',
            border: '1px solid var(--rule)',
            borderRadius: 8,
            cursor: drawing ? 'crosshair' : 'default',
          }}
        >
          {Array.from({ length: 9 }).map((_, index) => (
            <line key={`v${index}`} x1={(MAP_W / 10) * (index + 1)} y1={0} x2={(MAP_W / 10) * (index + 1)} y2={MAP_H} stroke="var(--rule)" strokeWidth="1" opacity="0.5" />
          ))}
          {Array.from({ length: 5 }).map((_, index) => (
            <line key={`h${index}`} x1={0} y1={(MAP_H / 6) * (index + 1)} x2={MAP_W} y2={(MAP_H / 6) * (index + 1)} stroke="var(--rule)" strokeWidth="1" opacity="0.5" />
          ))}
          <text x={16} y={30} fontSize="13" fill="var(--text-2)" fontFamily="var(--font-mono)">
            {selectedCamera ? `CAMERA ${selectedCamera}` : 'SELECT A CAMERA'}
          </text>

          {selectedZones.map((zone, index) => (
            <g key={zone.id}>
              <polygon
                points={zone.polygon.map(pointString).join(' ')}
                fill={index % 2 === 0 ? 'var(--ice)' : 'var(--iris)'}
                fillOpacity="0.16"
                stroke={index % 2 === 0 ? 'var(--ice)' : 'var(--iris)'}
                strokeWidth="3"
                strokeLinejoin="round"
              />
              {zone.polygon[0] && (
                <text x={clamp01(zone.polygon[0][0]) * MAP_W + 8} y={clamp01(zone.polygon[0][1]) * MAP_H - 8} fontSize="14" fill="var(--text-1)" fontWeight="600">
                  {zone.name}
                </text>
              )}
            </g>
          ))}

          {drawing && draftPoints.length > 0 && (
            <g>
              {draftPoints.length >= 3 && (
                <polygon points={draftPoints.map(pointString).join(' ')} fill="var(--ice)" fillOpacity="0.13" stroke="var(--ice)" strokeWidth="3" strokeDasharray="8 6" />
              )}
              {draftPoints.map((point, index) => (
                <circle key={index} cx={point[0] * MAP_W} cy={point[1] * MAP_H} r="7" fill="var(--ice)" stroke="var(--paper)" strokeWidth="2" />
              ))}
              {draftPoints.length === 2 && (
                <line x1={draftPoints[0][0] * MAP_W} y1={draftPoints[0][1] * MAP_H} x2={draftPoints[1][0] * MAP_W} y2={draftPoints[1][1] * MAP_H} stroke="var(--ice)" strokeWidth="3" strokeDasharray="8 6" />
              )}
            </g>
          )}

          {selectedPositions.map((position) => {
            const x = clamp01(position.x) * MAP_W;
            const y = clamp01(position.y) * MAP_H;
            const known = isKnownName(position.person_name);
            const label = known ? position.person_name as string : 'Unknown';
            return (
              <g key={`${position.camera_id}:${position.track_id}`}>
                <title>{`${label} on ${position.camera_id}`}</title>
                <circle cx={x} cy={y} r="9" fill={known ? 'var(--ice)' : 'var(--ink)'} fillOpacity={known ? 1 : 0.55} stroke="var(--paper)" strokeWidth="2" />
                <text x={x} y={y + 25} textAnchor="middle" fontSize="13" fill="var(--text-1)">{label}</text>
              </g>
            );
          })}
        </svg>
        <figcaption style={{ color: 'var(--text-2)', fontSize: 12.5, marginTop: 8 }}>
          {drawing
            ? 'Add polygon vertices in order, then save. The last point connects back to the first.'
            : 'Zones and live positions use normalized room-map coordinates. Calibrated camera coordinates are scaled to the same map.'}
        </figcaption>
      </figure>

      <div className="grid-2" style={{ marginTop: 16 }}>
        <div>
          <SectionHead title="Saved zones" meta={selectedZones.length} />
          {loadingZones ? (
            <p className="section-meta">Loading zones…</p>
          ) : selectedZones.length === 0 ? (
            <EmptyState title="No zones for this camera" body="Draw and save a polygon to start receiving entry and exit alerts." />
          ) : (
            <div style={{ display: 'grid', gap: 8 }}>
              {selectedZones.map((zone) => (
                <div key={zone.id} style={{ display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--rule)', padding: '8px 0' }}>
                  <span style={{ flex: 1 }}>{zone.name}</span>
                  <span className="section-meta">{zone.polygon.length} points</span>
                  <button type="button" className="btn" onClick={() => { void removeZone(zone.id); }} aria-label={`Delete ${zone.name} geofence`}>
                    Delete
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <SectionHead title="Personal history" meta="employee-only" />
          <p className="section-meta">Geofence transitions are available only in an employee's token-authenticated personal view. This management page does not request or display person-level history.</p>
        </div>
      </div>

      <div className="notice notice-info" role="note" style={{ marginTop: 16 }}>
        <p>
          Aggregate-only privacy-mode events remain unchanged. Transition records store no coordinates or tracker IDs, and temporary tracker state stays in memory and expires after 15 minutes. Legacy rows without a stored employee owner are hidden from personal history.
        </p>
      </div>
    </section>
  );
}
