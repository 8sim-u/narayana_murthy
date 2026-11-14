"""
SQLAlchemy model for Geofence Zones (Build #2: Real Geofence Zones).

Defines persistent 2D geofence polygons scoped by office and camera,
with zone types, alert triggers, and dwell thresholds.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class GeofenceZone(Base):
    __tablename__ = "geofence_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    office_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="hq", server_default="hq"
    )
    camera_id: Mapped[Optional[str]] = mapped_column(
        String(64), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Zone type: "restricted", "workstation", "entry_exit", or "custom"
    zone_type: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False, default="restricted"
    )
    # Normalized 2D coordinates: [[x1, y1], [x2, y2], ...] with x, y in [0.0, 1.0]
    polygon: Mapped[List[Any]] = mapped_column(JSON, nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#ef4444")
    alert_on_enter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    alert_on_exit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dwell_threshold_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class GeofenceEvent(Base):
    __tablename__ = "geofence_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    zone_name: Mapped[str] = mapped_column(String(128), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(32), nullable=False)
    office_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False, default="hq", server_default="hq"
    )
    camera_id: Mapped[Optional[str]] = mapped_column(
        String(64), index=True, nullable=True
    )
    transition_type: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False
    )
    track_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    person_label: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    identity_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    dwell_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alert_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

