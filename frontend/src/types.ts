// Types mirroring the WAMSS FastAPI backend schemas (see backend/app/schemas.py).
// Identifier names and values are copied exactly from the backend responses.

export interface HealthStatus {
  status: string;
  service: string;
  environment: string;
}

export interface DbHealthStatus {
  status: string;
  database: string;
}

export interface DetectionEvent {
  id: number;
  camera_id: string;
  track_id: string;
  objects: string[];
  confidence: number;
  zone: string;
  decision: string;
  alerts: string[];
  timestamp: string;
  // Person identification fields added with the tracking rollout.
  // person_name is the enrolled name, or "unknown" when unrecognized.
  // snapshot_path is the backend thumbnail path for this event.
  person_name?: string | null;
  snapshot_path?: string | null;
}

export interface EventCreate {
  camera_id: string;
  track_id: string;
  objects: string[];
  confidence: number;
  zone: string;
}

export interface EventResponse {
  received: boolean;
  camera_id: string;
  alerts: string[];
  decision: string;
}

// GET /positions -> live tracked-person positions per camera.
// x and y are normalized room coordinates in [0, 1].
export interface PersonPosition {
  track_id: string;
  person_name: string | null;
  x: number;
  y: number;
  camera_id: string;
  last_seen: string;
}

// GET /heatmap -> density samples for the heatmap overlay.
// x and y are normalized room coordinates in [0, 1].
export interface HeatPoint {
  x: number;
  y: number;
  weight: number;
}

// GET /occupancy -> person count per time bucket.
export interface OccupancyBucket {
  t: string;
  count: number;
  camera_id: string;
}
