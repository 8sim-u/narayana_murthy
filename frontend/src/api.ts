import type {
  DbHealthStatus,
  DetectionEvent,
  EventCreate,
  EventResponse,
  HealthStatus,
  GeofenceEvent,
  GeofenceZone,
  HeatPoint,
  Identity,
  OccupancyBucket,
  PersonPosition,
  UnknownSighting,
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

// Per-camera geofence polygons drawn on the normalized Room Map.
export async function getGeofences(): Promise<GeofenceZone[]> {
  const res = await fetch(`${API_URL}/geofences`);
  return handleResponse<GeofenceZone[]>(res);
}

export async function createGeofence(
  payload: Pick<GeofenceZone, 'camera_id' | 'name' | 'polygon'>,
): Promise<GeofenceZone> {
  const res = await fetch(`${API_URL}/geofences`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handleResponse<GeofenceZone>(res);
}

export async function deleteGeofence(zoneId: number): Promise<{ deleted: boolean; id: number }> {
  const res = await fetch(`${API_URL}/geofences/${zoneId}`, { method: 'DELETE' });
  return handleResponse<{ deleted: boolean; id: number }>(res);
}

export async function getMyGeofenceEvents(
  employeeToken: string,
  limit = 50,
): Promise<GeofenceEvent[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await fetch(`${API_URL}/geofence-events?${params.toString()}`, {
    headers: { 'X-Employee-Token': employeeToken },
    cache: 'no-store',
  });
  return handleResponse<GeofenceEvent[]>(res);
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

// POST /faces/enroll -> enroll only after backend verifies active consent
// for the employee on the originating camera.
export async function enrollFace(name: string, snapshotPath: string, cameraId: string): Promise<unknown> {
  const res = await fetch(`${API_URL}/faces/enroll`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, snapshot_path: snapshotPath, camera_id: cameraId }),
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
