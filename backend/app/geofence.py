"""
Geofence Zones & Events REST API (Build #2: Real Geofence Zones).

Endpoints:
  POST   /api/zones           Create a new geofence zone
  GET    /api/zones           List zones with optional filters (office_id, camera_id, is_active, zone_type)
  GET    /api/zones/events    List recent geofence transition events with filters
  POST   /api/zones/events   Ingest single or batch geofence transition events
  GET    /api/zones/occupancy Get live per-zone occupancy summary
  GET    /api/zones/{zone_id} Fetch a single zone by ID
  PATCH  /api/zones/{zone_id} Partial update of zone attributes
  DELETE /api/zones/{zone_id} Delete a zone
"""

from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.alerts import dispatch_alert
from app.database import get_db
from app.geofence_models import GeofenceEvent, GeofenceZone
from app.geofence_schemas import (
    GeofenceEventCreate,
    GeofenceEventOut,
    GeofenceZoneCreate,
    GeofenceZoneOut,
    GeofenceZoneUpdate,
    ZoneOccupancyOut,
    ZoneOccupant,
)

logger = logging.getLogger("wamss.api.geofence")

router = APIRouter(prefix="/api/zones", tags=["geofences"])


# ---------------------------------------------------------------------------
# Zone CRUD Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=GeofenceZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(zone_in: GeofenceZoneCreate, db: Session = Depends(get_db)):
    """Create a new geofence zone with normalized polygon boundaries."""
    now_utc = datetime.now(timezone.utc)
    db_zone = GeofenceZone(
        name=zone_in.name,
        office_id=zone_in.office_id,
        camera_id=zone_in.camera_id,
        zone_type=zone_in.zone_type.value,
        polygon=zone_in.polygon,
        color=zone_in.color or "#ef4444",
        alert_on_enter=zone_in.alert_on_enter,
        alert_on_exit=zone_in.alert_on_exit,
        dwell_threshold_seconds=zone_in.dwell_threshold_seconds,
        is_active=zone_in.is_active,
        created_at=now_utc,
        updated_at=now_utc,
    )
    try:
        db.add(db_zone)
        db.commit()
        db.refresh(db_zone)
        return db_zone
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Failed to create geofence zone: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to create geofence zone",
        )


@router.get("", response_model=List[GeofenceZoneOut])
def list_zones(
    office_id: Optional[str] = Query(None, description="Filter by office ID"),
    camera_id: Optional[str] = Query(None, description="Filter by bound camera ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    zone_type: Optional[str] = Query(None, description="Filter by zone type"),
    db: Session = Depends(get_db),
):
    """List geofence zones with optional scoping filters."""
    try:
        query = db.query(GeofenceZone)
        if office_id is not None:
            query = query.filter(GeofenceZone.office_id == office_id)
        if camera_id is not None:
            query = query.filter(GeofenceZone.camera_id == camera_id)
        if is_active is not None:
            query = query.filter(GeofenceZone.is_active == is_active)
        if zone_type is not None:
            query = query.filter(GeofenceZone.zone_type == zone_type)

        zones = query.order_by(GeofenceZone.id.asc()).all()
        return zones
    except SQLAlchemyError as exc:
        logger.error(f"Failed to query geofence zones: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query geofence zones",
        )


# ---------------------------------------------------------------------------
# Geofence Events & Occupancy Endpoints (Stage 3)
# ---------------------------------------------------------------------------


def _process_single_event(
    ev: GeofenceEventCreate,
    db: Session,
    zones_by_id: Dict[int, GeofenceZone],
) -> GeofenceEvent:
    """Validate, apply privacy mode rules, alert triggers, and build GeofenceEvent."""
    now_utc = datetime.now(timezone.utc)
    ts = ev.timestamp or now_utc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    zone = zones_by_id.get(ev.zone_id)
    if not zone:
        zone = db.query(GeofenceZone).filter(GeofenceZone.id == ev.zone_id).first()
        if zone:
            zones_by_id[ev.zone_id] = zone

    zone_name = ev.zone_name or (zone.name if zone else f"Zone {ev.zone_id}")
    zone_type = ev.zone_type or (zone.zone_type if zone else "custom")
    office_id = ev.office_id or (zone.office_id if zone else "hq")
    camera_id = ev.camera_id or (zone.camera_id if zone else None)

    # Privacy Mode double-enforcement:
    is_privacy = (
        os.environ.get("WAMSS_PRIVACY_MODE", "0").lower() in ("1", "true", "yes", "on")
        or (ev.privacy_mode is True)
    )

    if is_privacy:
        identity_id = None
        identity_name = None
        person_label = f"Person {ev.track_id}"
    else:
        identity_id = ev.identity_id
        identity_name = ev.identity_name
        person_label = (
            ev.person_label
            or (ev.identity_name if ev.identity_name else f"Person {ev.track_id}")
        )

    # Determine alert trigger
    if ev.alert_triggered is not None:
        alert_triggered = ev.alert_triggered
    elif zone:
        if ev.transition_type.value == "ZONE_ENTER":
            alert_triggered = zone.alert_on_enter
        elif ev.transition_type.value == "ZONE_EXIT":
            alert_triggered = zone.alert_on_exit
        elif ev.transition_type.value == "DWELL_EXCEEDED":
            alert_triggered = True
        else:
            alert_triggered = False
    else:
        alert_triggered = bool(ev.alert_triggered)

    db_event = GeofenceEvent(
        zone_id=ev.zone_id,
        zone_name=zone_name,
        zone_type=zone_type,
        office_id=office_id,
        camera_id=camera_id,
        transition_type=ev.transition_type.value,
        track_id=ev.track_id,
        person_label=person_label,
        identity_id=identity_id,
        identity_name=identity_name,
        dwell_seconds=ev.dwell_seconds,
        alert_triggered=alert_triggered,
        timestamp=ts,
        created_at=now_utc,
    )
    db.add(db_event)

    # Fan out alert if triggered
    if alert_triggered:
        action_verb = ev.transition_type.value.replace("ZONE_", "")
        detail_msg = (
            f"[WAMSS GEOFENCE] {zone_type.title()} Zone '{zone_name}' {action_verb} "
            f"by {person_label}"
        )
        if camera_id:
            detail_msg += f" ({camera_id})"

        alert_payload = {
            "zone_id": ev.zone_id,
            "zone_name": zone_name,
            "zone_type": zone_type,
            "transition_type": ev.transition_type.value,
            "track_id": ev.track_id,
            "person_label": person_label,
            "camera_id": camera_id,
            "office_id": office_id,
            "dwell_seconds": ev.dwell_seconds,
            "timestamp": ts.isoformat(),
            "detail": detail_msg,
        }
        try:
            dispatch_alert(f"GEOFENCE_{ev.transition_type.value}", alert_payload)
        except Exception as exc:
            logger.error("Alert dispatch failed: %s", exc)

    return db_event


@router.post("/events", status_code=status.HTTP_201_CREATED)
def ingest_geofence_events(
    events_in: Union[GeofenceEventCreate, List[GeofenceEventCreate]],
    db: Session = Depends(get_db),
):
    """
    Ingest single or batch geofence transition events emitted by the vision edge node.
    Enforces privacy mode and dispatches alerts on triggered transitions.
    """
    is_list = isinstance(events_in, list)
    items = events_in if is_list else [events_in]

    zones_cache: Dict[int, GeofenceZone] = {}
    created_events: List[GeofenceEvent] = []

    try:
        for item in items:
            db_ev = _process_single_event(item, db, zones_cache)
            created_events.append(db_ev)

        db.commit()
        for ev in created_events:
            db.refresh(ev)

        if is_list:
            return [GeofenceEventOut.model_validate(e) for e in created_events]
        return GeofenceEventOut.model_validate(created_events[0])
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Failed to persist geofence event(s): {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to store geofence events",
        )


@router.get("/events", response_model=List[GeofenceEventOut])
def list_geofence_events(
    office_id: Optional[str] = Query(None, description="Filter by office ID"),
    zone_id: Optional[int] = Query(None, description="Filter by zone ID"),
    camera_id: Optional[str] = Query(None, description="Filter by camera ID"),
    transition_type: Optional[str] = Query(None, description="Filter by transition type"),
    alert_triggered: Optional[bool] = Query(None, description="Filter by alert status"),
    limit: int = Query(50, ge=1, le=1000, description="Max events to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
):
    """Query recent geofence transition events with filtering."""
    try:
        query = db.query(GeofenceEvent)
        if office_id is not None:
            query = query.filter(GeofenceEvent.office_id == office_id)
        if zone_id is not None:
            query = query.filter(GeofenceEvent.zone_id == zone_id)
        if camera_id is not None:
            query = query.filter(GeofenceEvent.camera_id == camera_id)
        if transition_type is not None:
            query = query.filter(GeofenceEvent.transition_type == transition_type)
        if alert_triggered is not None:
            query = query.filter(GeofenceEvent.alert_triggered == alert_triggered)

        events = (
            query.order_by(GeofenceEvent.timestamp.desc(), GeofenceEvent.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return events
    except SQLAlchemyError as exc:
        logger.error(f"Failed to query geofence events: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query geofence events",
        )


@router.get("/occupancy", response_model=List[ZoneOccupancyOut])
def get_zone_occupancy(
    office_id: str = Query("hq", description="Office identifier"),
    camera_id: Optional[str] = Query(None, description="Filter by camera ID"),
    staleness_seconds: float = Query(300.0, ge=10.0, description="Max seconds since last activity"),
    db: Session = Depends(get_db),
):
    """
    Return live per-zone occupancy summary for active zones.
    Derives current count and occupant list from latest transition states.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=staleness_seconds)
    alerts_cutoff = now - timedelta(hours=24)

    try:
        zone_query = db.query(GeofenceZone).filter(
            GeofenceZone.office_id == office_id,
            GeofenceZone.is_active == True,  # noqa: E712
        )
        if camera_id is not None:
            zone_query = zone_query.filter(GeofenceZone.camera_id == camera_id)

        zones = zone_query.order_by(GeofenceZone.id.asc()).all()
        if not zones:
            return []

        zone_ids = [z.id for z in zones]

        # Query recent events for these zones
        recent_events = (
            db.query(GeofenceEvent)
            .filter(
                GeofenceEvent.zone_id.in_(zone_ids),
                GeofenceEvent.timestamp >= cutoff,
            )
            .order_by(GeofenceEvent.timestamp.asc(), GeofenceEvent.id.asc())
            .all()
        )

        # Count 24h alerts per zone
        alert_counts = (
            db.query(GeofenceEvent.zone_id, func.count(GeofenceEvent.id))
            .filter(
                GeofenceEvent.zone_id.in_(zone_ids),
                GeofenceEvent.alert_triggered == True,  # noqa: E712
                GeofenceEvent.timestamp >= alerts_cutoff,
            )
            .group_by(GeofenceEvent.zone_id)
            .all()
        )
        alert_map = {z_id: cnt for z_id, cnt in alert_counts}

        # Track state per (zone_id, track_id)
        # zone_id -> track_id -> latest event
        zone_track_map: Dict[int, Dict[int, GeofenceEvent]] = {z.id: {} for z in zones}
        for ev in recent_events:
            zone_track_map[ev.zone_id][ev.track_id] = ev

        result: List[ZoneOccupancyOut] = []
        for zone in zones:
            tracks_state = zone_track_map.get(zone.id, {})
            occupants: List[ZoneOccupant] = []
            for tid, latest_ev in tracks_state.items():
                if latest_ev.transition_type in ("ZONE_ENTER", "DWELL_EXCEEDED"):
                    entered_ts = latest_ev.timestamp
                    if entered_ts.tzinfo is None:
                        entered_ts = entered_ts.replace(tzinfo=timezone.utc)
                    current_dwell = max(0.0, (now - entered_ts).total_seconds())
                    occupants.append(
                        ZoneOccupant(
                            track_id=latest_ev.track_id,
                            person_label=latest_ev.person_label,
                            entered_at=entered_ts,
                            dwell_seconds=current_dwell,
                            identity_name=latest_ev.identity_name,
                        )
                    )

            occupants.sort(key=lambda o: o.entered_at)
            result.append(
                ZoneOccupancyOut(
                    zone_id=zone.id,
                    zone_name=zone.name,
                    zone_type=zone.zone_type,
                    color=zone.color,
                    current_count=len(occupants),
                    occupants=occupants,
                    recent_alerts_count=alert_map.get(zone.id, 0),
                )
            )

        return result
    except SQLAlchemyError as exc:
        logger.error(f"Failed to calculate zone occupancy: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to calculate zone occupancy",
        )


# ---------------------------------------------------------------------------
# Individual Zone Access / Updates / Deletion
# ---------------------------------------------------------------------------


@router.get("/{zone_id}", response_model=GeofenceZoneOut)
def get_zone(zone_id: int, db: Session = Depends(get_db)):
    """Fetch details of a single geofence zone by ID."""
    try:
        zone = db.query(GeofenceZone).filter(GeofenceZone.id == zone_id).first()
    except SQLAlchemyError as exc:
        logger.error(f"Database query error for zone {zone_id}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to fetch geofence zone",
        )

    if not zone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Geofence zone {zone_id} not found",
        )
    return zone


@router.patch("/{zone_id}", response_model=GeofenceZoneOut)
def update_zone(zone_id: int, zone_in: GeofenceZoneUpdate, db: Session = Depends(get_db)):
    """Partially update an existing geofence zone."""
    try:
        zone = db.query(GeofenceZone).filter(GeofenceZone.id == zone_id).first()
    except SQLAlchemyError as exc:
        logger.error(f"Database query error for zone {zone_id}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to update geofence zone",
        )

    if not zone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Geofence zone {zone_id} not found",
        )

    update_data = zone_in.model_dump(exclude_unset=True)
    if not update_data:
        return zone

    for key, val in update_data.items():
        if key == "zone_type" and val is not None:
            setattr(zone, key, val.value if hasattr(val, "value") else str(val))
        else:
            setattr(zone, key, val)

    zone.updated_at = datetime.now(timezone.utc)

    try:
        db.commit()
        db.refresh(zone)
        return zone
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Failed to update geofence zone {zone_id}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to update geofence zone",
        )


@router.delete("/{zone_id}")
def delete_zone(zone_id: int, db: Session = Depends(get_db)):
    """Delete a geofence zone by ID."""
    try:
        zone = db.query(GeofenceZone).filter(GeofenceZone.id == zone_id).first()
    except SQLAlchemyError as exc:
        logger.error(f"Database query error deleting zone {zone_id}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to delete geofence zone",
        )

    if not zone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Geofence zone {zone_id} not found",
        )

    try:
        db.delete(zone)
        db.commit()
        return {"id": zone_id, "status": "deleted"}
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Failed to delete geofence zone {zone_id}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to delete geofence zone",
        )
