"""
Pydantic v2 schemas for Geofence Zones.

Validates coordinate boundaries [0.0, 1.0], minimum 3 polygon vertices,
zone types, and dwell thresholds.
"""

from datetime import datetime
from enum import Enum
import re
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ZoneType(str, Enum):
    restricted = "restricted"
    workstation = "workstation"
    entry_exit = "entry_exit"
    custom = "custom"


ZONE_TYPE_DEFAULT_COLORS = {
    ZoneType.restricted: "#ef4444",
    ZoneType.workstation: "#3b82f6",
    ZoneType.entry_exit: "#10b981",
    ZoneType.custom: "#8b5cf6",
}

HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def validate_polygon_coordinates(polygon: List[List[float]]) -> List[List[float]]:
    """Validate that polygon contains at least 3 vertices strictly within [0.0, 1.0]."""
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError("Polygon must contain at least 3 vertices")

    validated: List[List[float]] = []
    for idx, pt in enumerate(polygon):
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError(f"Vertex at index {idx} must be a pair [x, y]")
        x, y = pt
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError(f"Coordinates at vertex {idx} must be numbers")
        x_f, y_f = float(x), float(y)
        if not (0.0 <= x_f <= 1.0) or not (0.0 <= y_f <= 1.0):
            raise ValueError(
                f"Coordinates at vertex {idx} ({x_f}, {y_f}) must be normalized within [0.0, 1.0]"
            )
        validated.append([x_f, y_f])
    return validated


class GeofenceZoneBase(BaseModel):
    name: str = Field(..., max_length=128, description="Name of the geofence zone")
    office_id: str = Field("hq", max_length=64, description="Office/site identifier")
    camera_id: Optional[str] = Field(None, max_length=64, description="Bound camera identifier")
    zone_type: ZoneType = Field(ZoneType.restricted, description="Zone category")
    color: Optional[str] = Field(None, max_length=16, description="Hex color string")
    alert_on_enter: bool = Field(True, description="Trigger alert upon entry")
    alert_on_exit: bool = Field(False, description="Trigger alert upon exit")
    dwell_threshold_seconds: Optional[int] = Field(
        None, gt=0, description="Dwell time trigger threshold in seconds"
    )
    is_active: bool = Field(True, description="Whether this zone is actively monitored")

    @field_validator("name")
    @classmethod
    def validate_name_not_empty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Zone name cannot be empty or whitespace only")
        return s

    @field_validator("color")
    @classmethod
    def validate_color_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not HEX_COLOR_PATTERN.match(s):
                raise ValueError(f"Invalid hex color: {v}. Must be format #RRGGBB or #RGB")
            return s
        return v


class GeofenceZoneCreate(GeofenceZoneBase):
    polygon: List[List[float]] = Field(
        ..., description="Array of [x, y] coordinates forming the polygon"
    )

    @field_validator("polygon")
    @classmethod
    def validate_poly(cls, v: List[List[float]]) -> List[List[float]]:
        return validate_polygon_coordinates(v)

    @model_validator(mode="after")
    def apply_default_color(self) -> "GeofenceZoneCreate":
        if not self.color:
            self.color = ZONE_TYPE_DEFAULT_COLORS.get(self.zone_type, "#ef4444")
        return self


class GeofenceZoneUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    office_id: Optional[str] = Field(None, max_length=64)
    camera_id: Optional[str] = Field(None, max_length=64)
    zone_type: Optional[ZoneType] = None
    polygon: Optional[List[List[float]]] = None
    color: Optional[str] = Field(None, max_length=16)
    alert_on_enter: Optional[bool] = None
    alert_on_exit: Optional[bool] = None
    dwell_threshold_seconds: Optional[int] = Field(None, gt=0)
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("Zone name cannot be empty or whitespace only")
            return s
        return v

    @field_validator("polygon")
    @classmethod
    def validate_poly_opt(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        if v is not None:
            return validate_polygon_coordinates(v)
        return v

    @field_validator("color")
    @classmethod
    def validate_color_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not HEX_COLOR_PATTERN.match(s):
                raise ValueError(f"Invalid hex color: {v}. Must be format #RRGGBB or #RGB")
            return s
        return v


class GeofenceZoneOut(BaseModel):
    id: int
    office_id: str
    camera_id: Optional[str]
    name: str
    zone_type: str
    polygon: List[List[float]]
    color: str
    alert_on_enter: bool
    alert_on_exit: bool
    dwell_threshold_seconds: Optional[int]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TransitionType(str, Enum):
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    DWELL_EXCEEDED = "DWELL_EXCEEDED"


class GeofenceEventCreate(BaseModel):
    zone_id: int
    transition_type: TransitionType
    track_id: int
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    office_id: Optional[str] = None
    camera_id: Optional[str] = None
    person_label: Optional[str] = None
    identity_id: Optional[str] = None
    identity_name: Optional[str] = None
    dwell_seconds: Optional[float] = None
    alert_triggered: Optional[bool] = None
    privacy_mode: Optional[bool] = None
    timestamp: Optional[datetime] = None


class GeofenceEventOut(BaseModel):
    id: int
    zone_id: int
    zone_name: str
    zone_type: str
    office_id: str
    camera_id: Optional[str]
    transition_type: str
    track_id: int
    person_label: str
    identity_id: Optional[str]
    identity_name: Optional[str]
    dwell_seconds: Optional[float]
    alert_triggered: bool
    timestamp: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ZoneOccupant(BaseModel):
    track_id: int
    person_label: str
    entered_at: datetime
    dwell_seconds: Optional[float] = None
    identity_name: Optional[str] = None


class ZoneOccupancyOut(BaseModel):
    zone_id: int
    zone_name: str
    zone_type: str
    color: str
    current_count: int
    occupants: List[ZoneOccupant]
    recent_alerts_count: int

