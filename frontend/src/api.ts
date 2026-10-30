import type {
  DbHealthStatus,
  DetectionEvent,
  EventCreate,
  EventResponse,
  HealthStatus,
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
