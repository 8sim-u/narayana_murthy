from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class EventCreate(BaseModel):
    camera_id: str = Field(..., min_length=1, description="Camera identifier (e.g. CAM_01)")
    track_id: str = Field(..., min_length=1, description="Tracker identification string")
    objects: List[str] = Field(default=["person"], description="List of detected object labels")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score between 0.0 and 1.0")
    zone: str = Field(default="UNKNOWN", description="Monitoring zone identifier")


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

    model_config = ConfigDict(from_attributes=True)
