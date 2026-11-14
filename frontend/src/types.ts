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
  // Identity confidence for the recognized person, when the vision
  // pipeline reports one. Null when the person is unknown or no score
  // was produced.
  person_name_confidence?: number | null;
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
  // Identity confidence for person_name, when the vision pipeline
  // reports one. Null when unknown or unscored.
  person_name_confidence?: number | null;
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

// GET /identities -> enrolled people registry. All fields except name are
// optional: the endpoint may be absent (404) or a row may lack a value.
export interface Identity {
  name: string;
  enrolled_at?: string | null;
  last_seen?: string | null;
  sightings?: number | null;
  cameras?: string[] | null;
}

// GET /unknowns -> recent unknown sightings awaiting review. Fields are
// optional so a missing or partial row never breaks the panel.
export interface UnknownSighting {
  id?: string | number | null;
  track_id?: string | null;
  snapshot_path?: string | null;
  camera_id?: string | null;
  timestamp?: string | null;
  last_seen?: string | null;
}

// --------------------------- Geofence Zones --------------------------------

export type ZoneType = 'restricted' | 'workstation' | 'entry_exit' | 'custom';

export interface GeofenceZone {
  id: number;
  office_id: string;
  camera_id: string | null;
  name: string;
  zone_type: ZoneType;
  polygon: [number, number][];
  color: string;
  alert_on_enter: boolean;
  alert_on_exit: boolean;
  dwell_threshold_seconds: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface GeofenceZoneCreate {
  name: string;
  office_id?: string;
  camera_id?: string | null;
  zone_type?: ZoneType;
  polygon: [number, number][];
  color?: string;
  alert_on_enter?: boolean;
  alert_on_exit?: boolean;
  dwell_threshold_seconds?: number | null;
  is_active?: boolean;
}

export interface GeofenceZoneUpdate {
  name?: string;
  office_id?: string;
  camera_id?: string | null;
  zone_type?: ZoneType;
  polygon?: [number, number][];
  color?: string;
  alert_on_enter?: boolean;
  alert_on_exit?: boolean;
  dwell_threshold_seconds?: number | null;
  is_active?: boolean;
}

export type TransitionType = 'ZONE_ENTER' | 'ZONE_EXIT' | 'DWELL_EXCEEDED';

export interface GeofenceEventRecord {
  id: number;
  zone_id: number;
  zone_name: string;
  zone_type: string;
  office_id: string;
  camera_id: string | null;
  transition_type: TransitionType;
  track_id: number;
  person_label: string;
  identity_id: string | null;
  identity_name: string | null;
  dwell_seconds: number | null;
  alert_triggered: boolean;
  timestamp: string;
  created_at?: string;
}

export interface ZoneOccupant {
  track_id: number;
  person_label: string;
  entered_at: string;
  dwell_seconds?: number | null;
  identity_name?: string | null;
}

export interface ZoneOccupancy {
  zone_id: number;
  zone_name: string;
  zone_type: string;
  color: string;
  current_count: number;
  occupants: ZoneOccupant[];
  recent_alerts_count: number;
}

