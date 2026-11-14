from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventCreate(BaseModel):
    camera_id: str = Field(..., min_length=1, description="Camera identifier (e.g. CAM_01)")
    track_id: str = Field(..., min_length=1, description="Tracker identification string")
    objects: List[str] = Field(default=["person"], description="List of detected object labels")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score between 0.0 and 1.0")
    zone: str = Field(default="UNKNOWN", description="Monitoring zone identifier")
    # Edge roadmap fields (all optional for backward compatibility).
    person_name: Optional[str] = Field(default=None, description="Recognized person name, or 'unknown'")
    person_name_confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Confidence of the person identification (0.0-1.0); null when the vision node did not report one",
    )
    position_x: Optional[float] = Field(default=None, description="Normalized x position in frame (0.0-1.0)")
    position_y: Optional[float] = Field(default=None, description="Normalized y position in frame (0.0-1.0)")
    snapshot_path: Optional[str] = Field(default=None, description="Snapshot file path relative to data/, e.g. snapshots/Person_1_20261102_120501.jpg")
    dwell_seconds: Optional[float] = Field(default=None, ge=0.0, description="Time the tracked person has been in frame")


class EventResponse(BaseModel):
    received: bool = True
    camera_id: str
    alerts: List[str]
    decision: str


class EventDetail(BaseModel):
    id: int
    camera_id: str
    track_id: str
    objects: List[str]
    confidence: float
    zone: str
    decision: str
    alerts: List[str]
    timestamp: datetime
    person_name: Optional[str] = None
    person_name_confidence: Optional[float] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    snapshot_path: Optional[str] = None
    dwell_seconds: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


# Re-export Geofence schemas
from app.geofence_schemas import (  # noqa: E402, F401
    GeofenceEventCreate,
    GeofenceEventOut,
    GeofenceZoneCreate,
    GeofenceZoneOut,
    GeofenceZoneUpdate,
    TransitionType,
    ZoneOccupancyOut,
    ZoneOccupant,
    ZoneType,
)


