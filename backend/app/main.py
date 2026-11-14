import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import get_db, init_db
from app.auth import APIKeyAuthMiddleware
from app.camera_consent import camera_mode, consented_employee_id_for_name
from app.models import AggregateOccupancyEvent, DetectionEvent
from app.rules import RuleEngine
from app.schemas import AggregateOccupancyCreate, EventCreate, EventDetail, EventResponse

# Workstation activity / presence / focus / security / policy routers.
# The model imports must run before init_db() so Base.metadata.create_all
# creates the new tables.
from app import activity_models  # noqa: F401 - registers ActivityLog
from app import security_models  # noqa: F401 - registers SecurityEvent
from app import wellness_models  # noqa: F401 - registers private wellness tables
from app.activity import ensure_activity_schema, router as activity_router
from app.attendance import router as attendance_router
from app.edge import (
    ensure_schema, process_geofence_position, record_identity_sighting, record_position,
)
from app.edge import router as edge_router
from app.focus import router as focus_router
from app.presence import router as presence_router
from app.security import router as security_router
from app.policy import router as policy_router
from app.exports import router as exports_router
from app.analytics import router as analytics_router
from app.hours import router as hours_router
from app.wellness import (
    router as wellness_router, start_live_sample_expirer, stop_live_sample_expirer,
)

logger = logging.getLogger("wamss.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class AccessLogAndErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"{request.method} {path} - {response.status_code} ({duration_ms:.2f}ms) [{client_ip}]")

            if path == "/health/db" and response.status_code >= 500:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"status": "error", "database": "unavailable"},
                )
            return response
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"{request.method} {path} failed ({duration_ms:.2f}ms): {type(exc).__name__}")
            if path == "/health/db":
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"status": "error", "database": "unavailable"},
                )
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on application startup
    init_db()
    ensure_activity_schema()
    # Additive edge columns for databases created before the edge expansion
    ensure_schema()
    start_live_sample_expirer()
    try:
        yield
    finally:
        stop_live_sample_expirer()


app = FastAPI(
    title="WAMSS Backend API",
    description="Workplace Activity Monitoring & Surveillance System event ingestion and query API",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(AccessLogAndErrorHandlerMiddleware)
# API key auth: enabled only when WAMSS_API_KEY is set. Registered after the
# access-log/error middleware so auth rejections go through the same pipeline.
app.add_middleware(APIKeyAuthMiddleware)
# CORS: the dashboard is served from a different origin (its own port or host)
# than the API, so browsers need explicit permission. Origins are configurable
# via WAMSS_CORS_ORIGINS (comma-separated); default allows all for local dev.
# Registered last so it runs outermost and answers preflight requests before
# the auth middleware sees them.
_cors_origins = [o.strip() for o in os.environ.get("WAMSS_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(activity_router)
app.include_router(attendance_router)
app.include_router(edge_router)
app.include_router(focus_router)
app.include_router(presence_router)
app.include_router(security_router)
app.include_router(policy_router)
app.include_router(exports_router)
app.include_router(analytics_router)
app.include_router(hours_router)
app.include_router(wellness_router)


@app.get("/health")
def get_health():
    """Simple service health check endpoint."""
    return {
        "status": "online",
        "service": "WAMSS backend",
        "environment": settings.environment,
    }


@app.get("/health/db")
def get_health_db(db: Session = Depends(get_db)):
    """Database connectivity health check endpoint."""
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database": "connected",
        }
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "error",
                "database": "unavailable",
            },
        )


@app.post("/event/aggregate", status_code=status.HTTP_201_CREATED)
def receive_aggregate_event(event_in: AggregateOccupancyCreate, db: Session = Depends(get_db)):
    """Persist only camera-level occupancy counts in a separate aggregate table."""
    if camera_mode(event_in.camera_id) != "aggregate":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Aggregate events are accepted only for aggregate-mode cameras",
        )
    if (event_in.occupancy == "occupied") != (event_in.person_count > 0):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Aggregate occupancy must agree with the reported count",
        )
    db_event = AggregateOccupancyEvent(
        camera_id=event_in.camera_id.strip(),
        occupancy=event_in.occupancy,
        person_count=event_in.person_count,
        timestamp=datetime.now(timezone.utc),
    )
    try:
        db.add(db_event)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to persist aggregate occupancy event: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to store aggregate occupancy event",
        )
    return {"received": True, "camera_id": db_event.camera_id}


@app.post("/event", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def receive_event(event_in: EventCreate, db: Session = Depends(get_db)):
    """
    Ingests person detection events from the vision pipeline,
    evaluates configurable decision rules, and persists event data.
    """
    # The backend is authoritative for a camera's privacy mode. A missing or
    # malformed mode configuration is aggregate-only and cannot accept
    # person-level events. Do not trust a client-supplied privacy flag alone.
    if event_in.privacy_mode or camera_mode(event_in.camera_id) != "identity":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This camera accepts aggregate-only events; person events are disabled",
        )

    # Auth decision (documented, 2026-11-15): identity is stored only for a
    # name that maps to exactly one employee with active consent for this
    # camera. Anything else is stored downgraded as an anonymous detection:
    # the name, the identification confidence, and the snapshot reference
    # are dropped, but the detection itself (zone, objects, position,
    # alerts) is still recorded so the vision pipeline keeps working for
    # unrecognized people. Rejecting the event would lose the detection
    # entirely and would make the anonymous feed unobservable. The
    # downgraded event carries no employee_id, so it never enters the
    # identity registry or geofence tracking.
    person_name = event_in.person_name
    employee_id = None
    if (
        isinstance(person_name, str)
        and person_name.strip()
        and person_name.strip().casefold() != "unknown"
    ):
        employee_id = consented_employee_id_for_name(
            person_name, event_in.camera_id
        )
    if employee_id is None:
        person_name = None
        person_name_confidence = None
        snapshot_path = None
    else:
        person_name = person_name.strip()
        person_name_confidence = event_in.person_name_confidence
        snapshot_path = event_in.snapshot_path

    # Evaluate configured decision rules
    rule_result = RuleEngine.evaluate(zone=event_in.zone, objects=event_in.objects)

    now_utc = datetime.now(timezone.utc)

    db_event = DetectionEvent(
        camera_id=event_in.camera_id,
        track_id=event_in.track_id,
        objects=event_in.objects,
        confidence=event_in.confidence,
        zone=event_in.zone,
        decision=rule_result.decision,
        alerts=rule_result.alerts,
        timestamp=now_utc,
        person_name=person_name,
        employee_id=employee_id,
        person_name_confidence=person_name_confidence,
        position_x=event_in.position_x,
        position_y=event_in.position_y,
        position_bounds=event_in.position_bounds,
        snapshot_path=snapshot_path,
        dwell_seconds=event_in.dwell_seconds,
    )

    try:
        db.add(db_event)
        db.commit()
        db.refresh(db_event)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error(f"Failed to persist detection event: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to store detection event",
        )

    # Update the live position registry for events that carry coordinates.
    # Kept outside the DB transaction: registry state must not block ingestion.
    try:
        record_position(
            camera_id=db_event.camera_id,
            track_id=db_event.track_id,
            person_name=db_event.person_name,
            x=db_event.position_x,
            y=db_event.position_y,
            person_name_confidence=db_event.person_name_confidence,
            position_bounds=event_in.position_bounds,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Position registry update failed: {type(exc).__name__}")

    # Bump the known-persons registry for sightings of a known person.
    # Cheap upsert, kept outside the event transaction: a registry failure
    # must never break ingestion (record_identity_sighting swallows DB
    # errors after a rollback).
    try:
        record_identity_sighting(
            db, db_event.person_name, camera_id=db_event.camera_id
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Identity registry update failed: {type(exc).__name__}")

    # Geofence evaluation requires per-person coordinates and an ephemeral
    # tracker key. Aggregate-only privacy events carry neither, so they are
    # naturally excluded without weakening the vision privacy path.
    try:
        process_geofence_position(
            db,
            camera_id=db_event.camera_id,
            track_id=db_event.track_id,
            person_name=db_event.person_name,
            employee_id=db_event.employee_id,
            x=db_event.position_x,
            y=db_event.position_y,
            position_bounds=event_in.position_bounds,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Geofence processing failed: {type(exc).__name__}")

    return EventResponse(
        received=True,
        camera_id=db_event.camera_id,
        alerts=rule_result.alerts,
        decision=rule_result.decision,
    )


@app.get("/events", response_model=List[EventDetail])
def list_events(
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of events to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    camera_id: Optional[str] = Query(None, description="Filter by camera ID"),
    start_time: Optional[datetime] = Query(None, description="Filter events after or at this UTC datetime"),
    end_time: Optional[datetime] = Query(None, description="Filter events before or at this UTC datetime"),
    db: Session = Depends(get_db),
):
    """Return detection events in reverse chronological order.

    Auth decision (documented, 2026-11-15): the event feed is an
    operational view gated by the deployment API key when one is configured,
    not by an employee token. Employee scoping stays on the personal routes
    (activity, presence, attendance, analytics, focus, hours, policy,
    security, wellness, geofence history, and the activity/focus CSV
    exports). Legacy rows with NULL employee_id stay visible here and in
    the events CSV: they are anonymous detections from before employee
    attribution existed, and downgraded events are anonymous by design, so
    scoping this feed to one employee would hide exactly the rows the
    consent downgrade is meant to make observable, and no employee token
    could ever reveal them.
    """
    try:
        query = db.query(DetectionEvent)

        if camera_id is not None:
            query = query.filter(DetectionEvent.camera_id == camera_id)
        if start_time is not None:
            query = query.filter(DetectionEvent.timestamp >= start_time)
        if end_time is not None:
            query = query.filter(DetectionEvent.timestamp <= end_time)

        events = (
            query.order_by(DetectionEvent.timestamp.desc(), DetectionEvent.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return events
    except SQLAlchemyError as exc:
        logger.error(f"Failed to query events: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to query detection events",
        )


@app.get("/events/{event_id}", response_model=EventDetail)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve one detection event by ID. Same auth as GET /events: the
    deployment API key when configured, not an employee token."""
    try:
        event = db.query(DetectionEvent).filter(
            DetectionEvent.id == event_id,
        ).first()
    except SQLAlchemyError as exc:
        logger.error(f"Database query error for event {event_id}: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to fetch detection event",
        )

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found",
        )
    return event
