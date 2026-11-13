"""Edge-computing expansion endpoints for the WAMSS backend.

Server side of the vision edge roadmap. Vision nodes POST detection events
to /event (with the new optional edge fields); this module adds:

  GET  /positions        live person positions (in-memory registry, 10s expiry)
  GET  /heatmap          stored position aggregates for heatmap rendering
  GET  /occupancy        time-bucketed distinct-person counts
  POST /faces/enroll     enroll a face (multipart photo, or JSON with snapshot_path)
  GET  /faces            list enrolled names
  GET  /faces/gallery    name + embedding list for edge-node sync
  GET  /faces/gallery/version
                         gallery version marker for edge-node sync polling
  DELETE /faces/{name}  remove a person from the gallery and the registry
  GET  /identities       persistent known-persons registry, most recently seen first
  GET  /unknowns         recent unknown sightings with snapshot links, for review
  GET  /snapshots/{f}    serve snapshot JPEGs for dashboard thumbnails

Gallery refresh after enroll/delete: the backend is the source of truth
for the gallery (embeddings live in the faces table, served by
GET /faces/gallery). Every enroll or delete bumps a version counter, kept
in data/faces/.gallery_version and exposed by GET /faces/gallery/version.
A vision node syncs by polling that version endpoint and, when the version
changes, re-fetching GET /faces/gallery and calling face_id.reload_gallery()
(the vision-side cache drop; face_id.py already provides it). The backend
never writes vision/faces/*.json itself. Enroll/delete responses also
carry the new gallery_version so a caller can confirm the bump.

Known-persons registry: table person_identities (see app.models).
PersonIdentity rows are created on enroll and bumped on every sighting of
a known person_name in POST /event (see record_identity_sighting, called
from app.main.receive_event). A person counts as known when they have a
gallery entry and/or a registry row; GET /identities merges in gallery
entries that predate the registry so older enrollments still show up.

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

import json
import logging
import io
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, JSON, String, func, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import database
from app.database import get_db
from app.models import Base, DetectionEvent, FaceRecord, PersonIdentity

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
    ("person_name_confidence", Float()),
    ("position_x", Float()),
    ("position_y", Float()),
    ("snapshot_path", String(256)),
    ("dwell_seconds", Float()),
]

# Columns of the person_identities registry table, for old databases where
# the table exists but predates a column (same additive pattern as above).
_IDENTITY_COLUMNS: List[Tuple[str, Any]] = [
    ("name", String(128)),
    ("enrolled_at", DateTime(timezone=True)),
    ("last_seen", DateTime(timezone=True)),
    ("sightings", Integer()),
    ("cameras", JSON()),
]


def _add_missing_columns(conn, table: str, columns: List[Tuple[str, Any]], dialect) -> None:
    cols = {c["name"] for c in inspect(conn).get_columns(table)}
    for name, typ in columns:
        if name in cols:
            continue
        col_type = typ.compile(dialect=dialect)
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))
        logger.info("added missing column %s.%s", table, name)


def ensure_schema() -> None:
    """Add any missing edge columns and the identities registry (additive).

    Idempotent and safe on old databases: creates the person_identities
    table when it does not exist yet, and adds any missing columns to
    detection_events and person_identities. Never drops or rewrites
    existing tables or data.
    """
    try:
        engine = database.engine
        # Create the registry table if this database predates it.
        Base.metadata.create_all(bind=engine, tables=[PersonIdentity.__table__])
        with engine.begin() as conn:
            _add_missing_columns(conn, "detection_events", _EDGE_COLUMNS, engine.dialect)
            _add_missing_columns(conn, "person_identities", _IDENTITY_COLUMNS, engine.dialect)
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
    person_name_confidence: Optional[float] = None,
) -> None:
    """Record a live position. No-op when coordinates are absent."""
    if x is None or y is None:
        return
    _position_registry[(camera_id, track_id)] = {
        "track_id": track_id,
        "person_name": person_name,
        "person_name_confidence": person_name_confidence,
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
            "person_name_confidence": entry.get("person_name_confidence"),
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
    person_name_confidence: Optional[float] = Field(
        default=None,
        description="Confidence of the person identification, or null when not reported",
    )
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

# ---------------------------------------------------------------- identities

# Gallery version marker: bumped on every enroll/delete so vision nodes can
# poll GET /faces/gallery/version and re-fetch the gallery only when it
# changes. File content is {"version": N, "updated_at": ISO timestamp}.
GALLERY_VERSION_FILE = FACES_DIR / ".gallery_version"


def _read_gallery_version() -> Dict[str, Any]:
    try:
        raw = GALLERY_VERSION_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return {"version": 0, "updated_at": None}
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("version"), int):
                return {"version": data["version"], "updated_at": data.get("updated_at")}
        except ValueError:
            pass
        return {"version": int(raw), "updated_at": None}
    except (OSError, ValueError):
        return {"version": 0, "updated_at": None}


def _bump_gallery_version() -> int:
    """Increment the gallery version marker. Never raises."""
    try:
        _ensure_data_dirs()
        version = _read_gallery_version()["version"] + 1
        GALLERY_VERSION_FILE.write_text(
            json.dumps(
                {
                    "version": version,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        return version
    except OSError as exc:
        logger.warning("gallery version bump failed: %s", type(exc).__name__)
        return _read_gallery_version()["version"]


def _is_known_name(name: Optional[str]) -> bool:
    """True for a real person name; "unknown", empty, and None are not known."""
    return bool(name) and bool(name.strip()) and name.strip().lower() != "unknown"


def _cameras_of(row: PersonIdentity) -> List[str]:
    cams = row.cameras
    if isinstance(cams, list):
        return [str(c) for c in cams if c]
    if isinstance(cams, str):
        return [c for c in cams.split(",") if c.strip()]
    return []


def ensure_identity_row(db: Session, name: str, enrolled_at: Optional[datetime] = None) -> PersonIdentity:
    """Return the registry row for name, creating it when missing.

    A new row starts with zero sightings and no last_seen; enrollment alone
    is not a sighting. Caller owns the commit.
    """
    row = db.query(PersonIdentity).filter(PersonIdentity.name == name).first()
    if row is None:
        row = PersonIdentity(
            name=name,
            enrolled_at=enrolled_at or datetime.now(timezone.utc),
            last_seen=None,
            sightings=0,
            cameras=[],
        )
        db.add(row)
    return row


def record_identity_sighting(
    db: Session,
    name: Optional[str],
    camera_id: Optional[str] = None,
    seen_at: Optional[datetime] = None,
) -> Optional[PersonIdentity]:
    """Bump the registry row for a known person seen in an event.

    Creates the row when the person is known to vision but has no registry
    row yet (for example enrolled directly in vision/faces/). Unknown,
    empty, and missing names are ignored. Never raises: a registry failure
    must not break event ingestion, so errors are logged and swallowed
    after a rollback.
    """
    if not _is_known_name(name):
        return None
    now = seen_at or datetime.now(timezone.utc)
    try:
        row = db.query(PersonIdentity).filter(PersonIdentity.name == name).first()
        if row is None:
            row = PersonIdentity(
                name=name,
                enrolled_at=now,
                last_seen=now,
                sightings=1,
                cameras=[camera_id] if camera_id else [],
            )
            db.add(row)
        else:
            row.last_seen = now
            row.sightings = (row.sightings or 0) + 1
            cams = _cameras_of(row)
            if camera_id and camera_id not in cams:
                cams.append(camera_id)
            row.cameras = cams
        db.commit()
        return row
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("identity sighting update failed: %s", type(exc).__name__)
        return None


class IdentityOut(BaseModel):
    name: str
    enrolled_at: datetime
    last_seen: Optional[datetime] = None
    sightings: int = 0
    cameras: List[str] = Field(default_factory=list, description="Camera ids the person has been seen on")


class GalleryVersionOut(BaseModel):
    version: int
    updated_at: Optional[str] = None
    count: int = Field(description="Number of enrolled faces in the gallery")


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


def _read_snapshot_bytes(snapshot_path: str) -> bytes:
    """Read a snapshot by its event-style path, e.g. "snapshots/foo.jpg".

    Accepts "snapshots/foo.jpg", "data/snapshots/foo.jpg", a bare filename,
    or an absolute path inside the snapshots directory. Rejects traversal.
    Raises HTTPException 404 when the file does not exist, 400 when the
    path is not a plain snapshot filename.
    """
    raw = snapshot_path.strip()
    for prefix in ("data/snapshots/", "snapshots/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    if "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="snapshot_path must be a plain snapshot filename",
        )
    if not _SAFE_FILENAME.match(raw):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid snapshot filename",
        )
    path = SNAPSHOTS_DIR / raw
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot {raw} not found",
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        logger.error("failed to read snapshot: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not read the snapshot",
        )


def _store_enrollment(db: Session, name: str, image_bytes: bytes) -> Dict[str, Any]:
    """Store one enrollment: photo file, FaceRecord, and registry row.

    Shared by the upload and snapshot-path enroll paths. Returns the
    response body. Raises HTTPException on storage or database failure.
    """
    safe_name = _sanitize_name(name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_name}_{stamp}.jpg"

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
        ensure_identity_row(db, name)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("face enrollment db error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to enroll face",
        )

    gallery_version = _bump_gallery_version()
    return {
        "name": name,
        "status": "enrolled" if face_available else "unavailable",
        "gallery_version": gallery_version,
    }


@router.post("/faces/enroll")
async def enroll_face(request: Request, db: Session = Depends(get_db)):
    """Enroll a face for person identification, two ways.

    Multipart (as before): form fields ``name`` plus an uploaded ``photo``.
    JSON (for enrolling from the unknown-review queue): body
    ``{"name": "...", "snapshot_path": "snapshots/foo.jpg"}`` naming a
    snapshot already stored under data/snapshots/.

    The photo is stored under data/faces/ and the embedding in the faces
    table, and a registry row is created in person_identities. When no face
    library is available the photo is still stored and a placeholder (null
    embedding, face_available=False) is kept with a clear "unavailable"
    status; the request never fails with a 500 for this.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request body must be valid JSON",
            )
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Request body must be a JSON object",
            )
        name = payload.get("name")
        snapshot_path = payload.get("snapshot_path")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="name is required",
            )
        if not isinstance(snapshot_path, str) or not snapshot_path.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="snapshot_path is required",
            )
        image_bytes = _read_snapshot_bytes(snapshot_path)
        if not image_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Snapshot file is empty",
            )
        return _store_enrollment(db, name, image_bytes)

    try:
        form = await request.form()
    except Exception as exc:
        logger.error("failed to parse enroll form: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not parse the enrollment form",
        )
    name = form.get("name")
    photo = form.get("photo")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name is required",
        )
    if photo is None or not hasattr(photo, "read"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="photo is required",
        )
    try:
        image_bytes = await photo.read()
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
    return _store_enrollment(db, name, image_bytes)


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


@router.get("/faces/gallery/version", response_model=GalleryVersionOut)
def get_face_gallery_version(db: Session = Depends(get_db)):
    """Gallery version marker for edge-node sync polling.

    Vision nodes poll this cheap endpoint and re-fetch GET /faces/gallery
    only when ``version`` changes (it bumps on every enroll and delete).
    """
    try:
        count = db.query(FaceRecord).count()
    except SQLAlchemyError as exc:
        logger.error("face gallery version query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to fetch gallery version",
        )
    marker = _read_gallery_version()
    return GalleryVersionOut(
        version=marker["version"], updated_at=marker["updated_at"], count=count
    )


@router.delete("/faces/{name}")
def delete_face(name: str, db: Session = Depends(get_db)):
    """Remove a person from the gallery and the registry.

    Deletes the FaceRecord and the PersonIdentity row, plus the stored
    face photo under data/faces/ when it belongs to this enrollment.
    Vision-side gallery JSON files (vision/faces/) are owned by the vision
    node, not this backend, so they are left alone; the node drops the
    person on its next gallery sync (see GET /faces/gallery/version).
    Returns 404 when the name is in neither the gallery nor the registry.
    """
    try:
        record = db.query(FaceRecord).filter(FaceRecord.name == name).first()
        identity = db.query(PersonIdentity).filter(PersonIdentity.name == name).first()
    except SQLAlchemyError as exc:
        logger.error("face delete query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to delete face",
        )
    if record is None and identity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No enrolled face or identity named {name}",
        )
    photo_path = record.photo_path if record is not None else None
    try:
        if record is not None:
            db.delete(record)
        if identity is not None:
            db.delete(identity)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("face delete db error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to delete face",
        )
    if photo_path:
        try:
            full = (BACKEND_ROOT / photo_path).resolve()
            if full.is_relative_to(FACES_DIR.resolve()) and full.is_file():
                full.unlink()
        except OSError as exc:
            logger.warning("enrolled photo cleanup failed: %s", type(exc).__name__)
    gallery_version = _bump_gallery_version()
    return {"name": name, "status": "deleted", "gallery_version": gallery_version}


@router.get("/identities", response_model=List[IdentityOut])
def list_identities(db: Session = Depends(get_db)):
    """List the known-persons registry, most recently seen first.

    People never seen since enrollment (last_seen null) sort last.
    Gallery entries that predate the registry are merged in with zero
    sightings so older enrollments still show up as known.
    """
    try:
        rows = (
            db.query(PersonIdentity)
            .order_by(
                PersonIdentity.last_seen.desc().nulls_last(),
                PersonIdentity.enrolled_at.desc(),
            )
            .all()
        )
        gallery_names = {r.name for r in db.query(FaceRecord.name).all()}
    except SQLAlchemyError as exc:
        logger.error("identities query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to list identities",
        )
    result = [
        IdentityOut(
            name=r.name,
            enrolled_at=r.enrolled_at,
            last_seen=r.last_seen,
            sightings=r.sightings or 0,
            cameras=_cameras_of(r),
        )
        for r in rows
    ]
    known = {r.name for r in rows}
    missing = sorted(gallery_names - known)
    if missing:
        try:
            legacy = (
                db.query(FaceRecord)
                .filter(FaceRecord.name.in_(missing))
                .order_by(FaceRecord.enrolled_at.desc())
                .all()
            )
        except SQLAlchemyError:
            legacy = []
        for r in legacy:
            result.append(
                IdentityOut(
                    name=r.name,
                    enrolled_at=r.enrolled_at,
                    last_seen=None,
                    sightings=0,
                    cameras=[],
                )
            )
    return result


class UnknownSighting(BaseModel):
    event_id: int
    camera_id: str
    track_id: str
    timestamp: datetime
    person_name: Optional[str] = None
    confidence: float = Field(description="Detection confidence of the event")
    snapshot_path: str
    snapshot_url: str = Field(description="Dashboard link, /snapshots/{file}")


MAX_UNKNOWN_ROWS_SCANNED = 500


@router.get("/unknowns", response_model=List[UnknownSighting])
def list_unknowns(
    limit: int = Query(50, ge=1, le=200, description="Maximum sightings to return"),
    camera_id: Optional[str] = Query(None, description="Filter to one camera"),
    db: Session = Depends(get_db),
):
    """Recent unknown sightings for review and enrollment.

    Source of truth is detection events whose person_name is "unknown"
    (or missing) and that carry a snapshot. Most recent first, deduplicated
    to one entry per (camera_id, track_id): the most recent sighting of
    each track, scanned over the 500 most recent unknown events. Each
    entry links its snapshot via /snapshots/{file} for the dashboard, and
    can be enrolled with POST /faces/enroll {"name", "snapshot_path"}.
    """
    try:
        query = db.query(DetectionEvent).filter(
            DetectionEvent.snapshot_path.is_not(None),
            DetectionEvent.snapshot_path != "",
            (
                DetectionEvent.person_name.is_(None)
                | (DetectionEvent.person_name == "")
                | (func.lower(DetectionEvent.person_name) == "unknown")
            ),
        )
        if camera_id is not None:
            query = query.filter(DetectionEvent.camera_id == camera_id)
        rows = (
            query.order_by(DetectionEvent.timestamp.desc(), DetectionEvent.id.desc())
            .limit(MAX_UNKNOWN_ROWS_SCANNED)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.error("unknowns query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable; unable to list unknown sightings",
        )
    seen_tracks = set()
    result = []
    for row in rows:
        key = (row.camera_id, row.track_id)
        if key in seen_tracks:
            continue
        seen_tracks.add(key)
        filename = Path(row.snapshot_path).name
        result.append(
            UnknownSighting(
                event_id=row.id,
                camera_id=row.camera_id,
                track_id=row.track_id,
                timestamp=row.timestamp,
                person_name=row.person_name,
                confidence=row.confidence,
                snapshot_path=row.snapshot_path,
                snapshot_url=f"/snapshots/{filename}",
            )
        )
        if len(result) >= limit:
            break
    return result

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
