from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DetectionEvent(Base):
    __tablename__ = "detection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    track_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    objects: Mapped[List[Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    zone: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    alerts: Mapped[List[Any]] = mapped_column(JSON, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    # Edge roadmap fields (added 2026-11-02). All nullable so older rows and
    # older edge payloads keep working.
    person_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Internal ownership key captured at consent validation; never serialized.
    employee_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    person_name_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    position_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    position_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Camera-map dimensions let consumers normalize calibrated meter points
    # without changing the coordinates retained by the existing event ledger.
    position_bounds: Mapped[Optional[List[float]]] = mapped_column(JSON, nullable=True)
    snapshot_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    dwell_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class AggregateOccupancyEvent(Base):
    """Aggregate-only camera occupancy; deliberately has no identity or track fields."""

    __tablename__ = "aggregate_occupancy_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    occupancy: Mapped[str] = mapped_column(String(8), nullable=False)
    person_count: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )


class GeofenceZone(Base):
    """A named normalized polygon configured for one camera."""

    __tablename__ = "geofence_zones"
    __table_args__ = (UniqueConstraint("camera_id", "name", name="uq_geofence_camera_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    polygon: Mapped[List[List[float]]] = mapped_column(JSON, nullable=False)


class GeofenceEvent(Base):
    """Minimal transition history; deliberately omits coordinates and track IDs."""

    __tablename__ = "geofence_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    zone_name: Mapped[str] = mapped_column(String(80), nullable=False)
    transition: Mapped[str] = mapped_column(String(8), nullable=False)
    person_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Nullable so legacy rows remain explicitly unowned and are never shown in personal history.
    employee_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )


class PersonIdentity(Base):
    """Persistent known-persons registry.

    One row per known person, surviving backend restarts. A person is
    known when they have a gallery entry (faces table) and/or a row here.
    Enrollment creates the row; later sightings bump last_seen, sightings,
    and the list of cameras the person has been seen on. ``cameras`` is a
    JSON list of camera ids.
    """

    __tablename__ = "person_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sightings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cameras: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)


class FaceRecord(Base):
    """Server-side face gallery for person identification.

    One row per enrolled person. ``embedding`` holds the face encoding as a
    plain list of floats (or null when no face library is available, in which
    case only the photo is stored and ``face_available`` is False).
    """

    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(JSON, nullable=True)
    photo_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    face_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
