"""Edge-computing expansion endpoints for the WAMSS backend.

Server side of the vision edge roadmap. Vision nodes POST detection events
to /event (with the new optional edge fields); this module adds:

  GET  /positions        live person positions (in-memory registry, 10s expiry)
  GET  /heatmap          stored position aggregates for heatmap rendering
  GET  /occupancy        time-bucketed distinct-person counts
  POST /faces/enroll     enroll a face (photo + name, multipart)
  GET  /faces            list enrolled names
  GET  /faces/gallery    name + embedding list for edge-node sync
  GET  /snapshots/{f}    serve snapshot JPEGs for dashboard thumbnails

Wiring (applied in app.main, not here):
    from app.edge import router as edge_router
    app.include_router(edge_router)

Auth: the APIKeyAuthMiddleware in app.auth covers every route (except the
health/docs exemptions), so these endpoints inherit the same X-API-Key
behavior as the existing dashboard GETs. Nothing extra is needed here.

Backward compatibility: the new DetectionEvent columns are all nullable and
added additively. ensure_schema() adds any missing columns to existing
databases at startup (this project has no Alembic migrations; tables are
created with Base.metadata.create_all).
"""

import logging
import io
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Float, String, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import database
from app.database import get_db
from app.models import DetectionEvent, FaceRecord

logger = logging.getLogger("wamss.api.edge")

router = APIRouter(tags=["edge"])

# ---------------------------------------------------------------- paths

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
FACES_DIR = DATA_DIR / "faces"


def _ensure_data_dirs() -> None:
    for d in (SNAPSHOTS_DIR, FACES_DIR):
        d.mkdir(parents=True, exist_ok=True)


_ensure_data_dirs()

# ---------------------------------------------------------------- schema

# Additive DDL for existing deployments: create_all() only creates missing
# tables, never missing columns. This keeps old databases working after the
# edge columns were added to the model.
_EDGE_COLUMNS: List[Tuple[str, Any]] = [
    ("person_name", String(128)),
    ("position_x", Float()),
    ("position_y", Float()),
    ("snapshot_path", String(256)),
    ("dwell_seconds", Float()),
]


def ensure_schema() -> None:
    """Add any missing edge columns to detection_events (additive, idempotent)."""
    try:
        engine = database.engine
        with engine.begin() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("detection_events")}
            missing = [(name, typ) for name, typ in _EDGE_COLUMNS if name not in cols]
            if not missing:
                return
            dialect = engine.dialect
            for name, typ in missing:
                col_type = typ.compile(dialect=dialect)
                conn.execute(
                    text(f"ALTER TABLE detection_events ADD COLUMN {name} {col_type}")
                )
                logger.info("added missing column detection_events.%s", name)
    except Exception as exc:
        # Never break startup or imports: a missing column will surface as a
        # normal SQL error on the specific endpoint instead.
        logger.warning("ensure_schema skipped: %s", type(exc).__name__)


# Run once at import so test suites that bypass the lifespan still get the
# additive columns on stale databases. Wrapped in ensure_schema already.
try:
    ensure_schema()
except Exception:  # pragma: no cover - ensure_schema guards internally
    pass

# ---------------------------------------------------------------- positions

# In-memory registry: (camera_id, track_id) -> entry dict.
# Updated on every POST /event carrying position_x/position_y (done in
# app.main.receive_event via record_position).
POSITION_TTL_SECONDS = 10.0

_position_registry: Dict[Tuple[str, str], Dict[str, Any]] = {}


def record_position(
    camera_id: str,
    track_id: str,
    person_name: Optional[str] = None,
    x: Optional[float] = None,
    y: Optional[float] = None,
) -> None:
    """Record a live position. No-op when coordinates are absent."""
    if x is None or y is None:
        return
    _position_registry[(camera_id, track_id)] = {
        "track_id": track_id,
        "person_name": person_name,
        "x": x,
        "y": y,
        "camera_id": camera_id,
        "last_seen": datetime.now(timezone.utc),
        "last_seen_monotonic": time.monotonic(),
    }


def _live_positions() -> List[Dict[str, Any]]:
    now = time.monotonic()
    expired = [
        key
        for key, entry in _position_registry.items()
        if now - entry["last_seen_monotonic"] > POSITION_TTL_SECONDS
    ]
    for key in expired:
        del _position_registry[key]
    return [
        {
            "track_id": entry["track_id"],
            "person_name": entry["person_name"],
            "x": entry["x"],
            "y": entry["y"],
            "camera_id": entry["camera_id"],
            "last_seen": entry["last_seen"].isoformat(),
        }
        for entry in _position_registry.values()
    ]


class PositionOut(BaseModel):
    track_id: str
    person_name: Optional[str] = None
    x: float
    y: float
    camera_id: str
    last_seen: str = Field(description="ISO 8601 timestamp of the last update")


@router.get("/positions", response_model=List[PositionOut])
def get_positions():
    """Live person positions from the in-memory registry.

    Entries expire if not refreshed within 10 seconds, so the list only
    contains people currently seen by an edge node.
    """
    return _live_positions()

# ---------------------------------------------------------------- heatmap


class HeatPoint(BaseModel):
    x: float
    y: float
    weight: int = 1


MAX_HEATMAP_POINTS = 2000


@router.get("/heatmap", response_model=List[HeatPoint])
def get_heatmap(
    camera_id: Optional[str] = Query(None, description="Filter to one camera; all cameras when omitted"),
    hours: int = Query(24, ge=1, le=24 * 30, description="Lookback window in hours"),
    db: Session = Depends(get_db),
):
    """Aggregate stored (position_x, position_y) points for heatmap rendering.

    Returns raw points with weight 1, newest first, capped at 2000 points.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        query = db.query(
            DetectionEvent.position_x, DetectionEvent.position_y
        ).filter(
            DetectionEvent.timestamp >= since,
            DetectionEvent.position_x.is_not(None),
            DetectionEvent.position_y.is_not(None),
        )
        if camera_id is not None:
            query = query.filter(DetectionEvent.camera_id == camera_id)
        rows = (
            query.order_by(DetectionEvent.id.desc())
            .limit(MAX_HEATMAP_POINTS)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("heatmap query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to build heatmap",
        )
    return [HeatPoint(x=x, y=y, weight=1) for x, y in rows]

# ---------------------------------------------------------------- occupancy


class OccupancyBucket(BaseModel):
    t: str = Field(description="ISO 8601 timestamp of the bucket start")
    count: int = Field(description="Distinct track_ids seen in this bucket")
    camera_id: Optional[str] = Field(
        default=None, description="Camera for per-camera rows; null for the total"
    )


BUCKET_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


@router.get("/occupancy", response_model=List[OccupancyBucket])
def get_occupancy(
    hours: int = Query(24, ge=1, le=24 * 30, description="Lookback window in hours"),
    bucket: str = Query("5m", description="Bucket size: 1m, 5m, 15m, or 1h"),
    camera_id: Optional[str] = Query(None, description="Filter to one camera; total across cameras when omitted"),
    db: Session = Depends(get_db),
):
    """Time-bucketed person counts.

    Counts distinct track_ids per bucket per camera. When camera_id is
    omitted the counts are totals across all cameras (camera_id is null).
    """
    if bucket not in BUCKET_MINUTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bucket must be one of: 1m, 5m, 15m, 1h",
        )
    bucket_seconds = BUCKET_MINUTES[bucket] * 60
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        query = db.query(
            DetectionEvent.timestamp, DetectionEvent.track_id, DetectionEvent.camera_id
        ).filter(DetectionEvent.timestamp >= since)
        if camera_id is not None:
            query = query.filter(DetectionEvent.camera_id == camera_id)
        rows = query.order_by(DetectionEvent.timestamp.asc()).all()
    except SQLAlchemyError as exc:
        logger.error("occupancy query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to compute occupancy",
        )

    # Bucketize in Python so timezone-naive SQLite timestamps and aware
    # PostgreSQL timestamps are handled the same way. With a camera filter
    # the rows form one series; without it all cameras merge into a total
    # (camera_id null in the output).
    buckets: Dict[Tuple[int, Optional[str]], set] = {}
    for ts, track_id, cam in rows:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        epoch = int(ts.timestamp())
        bucket_start = (epoch // bucket_seconds) * bucket_seconds
        key = (bucket_start, cam if camera_id is not None else None)
        buckets.setdefault(key, set()).add(track_id)

    result = [
        OccupancyBucket(
            t=datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
            count=len(tracks),
            camera_id=cam,
        )
        for (start, cam), tracks in buckets.items()
    ]
    result.sort(key=lambda b: b.t)
    return result

# ---------------------------------------------------------------- faces


class FaceInfo(BaseModel):
    name: str
    enrolled_at: datetime


class FaceGalleryItem(BaseModel):
    name: str
    embedding: Optional[List[float]] = Field(
        default=None,
        description="Face encoding as a float list, or null when no face library was available",
    )


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", name.strip()).strip("._")
    return cleaned or "person"


def _encode_face(image_bytes: bytes) -> Tuple[Optional[List[float]], bool]:
    """Try to compute a face embedding.

    Returns (embedding_or_None, face_available). Never raises: any failure
    (library missing, no face found, bad image) yields the unavailable flag
    so enrollment never returns a 500.
    """
    try:
        import face_recognition  # type: ignore
    except ImportError:
        return None, False
    try:
        import numpy as np

        image = face_recognition.load_image_file(io.BytesIO(image_bytes))
        encodings = face_recognition.face_encodings(image)
        if not encodings:
            return None, False
        return [float(v) for v in np.asarray(encodings[0]).ravel().tolist()], True
    except Exception as exc:
        logger.warning("face encoding failed: %s", type(exc).__name__)
        return None, False


@router.post("/faces/enroll")
def enroll_face(
    name: str = Form(..., min_length=1, description="Person name for this face"),
    photo: UploadFile = File(..., description="Face photo"),
    db: Session = Depends(get_db),
):
    """Enroll a face for person identification.

    The photo is stored under data/faces/ and the embedding in the faces
    table. When no face library is available the photo is still stored and a
    placeholder (null embedding, face_available=False) is kept with a clear
    "unavailable" status; the request never fails with a 500 for this.
    """
    safe_name = _sanitize_name(name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_name}_{stamp}.jpg"

    try:
        image_bytes = photo.file.read()
    except Exception as exc:
        logger.error("failed to read uploaded photo: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the uploaded photo",
        )
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded photo is empty",
        )

    photo_path = FACES_DIR / filename
    try:
        with open(photo_path, "wb") as fh:
            fh.write(image_bytes)
    except OSError as exc:
        logger.error("failed to store face photo: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not store the face photo",
        )

    embedding, face_available = _encode_face(image_bytes)

    try:
        existing = db.query(FaceRecord).filter(FaceRecord.name == name).first()
        if existing is not None:
            existing.embedding = embedding
            existing.photo_path = str(photo_path.relative_to(BACKEND_ROOT))
            existing.face_available = face_available
            existing.enrolled_at = datetime.now(timezone.utc)
        else:
            db.add(
                FaceRecord(
                    name=name,
                    embedding=embedding,
                    photo_path=str(photo_path.relative_to(BACKEND_ROOT)),
                    face_available=face_available,
                    enrolled_at=datetime.now(timezone.utc),
                )
            )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("face enrollment db error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to enroll face",
        )

    return {"name": name, "status": "enrolled" if face_available else "unavailable"}


@router.get("/faces", response_model=List[FaceInfo])
def list_faces(db: Session = Depends(get_db)):
    """List enrolled faces (name + enrollment time)."""
    try:
        records = db.query(FaceRecord).order_by(FaceRecord.name.asc()).all()
    except SQLAlchemyError as exc:
        logger.error("faces list query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to list faces",
        )
    return [FaceInfo(name=r.name, enrolled_at=r.enrolled_at) for r in records]


@router.get("/faces/gallery", response_model=List[FaceGalleryItem])
def get_face_gallery(db: Session = Depends(get_db)):
    """Return name + embedding pairs so edge nodes can sync the gallery."""
    try:
        records = db.query(FaceRecord).order_by(FaceRecord.name.asc()).all()
    except SQLAlchemyError as exc:
        logger.error("face gallery query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to fetch face gallery",
        )
    return [FaceGalleryItem(name=r.name, embedding=r.embedding) for r in records]

# ---------------------------------------------------------------- snapshots

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@router.get("/snapshots/{filename}")
def get_snapshot(filename: str):
    """Serve a snapshot JPEG from data/snapshots/ for dashboard thumbnails.

    The filename is strictly validated (no directories, no traversal) before
    touching the filesystem.
    """
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid snapshot filename",
        )
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid snapshot filename",
        )
    path = SNAPSHOTS_DIR / filename
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot {filename} not found",
        )
    return FileResponse(path, media_type="image/jpeg")
