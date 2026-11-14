import type {
  DbHealthStatus,
  DetectionEvent,
  EventCreate,
  EventResponse,
  GeofenceEventRecord,
  GeofenceZone,
  GeofenceZoneCreate,
  GeofenceZoneUpdate,
  HealthStatus,
  HeatPoint,
  Identity,
  OccupancyBucket,
  PersonPosition,
  UnknownSighting,
  ZoneOccupancy,
} from './types';

// Same-origin by default: the dashboard calls the relative path /api/*,
// which the deployment's nginx reverse-proxies to the backend with the API
// key attached server-side (see frontend/nginx.conf.template, docs/AUTH.md).
// Set VITE_API_URL at build time only to point the dashboard at a remote
// backend directly; the browser must then reach it, and without a proxy the
// authenticated endpoints return 401 (browsers cannot hold the shared key).
const API_URL = (import.meta.env.VITE_API_URL as string | undefined) || '/api';

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Request failed (${res.status}): ${body || res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API_URL}/health`);
  return handleResponse<HealthStatus>(res);
}

export async function fetchDbHealth(): Promise<DbHealthStatus> {
  const res = await fetch(`${API_URL}/health/db`);
  return handleResponse<DbHealthStatus>(res);
}

export async function fetchEvents(cameraId?: string, limit = 100): Promise<DetectionEvent[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: '0' });
  if (cameraId) {
    params.set('camera_id', cameraId);
  }
  const res = await fetch(`${API_URL}/events?${params.toString()}`);
  return handleResponse<DetectionEvent[]>(res);
}

export async function sendEvent(payload: EventCreate): Promise<EventResponse> {
  const res = await fetch(`${API_URL}/event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handleResponse<EventResponse>(res);
}

export function getApiUrl(): string {
  return API_URL;
}

// Live tracked-person positions for the Room Map tab.
export async function getPositions(): Promise<PersonPosition[]> {
  const res = await fetch(`${API_URL}/positions`);
  return handleResponse<PersonPosition[]>(res);
}

// Density samples for the Room Map heatmap overlay.
export async function getHeatmap(cameraId?: string, hours = 24): Promise<HeatPoint[]> {
  const params = new URLSearchParams({ hours: String(hours) });
  if (cameraId) {
    params.set('camera_id', cameraId);
  }
  const res = await fetch(`${API_URL}/heatmap?${params.toString()}`);
  return handleResponse<HeatPoint[]>(res);
}

// Person counts per time bucket for the occupancy timeline.
export async function getOccupancy(hours = 24, bucket = '15m'): Promise<OccupancyBucket[]> {
  const params = new URLSearchParams({ hours: String(hours), bucket });
  const res = await fetch(`${API_URL}/occupancy?${params.toString()}`);
  return handleResponse<OccupancyBucket[]>(res);
}

// Resolve an event snapshot_path to a same-origin URL the browser can load.
export function snapshotUrl(snapshotPath: string): string {
  const filename = snapshotPath.split('/').pop() ?? snapshotPath;
  return `${API_URL}/snapshots/${encodeURIComponent(filename)}`;
}

// Fetch a list endpoint that may not exist yet (identity endpoints land
// separately from the UI). A 404 means "not available yet" and resolves to
// the fallback instead of throwing; any other failure still throws.
async function fetchOptionalList<T>(path: string, fallback: T[]): Promise<T[]> {
  const res = await fetch(`${API_URL}${path}`);
  if (res.status === 404) return fallback;
  return handleResponse<T[]>(res);
}

// GET /identities -> enrolled people registry. Returns [] when the
// endpoint is not deployed yet.
export async function getIdentities(): Promise<Identity[]> {
  return fetchOptionalList<Identity>('/identities', []);
}

// GET /unknowns -> recent unknown sightings awaiting review. Returns []
// when the endpoint is not deployed yet.
export async function getUnknowns(): Promise<UnknownSighting[]> {
  return fetchOptionalList<UnknownSighting>('/unknowns', []);
}

// POST /faces/enroll -> enroll an unknown sighting under a typed name.
// Sends JSON { name, snapshot_path } as specified for the identity build.
export async function enrollFace(name: string, snapshotPath: string): Promise<unknown> {
  const res = await fetch(`${API_URL}/faces/enroll`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, snapshot_path: snapshotPath }),
  });
  return handleResponse<unknown>(res);
}

// DELETE /faces/{name} -> remove an enrolled person from the registry.
export async function deleteIdentity(name: string): Promise<unknown> {
  const res = await fetch(`${API_URL}/faces/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  return handleResponse<unknown>(res);
}

// --------------------------- Geofence Zones API ----------------------------

export async function getGeofenceZones(
  officeId?: string,
  cameraId?: string,
  isActive?: boolean,
  zoneType?: string,
): Promise<GeofenceZone[]> {
  const params = new URLSearchParams();
  if (officeId) params.set('office_id', officeId);
  if (cameraId) params.set('camera_id', cameraId);
  if (isActive !== undefined) params.set('is_active', String(isActive));
  if (zoneType) params.set('zone_type', zoneType);
  const q = params.toString();
  return fetchOptionalList<GeofenceZone>(`/zones${q ? `?${q}` : ''}`, []);
}

export async function getGeofenceZone(id: number): Promise<GeofenceZone> {
  const res = await fetch(`${API_URL}/zones/${id}`);
  return handleResponse<GeofenceZone>(res);
}

export async function createGeofenceZone(zone: GeofenceZoneCreate): Promise<GeofenceZone> {
  const res = await fetch(`${API_URL}/zones`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(zone),
  });
  return handleResponse<GeofenceZone>(res);
}

export async function updateGeofenceZone(
  id: number,
  patch: GeofenceZoneUpdate,
): Promise<GeofenceZone> {
  const res = await fetch(`${API_URL}/zones/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return handleResponse<GeofenceZone>(res);
}

export async function deleteGeofenceZone(id: number): Promise<{ id: number; status: string }> {
  const res = await fetch(`${API_URL}/zones/${id}`, {
    method: 'DELETE',
  });
  return handleResponse<{ id: number; status: string }>(res);
}

export async function getZoneOccupancy(
  officeId?: string,
  cameraId?: string,
): Promise<ZoneOccupancy[]> {
  const params = new URLSearchParams();
  if (officeId) params.set('office_id', officeId);
  if (cameraId) params.set('camera_id', cameraId);
  const q = params.toString();
  return fetchOptionalList<ZoneOccupancy>(`/zones/occupancy${q ? `?${q}` : ''}`, []);
}

export async function getGeofenceEvents(filters?: {
  office_id?: string;
  zone_id?: number;
  camera_id?: string;
  transition_type?: string;
  alert_triggered?: boolean;
  limit?: number;
}): Promise<GeofenceEventRecord[]> {
  const params = new URLSearchParams();
  if (filters?.office_id) params.set('office_id', filters.office_id);
  if (filters?.zone_id !== undefined) params.set('zone_id', String(filters.zone_id));
  if (filters?.camera_id) params.set('camera_id', filters.camera_id);
  if (filters?.transition_type) params.set('transition_type', filters.transition_type);
  if (filters?.alert_triggered !== undefined)
    params.set('alert_triggered', String(filters.alert_triggered));
  if (filters?.limit) params.set('limit', String(filters.limit));
  const q = params.toString();
  return fetchOptionalList<GeofenceEventRecord>(`/zones/events${q ? `?${q}` : ''}`, []);
}

