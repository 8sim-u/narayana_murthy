// Room Map tab: live 2D floor plan of tracked persons, real geofence zones,
// interactive polygon drawing, a heatmap overlay, and an occupancy timeline.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createGeofenceZone,
  deleteGeofenceZone,
  getGeofenceEvents,
  getGeofenceZones,
  getHeatmap,
  getOccupancy,
  getPositions,
  getZoneOccupancy,
  updateGeofenceZone,
} from '../api';
import type {
  GeofenceEventRecord,
  GeofenceZone,
  GeofenceZoneCreate,
  HeatPoint,
  OccupancyBucket,
  PersonPosition,
  ZoneOccupancy,
} from '../types';
import { KnownPeopleList, UnknownPeoplePanel } from './IdentityPanels';
import { ErrorNotice, SectionHead, SkeletonCards, Stat } from './ui';
import GeofenceConfigModal from './GeofenceConfigModal';
import GeofenceZoneList from './GeofenceZoneList';
import GeofenceActivityFeed from './GeofenceActivityFeed';
import {
  ArrowsCounterClockwise,
  Check,
  Polygon,
  Trash,
  X,
} from '@phosphor-icons/react';

const POLL_MS = 2000;
const ZONES_POLL_MS = 3000;
const EVENTS_POLL_MS = 5000;
const OCCUPANCY_REFRESH_MS = 60000;
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

function computeCentroid(polygon: [number, number][]): { cx: number; cy: number } {
  if (!polygon || polygon.length === 0) return { cx: 0, cy: 0 };
  const sumX = polygon.reduce((acc, p) => acc + p[0], 0);
  const sumY = polygon.reduce((acc, p) => acc + p[1], 0);
  return {
    cx: (sumX / polygon.length) * MAP_W,
    cy: (sumY / polygon.length) * MAP_H,
  };
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

interface RoomMapTabProps {
  officeId?: string;
}

export default function RoomMapTab({ officeId }: RoomMapTabProps) {
  const [positions, setPositions] = useState<PersonPosition[]>([]);
  const [heat, setHeat] = useState<HeatPoint[]>([]);
  const [occupancy, setOccupancy] = useState<OccupancyBucket[]>([]);
  const [zones, setZones] = useState<GeofenceZone[]>([]);
  const [zoneOccupancies, setZoneOccupancies] = useState<Record<number, ZoneOccupancy>>({});
  const [geofenceEvents, setGeofenceEvents] = useState<GeofenceEventRecord[]>([]);

  const [heatmapOn, setHeatmapOn] = useState(false);
  const [heatCamera, setHeatCamera] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reducedMotion] = useState(prefersReducedMotion);

  // Drawing state
  const [isDrawing, setIsDrawing] = useState(false);
  const [drawPoints, setDrawPoints] = useState<[number, number][]>([]);
  const [cursorPos, setCursorPos] = useState<[number, number] | null>(null);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [hoveredZoneId, setHoveredZoneId] = useState<number | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);

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

  const loadZones = useCallback(async () => {
    try {
      const data = await getGeofenceZones(officeId || undefined);
      setZones(data);
    } catch {
      // Zone endpoint fallback
    }
  }, [officeId]);

  const loadZoneOccupancy = useCallback(async () => {
    try {
      const occList = await getZoneOccupancy(officeId || undefined);
      const map: Record<number, ZoneOccupancy> = {};
      occList.forEach((o) => {
        map[o.zone_id] = o;
      });
      setZoneOccupancies(map);
    } catch {
      // Occupancy endpoint fallback
    }
  }, [officeId]);

  const loadGeofenceEvents = useCallback(async () => {
    try {
      const evts = await getGeofenceEvents({ office_id: officeId || undefined, limit: 40 });
      setGeofenceEvents(evts);
    } catch {
      // Events endpoint fallback
    }
  }, [officeId]);

  // Initial and periodic fetches
  useEffect(() => {
    void loadPositions();
    void loadOccupancy();
    void loadZones();
    void loadZoneOccupancy();
    void loadGeofenceEvents();

    const posInterval = window.setInterval(() => { void loadPositions(); }, POLL_MS);
    const occInterval = window.setInterval(() => { void loadOccupancy(); }, OCCUPANCY_REFRESH_MS);
    const zonesInterval = window.setInterval(() => {
      void loadZones();
      void loadZoneOccupancy();
    }, ZONES_POLL_MS);
    const eventsInterval = window.setInterval(() => { void loadGeofenceEvents(); }, EVENTS_POLL_MS);

    return () => {
      window.clearInterval(posInterval);
      window.clearInterval(occInterval);
      window.clearInterval(zonesInterval);
      window.clearInterval(eventsInterval);
    };
  }, [loadPositions, loadOccupancy, loadZones, loadZoneOccupancy, loadGeofenceEvents]);

  useEffect(() => {
    void loadHeat();
  }, [loadHeat]);

  const cameras = useMemo(() => {
    const seen = new Set<string>();
    for (const p of positions) seen.add(p.camera_id);
    for (const z of zones) {
      if (z.camera_id) seen.add(z.camera_id);
    }
    return [...seen].sort();
  }, [positions, zones]);

  const knownCount = positions.filter((p) => isKnownName(p.person_name)).length;
  const maxWeight = Math.max(1, ...heat.map((h) => h.weight));
  const markerTransition = reducedMotion ? 'none' : 'cx 0.6s ease, cy 0.6s ease';

  const totalRecentAlerts = useMemo(() => {
    return Object.values(zoneOccupancies).reduce((acc, o) => acc + (o.recent_alerts_count || 0), 0);
  }, [zoneOccupancies]);

  // SVG Drawing interactions
  const handleMapClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isDrawing) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const rawX = (e.clientX - rect.left) / rect.width;
    const rawY = (e.clientY - rect.top) / rect.height;
    const x = parseFloat(clamp01(rawX).toFixed(4));
    const y = parseFloat(clamp01(rawY).toFixed(4));

    // Check if clicking near start point to close
    if (drawPoints.length >= 3) {
      const startX = drawPoints[0][0];
      const startY = drawPoints[0][1];
      const distPx = Math.hypot((x - startX) * MAP_W, (y - startY) * MAP_H);
      if (distPx < 25) {
        setShowConfigModal(true);
        return;
      }
    }

    setDrawPoints((prev) => [...prev, [x, y]]);
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isDrawing || drawPoints.length === 0) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const rawX = (e.clientX - rect.left) / rect.width;
    const rawY = (e.clientY - rect.top) / rect.height;
    setCursorPos([parseFloat(clamp01(rawX).toFixed(4)), parseFloat(clamp01(rawY).toFixed(4))]);
  };

  const handleUndoPoint = () => {
    setDrawPoints((prev) => prev.slice(0, -1));
  };

  const handleClearPoints = () => {
    setDrawPoints([]);
    setCursorPos(null);
  };

  const handleCancelDrawing = () => {
    setIsDrawing(false);
    setDrawPoints([]);
    setCursorPos(null);
    setShowConfigModal(false);
  };

  const handleSaveZone = async (newZone: GeofenceZoneCreate) => {
    await createGeofenceZone(newZone);
    setIsDrawing(false);
    setDrawPoints([]);
    setCursorPos(null);
    await loadZones();
    await loadZoneOccupancy();
  };

  const handleToggleActive = async (zone: GeofenceZone) => {
    await updateGeofenceZone(zone.id, { is_active: !zone.is_active });
    await loadZones();
    await loadZoneOccupancy();
  };

  const handleDeleteZone = async (zoneId: number) => {
    await deleteGeofenceZone(zoneId);
    await loadZones();
    await loadZoneOccupancy();
  };

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
          title="Live Room Map & Geofence Zones"
          meta={`refresh ${POLL_MS / 1000}s`}
          actions={
            <div className="form-row" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
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

              {!isDrawing ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => setIsDrawing(true)}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                  <Polygon size={16} weight="bold" /> Draw Geofence Zone
                </button>
              ) : null}
            </div>
          }
        />

        {isDrawing && (
          <div className="draw-banner rise">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Polygon size={18} weight="duotone" />
              <span>
                <strong>Draw Mode Active:</strong> Click anywhere on the floor map to place polygon vertices.
                Current: <strong>{drawPoints.length}</strong> vertices placed. (Minimum 3 required).
              </span>
            </span>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="btn"
                onClick={handleUndoPoint}
                disabled={drawPoints.length === 0}
                style={{ fontSize: 12, padding: '4px 10px', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                <ArrowsCounterClockwise size={13} /> Undo
              </button>
              <button
                type="button"
                className="btn"
                onClick={handleClearPoints}
                disabled={drawPoints.length === 0}
                style={{ fontSize: 12, padding: '4px 10px', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                <Trash size={13} /> Clear
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setShowConfigModal(true)}
                disabled={drawPoints.length < 3}
                style={{ fontSize: 12, padding: '4px 10px', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                <Check size={14} weight="bold" /> Complete Zone ({drawPoints.length} pts)
              </button>
              <button
                type="button"
                className="btn"
                onClick={handleCancelDrawing}
                style={{ fontSize: 12, padding: '4px 10px', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                <X size={13} /> Cancel
              </button>
            </div>
          </div>
        )}

        <div className="grid-4" style={{ marginBottom: 16 }}>
          <Stat label="People tracked" value={positions.length} mono tone="var(--ice)" />
          <Stat label="Identified" value={knownCount} mono />
          <Stat
            label="Geofence zones"
            value={`${zones.filter((z) => z.is_active).length} / ${zones.length}`}
            sub="Active / Total"
            mono
          />
          <Stat
            label="Recent Zone Alerts"
            value={totalRecentAlerts}
            tone={totalRecentAlerts > 0 ? 'var(--bad)' : undefined}
            sub="Last 24 hours"
            mono
          />
        </div>

        {loading && positions.length === 0 && zones.length === 0 ? (
          <SkeletonCards count={1} />
        ) : (
          <figure style={{ margin: 0 }}>
            <svg
              ref={svgRef}
              viewBox={`0 0 ${MAP_W} ${MAP_H}`}
              role="img"
              aria-label="Room map with live person positions and geofence zones"
              style={{
                width: '100%',
                height: 'auto',
                display: 'block',
                background: 'var(--paper)',
                border: isDrawing ? '2px dashed var(--ice)' : '1px solid var(--rule)',
                borderRadius: 8,
                cursor: isDrawing ? 'crosshair' : 'default',
              }}
              onClick={handleMapClick}
              onMouseMove={handleMouseMove}
              onDoubleClick={() => {
                if (isDrawing && drawPoints.length >= 3) {
                  setShowConfigModal(true);
                }
              }}
            >
              <defs>
                <radialGradient id="heatblob">
                  <stop offset="0%" stopColor="var(--ice)" stopOpacity="0.5" />
                  <stop offset="55%" stopColor="var(--iris)" stopOpacity="0.22" />
                  <stop offset="100%" stopColor="var(--iris)" stopOpacity="0" />
                </radialGradient>
              </defs>

              {/* room outline and faint grid */}
              <rect x="1" y="1" width={MAP_W - 2} height={MAP_H - 2} rx="8" fill="none" stroke="none" />
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
                CAMERA PLAN VIEW {officeId ? `&middot; OFFICE ${officeId.toUpperCase()}` : ''}
              </text>

              {/* Heatmap density layer */}
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

              {/* Geofence Polygons Layer */}
              {zones.map((zone) => {
                const pointsStr = zone.polygon
                  .map(([x, y]) => `${clamp01(x) * MAP_W},${clamp01(y) * MAP_H}`)
                  .join(' ');
                const centroid = computeCentroid(zone.polygon);
                const occ = zoneOccupancies[zone.id];
                const count = occ ? occ.current_count : 0;
                const alertsCount = occ ? occ.recent_alerts_count : 0;
                const isRestrictedBreach = zone.zone_type === 'restricted' && count > 0;
                const isHovered = hoveredZoneId === zone.id;

                const badgeWidth = Math.max(76, zone.name.length * 7.5 + (count > 0 ? 34 : 14));
                const badgeHeight = 22;

                return (
                  <g key={`zone-${zone.id}`}>
                    <polygon
                      points={pointsStr}
                      fill={zone.color}
                      fillOpacity={zone.is_active ? (isHovered ? 0.32 : 0.20) : 0.05}
                      stroke={zone.color}
                      strokeWidth={isHovered ? 2.5 : zone.is_active ? 1.8 : 1}
                      strokeDasharray={zone.is_active ? undefined : '5,5'}
                      onMouseEnter={() => setHoveredZoneId(zone.id)}
                      onMouseLeave={() => setHoveredZoneId(null)}
                      style={{ cursor: 'pointer', transition: 'fill-opacity 0.2s, stroke-width 0.2s' }}
                    >
                      <title>{`${zone.name} (${zone.zone_type}): ${count} occupants, ${alertsCount} alerts (24h)`}</title>
                    </polygon>

                    {/* Zone Badge at Centroid */}
                    <g
                      transform={`translate(${centroid.cx}, ${centroid.cy})`}
                      pointerEvents="none"
                      style={{ transition: 'transform 0.2s ease' }}
                    >
                      <rect
                        x={-badgeWidth / 2}
                        y={-badgeHeight / 2}
                        width={badgeWidth}
                        height={badgeHeight}
                        rx={4}
                        fill="var(--paper)"
                        stroke={zone.color}
                        strokeWidth="1.2"
                        opacity={zone.is_active ? 0.95 : 0.65}
                      />
                      <text
                        x={0}
                        y={4}
                        textAnchor="middle"
                        fontSize="11"
                        fontWeight="600"
                        fill="var(--ink)"
                        fontFamily="var(--font-display)"
                      >
                        {zone.name} {count > 0 ? `(${count})` : ''}
                      </text>

                      {/* Breach / Alert Pulsing Beacon */}
                      {(isRestrictedBreach || alertsCount > 0) && (
                        <g transform={`translate(${badgeWidth / 2 + 2}, ${-badgeHeight / 2 + 2})`}>
                          <circle cx={0} cy={0} r={6} fill="var(--bad)" className="pulse-indicator" />
                          <circle cx={0} cy={0} r={3.5} fill="#ffffff" />
                        </g>
                      )}
                    </g>
                  </g>
                );
              })}

              {/* Active Drawing Rubber-band & Vertices Layer */}
              {isDrawing && (
                <g>
                  {drawPoints.length >= 2 && (
                    <polyline
                      points={drawPoints.map(([x, y]) => `${clamp01(x) * MAP_W},${clamp01(y) * MAP_H}`).join(' ')}
                      fill="none"
                      stroke="var(--ice)"
                      strokeWidth="2"
                      strokeDasharray="4 2"
                    />
                  )}

                  {cursorPos && drawPoints.length > 0 && (
                    <line
                      x1={drawPoints[drawPoints.length - 1][0] * MAP_W}
                      y1={drawPoints[drawPoints.length - 1][1] * MAP_H}
                      x2={cursorPos[0] * MAP_W}
                      y2={cursorPos[1] * MAP_H}
                      stroke="var(--ice)"
                      strokeWidth="1.5"
                      strokeDasharray="3 3"
                    />
                  )}

                  {drawPoints.map(([x, y], idx) => {
                    const isFirst = idx === 0;
                    const canClose = isFirst && drawPoints.length >= 3;
                    return (
                      <g key={`vertex-${idx}`}>
                        <circle
                          cx={x * MAP_W}
                          cy={y * MAP_H}
                          r={canClose ? 11 : 6.5}
                          fill={isFirst ? 'var(--ice)' : 'var(--paper)'}
                          stroke="var(--ice)"
                          strokeWidth="2"
                        />
                        <text
                          x={x * MAP_W}
                          y={y * MAP_H + 3.5}
                          textAnchor="middle"
                          fontSize="9.5"
                          fontWeight="700"
                          fill={isFirst ? '#ffffff' : 'var(--ice)'}
                          fontFamily="var(--font-mono)"
                          pointerEvents="none"
                        >
                          {idx + 1}
                        </text>
                        {canClose && (
                          <text
                            x={x * MAP_W}
                            y={y * MAP_H - 15}
                            textAnchor="middle"
                            fontSize="10"
                            fontWeight="600"
                            fill="var(--ice)"
                            fontFamily="var(--font-mono)"
                          >
                            Click to close
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
              )}

              {/* Person markers */}
              {positions.map((p) => {
                const cx = clamp01(p.x) * MAP_W;
                const cy = clamp01(p.y) * MAP_H;
                const known = isKnownName(p.person_name);
                const conf =
                  typeof p.person_name_confidence === 'number' &&
                  Number.isFinite(p.person_name_confidence)
                    ? p.person_name_confidence.toFixed(2)
                    : null;
                const label = known
                  ? conf !== null
                    ? `${p.person_name as string} ${conf}`
                    : (p.person_name as string)
                  : 'Unknown';
                const fill = known ? 'var(--ice)' : 'var(--ink)';
                const opacity = known ? 1 : 0.55;
                return (
                  <g key={p.track_id} opacity={opacity}>
                    <title>{`${label} (${p.track_id}) on ${p.camera_id}`}</title>
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
                      fill={known ? 'var(--text-1)' : 'var(--text-2)'}
                      fontWeight={known ? 600 : 400}
                      fontFamily="var(--font-display)"
                    >
                      {label}
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
                Identified person
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--ink)', opacity: 0.55, display: 'inline-block' }} />
                Unknown track
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 12, height: 12, borderRadius: 2, background: 'rgba(239, 68, 68, 0.4)', border: '1px solid #ef4444', display: 'inline-block' }} />
                Geofence zone
              </span>
              {heatmapOn && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ width: 14, height: 14, borderRadius: '50%', background: 'radial-gradient(circle, var(--ice) 0%, var(--iris) 60%, transparent 100%)', display: 'inline-block' }} />
                  Dwell density
                </span>
              )}
              <span style={{ marginLeft: 'auto' }}>
                Normalized room coordinates in [0.0, 1.0]. Polygons define spatial detection triggers.
              </span>
            </figcaption>
          </figure>
        )}
      </section>

      {/* Geofence Configuration Modal */}
      <GeofenceConfigModal
        isOpen={showConfigModal}
        polygon={drawPoints}
        officeId={officeId}
        cameras={cameras}
        onSave={handleSaveZone}
        onClose={() => setShowConfigModal(false)}
      />

      {/* Geofence Zone Management & Activity Feed */}
      <div className="grid-2" style={{ marginTop: 20 }}>
        <GeofenceZoneList
          zones={zones}
          occupancies={zoneOccupancies}
          onToggleActive={handleToggleActive}
          onDeleteZone={handleDeleteZone}
        />
        <GeofenceActivityFeed events={geofenceEvents} />
      </div>

      <section className="panel" aria-label="Occupancy timeline" style={{ marginTop: 20 }}>
        <SectionHead title="Occupancy Timeline" meta="last 24h, 15m buckets" />
        {occupancy.length === 0 ? (
          <p style={{ color: 'var(--text-2)', fontSize: 13.5, margin: '10px 0 0' }}>
            Occupancy buckets will accumulate here once the vision pipeline reports tracked persons over time.
          </p>
        ) : (
          <OccupancyChart buckets={occupancy} />
        )}
      </section>

      <div className="grid-2" style={{ marginTop: 20 }}>
        <UnknownPeoplePanel />
        <KnownPeopleList />
      </div>
    </div>
  );
}
