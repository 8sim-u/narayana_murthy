// Room Map tab: live 2D floor plan of tracked persons, geofence zones drawn
// from the same endpoint the Geofence tab uses, a heatmap overlay and an
// occupancy timeline. Every coordinate comes normalized [0, 1] from the
// backend; nothing here is simulated.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchEvents, getGeofences, getHeatmap, getOccupancy, getPositions } from '../api';
import type { GeofenceZone, HeatPoint, OccupancyBucket, PersonPosition } from '../types';
import { Chip, EmptyState, ErrorNotice, SectionHead, SkeletonCards, Stat } from './ui';

const POLL_MS = 2000;
const OCCUPANCY_REFRESH_MS = 60000;
const ZONES_REFRESH_MS = 30000;
const EVENT_CAMERAS_REFRESH_MS = 10000;
const EVENT_CAMERA_LIMIT = 100;
const MAP_W = 1000;
const MAP_H = 620;

// Position coordinates arrive normalized to [0, 1]; clamp defensively.
function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.min(1, Math.max(0, v));
}

function isKnownName(name: string | null | undefined): boolean {
  return !!name && name.trim() !== '' && name.trim().toLowerCase() !== 'unknown';
}

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// Same normalization the Geofence manager uses, so zones drawn on the
// Geofence tab land pixel-identical here.
function pointString(point: [number, number]): string {
  return `${clamp01(point[0]) * MAP_W},${clamp01(point[1]) * MAP_H}`;
}

/* ------------------------------ occupancy chart ---------------------------- */

// Hand-rolled SVG bar chart. Sums per-bucket counts across cameras so the
// timeline reads as a single floor-wide series.
function OccupancyChart({ buckets }: { buckets: OccupancyBucket[] }) {
  const series = useMemo(() => {
    const byTime = new Map<string, number>();
    for (const b of buckets) {
      byTime.set(b.t, (byTime.get(b.t) ?? 0) + b.count);
    }
    return [...byTime.entries()]
      .map(([t, count]) => ({ t, count }))
      .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
  }, [buckets]);

  const W = 1000;
  const H = 220;
  const PAD_L = 44;
  const PAD_B = 34;
  const PAD_T = 16;
  const max = Math.max(1, ...series.map((s) => s.count));
  const n = Math.max(1, series.length);
  const slot = (W - PAD_L - 12) / n;
  const barW = Math.max(2, slot * 0.62);
  const innerH = H - PAD_T - PAD_B;
  const peakIdx = series.reduce((best, s, i) => (s.count > series[best].count ? i : best), 0);

  const hourTicks = useMemo(() => {
    // Label every 6th bucket when the bucket is 15m (hourly ticks).
    const ticks: { x: number; label: string }[] = [];
    series.forEach((s, i) => {
      if (i % 6 !== 0) return;
      const d = new Date(s.t);
      if (Number.isNaN(d.getTime())) return;
      const x = PAD_L + slot * i + slot / 2;
      ticks.push({
        x,
        label: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      });
    });
    return ticks;
  }, [series, slot]);

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Occupancy timeline: person count per 15 minute bucket"
        style={{ width: '100%', height: 'auto', display: 'block' }}
      >
        {/* baseline */}
        <line
          x1={PAD_L}
          y1={H - PAD_B}
          x2={W - 12}
          y2={H - PAD_B}
          stroke="var(--rule)"
          strokeWidth="1"
        />
        {series.map((s, i) => {
          const h = Math.max(2, (s.count / max) * innerH);
          const x = PAD_L + slot * i + (slot - barW) / 2;
          const y = H - PAD_B - h;
          const peak = i === peakIdx && s.count > 0;
          return (
            <g key={s.t}>
              <title>{`${s.count} person(s) at ${s.t}`}</title>
              <rect
                x={x}
                y={y}
                width={barW}
                height={h}
                rx={2}
                fill={peak ? 'var(--ice)' : 'var(--iris)'}
                opacity={peak ? 0.95 : 0.55}
              />
            </g>
          );
        })}
        {hourTicks.map((tick) => (
          <text
            key={tick.label}
            x={tick.x}
            y={H - 10}
            textAnchor="middle"
            fontSize="12"
            fill="var(--text-2)"
            fontFamily="var(--font-mono)"
          >
            {tick.label}
          </text>
        ))}
        <text
          x={8}
          y={PAD_T + 4}
          fontSize="12"
          fill="var(--text-2)"
          fontFamily="var(--font-mono)"
        >
          {max}
        </text>
      </svg>
      <p style={{ color: 'var(--text-2)', fontSize: 12.5, margin: '6px 0 0' }}>
        Peak of {max} person{max === 1 ? '' : 's'} in one bucket over the window. Bars are floor-wide totals.
      </p>
    </div>
  );
}

/* ---------------------------------- tab ------------------------------------ */

export default function RoomMapTab() {
  const [positions, setPositions] = useState<PersonPosition[]>([]);
  const [zones, setZones] = useState<GeofenceZone[]>([]);
  const [eventCameras, setEventCameras] = useState<string[]>([]);
  const [heat, setHeat] = useState<HeatPoint[]>([]);
  const [occupancy, setOccupancy] = useState<OccupancyBucket[]>([]);
  const [heatmapOn, setHeatmapOn] = useState(false);
  const [heatCamera, setHeatCamera] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reducedMotion] = useState(prefersReducedMotion);

  const loadPositions = useCallback(async () => {
    try {
      const data = await getPositions();
      setPositions(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load positions');
    } finally {
      setLoading(false);
    }
  }, []);

  // Camera identities come from the real event ledger (the same source the
  // Vision Events tab uses). A camera that has reported events but currently
  // has no live positions is shown as quiet, never dropped from the map.
  const loadEventCameras = useCallback(async () => {
    try {
      const events = await fetchEvents(undefined, EVENT_CAMERA_LIMIT);
      const seen = new Set<string>();
      for (const e of events) seen.add(e.camera_id);
      setEventCameras([...seen].sort());
    } catch {
      // Camera list is secondary; the map keeps the last known set.
    }
  }, []);

  const loadZones = useCallback(async () => {
    try {
      setZones(await getGeofences());
    } catch {
      // Zones are config; the map still shows positions without them.
    }
  }, []);

  const loadOccupancy = useCallback(async () => {
    try {
      setOccupancy(await getOccupancy(24, '15m'));
    } catch {
      // Occupancy is secondary; the map keeps working without it.
    }
  }, []);

  const loadHeat = useCallback(async () => {
    if (!heatmapOn) return;
    try {
      setHeat(await getHeatmap(heatCamera || undefined, 24));
    } catch {
      setHeat([]);
    }
  }, [heatmapOn, heatCamera]);

  useEffect(() => {
    void loadPositions();
    void loadOccupancy();
    void loadZones();
    void loadEventCameras();
    const pos = window.setInterval(() => { void loadPositions(); }, POLL_MS);
    const occ = window.setInterval(() => { void loadOccupancy(); }, OCCUPANCY_REFRESH_MS);
    const zon = window.setInterval(() => { void loadZones(); }, ZONES_REFRESH_MS);
    const cams = window.setInterval(() => { void loadEventCameras(); }, EVENT_CAMERAS_REFRESH_MS);
    return () => {
      window.clearInterval(pos);
      window.clearInterval(occ);
      window.clearInterval(zon);
      window.clearInterval(cams);
    };
  }, [loadPositions, loadOccupancy, loadZones, loadEventCameras]);

  useEffect(() => {
    void loadHeat();
  }, [loadHeat]);

  const cameras = useMemo(() => {
    const seen = new Set<string>();
    for (const p of positions) seen.add(p.camera_id);
    for (const c of eventCameras) seen.add(c);
    return [...seen].sort();
  }, [positions, eventCameras]);

  const positionCameras = useMemo(() => {
    const seen = new Set<string>();
    for (const p of positions) seen.add(p.camera_id);
    return seen;
  }, [positions]);

  const knownCount = positions.filter((p) => isKnownName(p.person_name)).length;
  const maxWeight = Math.max(1, ...heat.map((h) => h.weight));

  const markerTransition = reducedMotion ? 'none' : 'cx 0.6s ease, cy 0.6s ease';

  return (
    <div className="rise">
      {error && (
        <ErrorNotice
          message={`Could not load live positions: ${error}`}
          onRetry={() => { setLoading(true); void loadPositions(); }}
        />
      )}

      <section className="panel" aria-label="Live room map">
        <SectionHead
          title="Live Room Map"
          meta={`refresh ${POLL_MS / 1000}s`}
          actions={
            <div className="form-row" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <label className="field" style={{ margin: 0 }}>
                <span className="field-label">Heat camera</span>
                <select
                  className="input"
                  value={heatCamera}
                  onChange={(e) => setHeatCamera(e.target.value)}
                  disabled={!heatmapOn}
                  aria-label="Camera for heatmap overlay"
                >
                  <option value="">All cameras</option>
                  {cameras.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className={heatmapOn ? 'btn btn-primary' : 'btn'}
                aria-pressed={heatmapOn}
                onClick={() => setHeatmapOn((v) => !v)}
              >
                Heatmap {heatmapOn ? 'on' : 'off'}
              </button>
            </div>
          }
        />

        <div className="grid-4" style={{ marginBottom: 16 }}>
          <Stat label="People tracked" value={positions.length} mono tone="var(--ice)" />
          <Stat label="Identified" value={knownCount} mono />
          <Stat label="Unknown" value={positions.length - knownCount} mono />
          <Stat label="Cameras seen" value={cameras.length} mono />
        </div>

        {/* Camera line, ordered and complete: a camera with no live
            positions is stated as quiet rather than silently missing. */}
        {cameras.length > 0 && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
            {cameras.map((c) => (
              <Chip key={c} tone={positionCameras.has(c) ? 'signal' : 'idle'}>
                {positionCameras.has(c) ? `${c} live` : `${c} no live positions`}
              </Chip>
            ))}
          </div>
        )}

        {loading && positions.length === 0 ? (
          <SkeletonCards count={1} />
        ) : positions.length === 0 ? (
          <EmptyState
            title="No tracked persons"
            body="The vision pipeline has not reported any tracked persons yet. Detections will appear here as they arrive."
          />
        ) : (
          <figure style={{ margin: 0 }}>
            <svg
              viewBox={`0 0 ${MAP_W} ${MAP_H}`}
              role="img"
              aria-label="Room map with live person positions and geofence zones"
              style={{
                width: '100%',
                height: 'auto',
                display: 'block',
                background: 'var(--paper)',
                border: '1px solid var(--rule)',
                borderRadius: 8,
              }}
            >
              <defs>
                <radialGradient id="heatblob">
                  <stop offset="0%" stopColor="var(--ice)" stopOpacity="0.5" />
                  <stop offset="55%" stopColor="var(--iris)" stopOpacity="0.22" />
                  <stop offset="100%" stopColor="var(--iris)" stopOpacity="0" />
                </radialGradient>
              </defs>

              {/* faint grid */}
              {Array.from({ length: 9 }).map((_, i) => (
                <line
                  key={`v${i}`}
                  x1={(MAP_W / 10) * (i + 1)}
                  y1={0}
                  x2={(MAP_W / 10) * (i + 1)}
                  y2={MAP_H}
                  stroke="var(--rule)"
                  strokeWidth="1"
                  opacity="0.5"
                />
              ))}
              {Array.from({ length: 5 }).map((_, i) => (
                <line
                  key={`h${i}`}
                  x1={0}
                  y1={(MAP_H / 6) * (i + 1)}
                  x2={MAP_W}
                  y2={(MAP_H / 6) * (i + 1)}
                  stroke="var(--rule)"
                  strokeWidth="1"
                  opacity="0.5"
                />
              ))}
              <text x={16} y={30} fontSize="13" fill="var(--text-2)" fontFamily="var(--font-mono)">
                CAMERA PLAN VIEW
              </text>

              {/* geofence zones, exactly the polygons the Geofence tab draws */}
              {zones.map((zone, i) => (
                <g key={zone.id}>
                  <polygon
                    points={zone.polygon.map(pointString).join(' ')}
                    fill={i % 2 === 0 ? 'var(--ice)' : 'var(--iris)'}
                    fillOpacity="0.16"
                    stroke={i % 2 === 0 ? 'var(--ice)' : 'var(--iris)'}
                    strokeWidth="3"
                    strokeLinejoin="round"
                  />
                  {zone.polygon[0] && (
                    <text
                      x={clamp01(zone.polygon[0][0]) * MAP_W + 8}
                      y={clamp01(zone.polygon[0][1]) * MAP_H - 8}
                      fontSize="14"
                      fontWeight="600"
                      fill="var(--ink)"
                      fontFamily="var(--font-display)"
                    >
                      {zone.name}
                    </text>
                  )}
                </g>
              ))}

              {/* heatmap density layer */}
              {heatmapOn &&
                heat.map((h, i) => {
                  const cx = clamp01(h.x) * MAP_W;
                  const cy = clamp01(h.y) * MAP_H;
                  const r = 60 + (h.weight / maxWeight) * 90;
                  return (
                    <circle
                      key={i}
                      cx={cx}
                      cy={cy}
                      r={r}
                      fill="url(#heatblob)"
                      opacity={0.35 + (h.weight / maxWeight) * 0.5}
                    />
                  );
                })}

              {/* person markers: ice for identified (with confidence), quiet ink for unknown */}
              {positions.map((p) => {
                const cx = clamp01(p.x) * MAP_W;
                const cy = clamp01(p.y) * MAP_H;
                const known = isKnownName(p.person_name);
                const conf =
                  typeof p.person_name_confidence === 'number' &&
                  Number.isFinite(p.person_name_confidence)
                    ? p.person_name_confidence.toFixed(2)
                    : null;
                const label = known ? (p.person_name as string) : 'Unknown';
                const fill = known ? 'var(--ice)' : 'var(--ink)';
                const opacity = known ? 1 : 0.55;
                return (
                  <g key={p.track_id} opacity={opacity}>
                    <title>{`${label}${conf ? ` ${conf}` : ''} (${p.track_id}) on ${p.camera_id}`}</title>
                    {known && (
                      <circle cx={cx} cy={cy} r={15} fill="none" stroke="var(--iris)" strokeWidth="1.5" opacity="0.7" />
                    )}
                    <circle
                      cx={cx}
                      cy={cy}
                      r={8}
                      fill={fill}
                      stroke="var(--paper)"
                      strokeWidth="2"
                      style={{ transition: markerTransition }}
                    />
                    <text
                      x={cx}
                      y={cy + 30}
                      textAnchor="middle"
                      fontSize="13"
                      fill={known ? 'var(--ink)' : 'var(--text-2)'}
                      fontWeight={known ? 600 : 400}
                      fontFamily={known ? 'var(--font-display)' : 'var(--font-mono)'}
                    >
                      {label}
                      {known && conf ? (
                        <tspan fontFamily="var(--font-mono)" fontWeight="400" fill="var(--text-2)">
                          {' '}{conf}
                        </tspan>
                      ) : null}
                    </text>
                  </g>
                );
              })}
            </svg>
            <figcaption
              style={{
                display: 'flex',
                gap: 18,
                flexWrap: 'wrap',
                marginTop: 10,
                color: 'var(--text-2)',
                fontSize: 12.5,
              }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--ice)', display: 'inline-block' }} />
                Identified person, confidence next to the name
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--ink)', opacity: 0.55, display: 'inline-block' }} />
                Unknown track
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 14, height: 14, borderRadius: '50%', background: 'var(--iris)', opacity: 0.35, display: 'inline-block' }} />
                Geofence zone
              </span>
              {heatmapOn && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ width: 14, height: 14, borderRadius: '50%', background: 'radial-gradient(circle, var(--ice) 0%, var(--iris) 60%, transparent 100%)', display: 'inline-block' }} />
                  Dwell density
                </span>
              )}
              <span style={{ marginLeft: 'auto' }}>
                Marker positions are a rough single-camera estimate from the detection box centre, not an exact floor position. Zones are normalized room coordinates.
              </span>
            </figcaption>
          </figure>
        )}
      </section>

      <section className="panel" aria-label="Occupancy timeline" style={{ marginTop: 20 }}>
        <SectionHead title="Occupancy Timeline" meta="last 24h, 15m buckets" />
        {occupancy.length === 0 ? (
          <EmptyState
            title="No occupancy data"
            body="Occupancy buckets will accumulate here once the vision pipeline reports tracked persons over time."
          />
        ) : (
          <OccupancyChart buckets={occupancy} />
        )}
      </section>
    </div>
  );
}
