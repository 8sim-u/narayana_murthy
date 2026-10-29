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
