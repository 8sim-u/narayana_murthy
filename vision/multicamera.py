"""Multi-camera person detection for the WAMSS edge node (Person A).

Configuration is driven entirely by environment variables (see
vision/.env.example). Nothing in this file contains a real camera IP or a
real backend address: defaults are placeholders that point at RFC 5737
documentation addresses, so running without config is safe and inert.
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

import requests
import cv2
from ultralytics import YOLO

try:
    # MediaPipe is OPTIONAL. It powers privacy-mode presence/pose (on-device)
    # and face blurring for anonymized snapshots. When absent, privacy mode
    # falls back to the YOLO aggregate path and refuses to store any frame.
    # The non-privacy path never needs it.
    import mediapipe as mp
except ImportError:
    mp = None

try:
    from vision.geofence_evaluator import (
        GeofenceEvaluator,
        ZoneConfig,
        extract_floor_anchor,
    )
except ImportError:
    try:
        from geofence_evaluator import (  # type: ignore
            GeofenceEvaluator,
            ZoneConfig,
            extract_floor_anchor,
        )
    except ImportError:
        GeofenceEvaluator = None
        ZoneConfig = None
        extract_floor_anchor = None



def _parse_bool(value, default):
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_float(value, default):
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        print(f"[config] WARNING: invalid float '{value}', using {default}")
        return default


def _load_cameras():
    """Resolve the camera map, highest priority source first.

    0. CAMERA_STREAM_URL: single-camera shorthand for edge nodes, e.g.
       CAMERA_STREAM_URL="1" with CAMERA_ID="CAM_01" (defaults to CAM_01).
       A numeric string means a local webcam device index; anything else
       is treated as a stream URL (RTSP/MJPEG).
    1. WAMSS_CAMERAS: a JSON object mapping camera id to stream URL, e.g.
       WAMSS_CAMERAS='{"CAM_01":"http://10.0.0.5:8080/video"}'
    2. Numbered vars: WAMSS_CAM_<id>_URL, e.g. WAMSS_CAM_01_URL=...
    3. SAMPLE_CAMERAS: placeholder URLs only (never real cameras).
    """
    single = os.environ.get("CAMERA_STREAM_URL", "").strip()
    if single:
        cam_id = os.environ.get("CAMERA_ID", "CAM_01").strip() or "CAM_01"
        print(f"[config] Camera from CAMERA_STREAM_URL: {cam_id}")
        return _normalize_cameras({cam_id: single})

    raw = os.environ.get("WAMSS_CAMERAS")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                print(f"[config] Cameras from WAMSS_CAMERAS: {sorted(data)}")
                return _normalize_cameras(dict(data))
            print("[config] WARNING: WAMSS_CAMERAS is empty or not an "
                  "object, falling back to numbered vars")
        except json.JSONDecodeError as exc:
            print(f"[config] WARNING: WAMSS_CAMERAS is not valid JSON "
                  f"({exc}), falling back to numbered vars")

    numbered = {}
    for key, value in sorted(os.environ.items()):
        match = re.fullmatch(r"WAMSS_CAM_([A-Z0-9]+)_URL", key)
        if match and value.strip():
            numbered[f"CAM_{match.group(1)}"] = value.strip()
    if numbered:
        print(f"[config] Cameras from numbered vars: {sorted(numbered)}")
        return _normalize_cameras(numbered)

    print("[config] WARNING: no cameras configured; using SAMPLE-ONLY "
          "placeholder URLs (192.0.2.x documentation addresses). Set "
          "CAMERA_STREAM_URL, WAMSS_CAMERAS or WAMSS_CAM_<id>_URL to run for real.")
    return dict(SAMPLE_CAMERAS)


def _normalize_cameras(cameras):
    """Convert all-digit camera URLs to int device indices.

    cv2.VideoCapture("0") opens the literal file "0"; only the int form
    cv2.VideoCapture(0) addresses a local camera device. Accepting "0",
    "1", ... in WAMSS_CAMERAS lets a laptop's built-in webcam be used as
    e.g. WAMSS_CAMERAS='{"CAM_01":"0"}' without a wrapper script.
    """
    normalized = {}
    for cam_id, url in cameras.items():
        if isinstance(url, str) and url.strip().isdigit():
            normalized[cam_id] = int(url.strip())
        else:
            normalized[cam_id] = url
    return normalized


# SAMPLE ONLY: placeholder URLs using RFC 5737 TEST-NET-1 documentation
# addresses. They will never reach a real camera. These exist only so the
# module imports safely without configuration; real deployments MUST set
# WAMSS_CAMERAS or WAMSS_CAM_<id>_URL.
SAMPLE_CAMERAS = {
    "CAM_01": "http://192.0.2.10:8080/video",
    "CAM_02": "http://192.0.2.11:8080/video",
}

CAMERAS = _load_cameras()

MODEL_PATH = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")

# The real backend /event endpoint (NOT the deprecated standalone bridge).
BACKEND_URL = os.environ.get("WAMSS_BACKEND_URL", "http://localhost:8000/event")

# Shared secret for the backend API key auth (docs/AUTH.md). Sent as the
# X-API-Key header when set; when empty, auth stays disabled on the backend.
BACKEND_API_KEY = os.environ.get("WAMSS_API_KEY", "")


def _backend_headers():
    """Headers for backend POSTs; includes X-API-Key only when configured."""
    return {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else {}


# Privacy mode (WAMSS_PRIVACY_MODE=1): presence without identity.
# Frames stay in memory and are never written to disk unblurred; the pipeline
# emits person-count and coarse pose/occupancy aggregates only; no identity,
# gaze, or recognition model may run. See "Privacy mode" in vision/README.md.
PRIVACY_MODE = _parse_bool(os.environ.get("WAMSS_PRIVACY_MODE"), False)

# Occupancy debounce: seconds of sustained presence (person count > 0)
# before a zone reports "occupied", and seconds of sustained absence before
# it reports "vacant" again.
OCCUPANCY_DEBOUNCE_S = _parse_float(
    os.environ.get("WAMSS_OCCUPANCY_DEBOUNCE_S"), 5.0)

# Optional directory for privacy-mode occupancy snapshots. Frames stored here
# get faces blurred first (MediaPipe). If MediaPipe is absent, frames are NOT
# stored and the reason is logged. Empty by default: no snapshots are stored.
PRIVACY_SNAPSHOT_DIR = os.environ.get("WAMSS_PRIVACY_SNAPSHOT_DIR", "").strip()

# Send detection events at most this often per camera, in seconds.
EVENT_INTERVAL = _parse_float(os.environ.get("WAMSS_EVENT_INTERVAL"), 1.0)

# Headless mode: skip cv2.imshow / waitKey window calls and log detection
# summaries instead. Defaults to true when there is no X/Wayland display on
# Linux; override explicitly with WAMSS_HEADLESS=0/1.
if sys.platform.startswith("linux"):
    _headless_default = "DISPLAY" not in os.environ
else:
    _headless_default = False
HEADLESS = _parse_bool(os.environ.get("WAMSS_HEADLESS"), _headless_default)

# Throttle for headless per-frame detection summaries (seconds).
HEADLESS_LOG_INTERVAL = 5.0


# ---------------------------------------------------------------------------
# Workstream 1 (Vision) edge extensions: face recognition, 2D room position,
# per-track first-sighting snapshots, and dwell time.
#
# All of these are best-effort and degrade gracefully: when the optional
# modules or their dependencies are missing, the pipeline keeps its
# original behaviour. Face recognition is NEVER used in privacy mode;
# run_camera_privacy neither imports nor calls face_id (see the privacy
# guarantee block below).
# ---------------------------------------------------------------------------
try:
    from . import face_id as _face_id          # `python -m vision.multicamera`
except ImportError:
    try:
        import face_id as _face_id             # `python vision/multicamera.py`
    except ImportError:
        _face_id = None

try:
    from . import room_calib as _room_calib
except ImportError:
    try:
        import room_calib as _room_calib
    except ImportError:
        _room_calib = None

try:
    from . import identity_cache as _identity_cache
except ImportError:
    try:
        import identity_cache as _identity_cache
    except ImportError:
        _identity_cache = None

# Directory for first-sighting person snapshots (JPEG crops). Empty
# disables snapshots entirely.
SNAPSHOT_DIR = os.environ.get("WAMSS_SNAPSHOT_DIR", "data/snapshots").strip()

# Face recognition on the non-privacy path. Requires an enrolled gallery
# in vision/faces/; without a backend or gallery, names stay "unknown".
FACE_ID_ENABLED = _parse_bool(os.environ.get("WAMSS_FACE_ID"), True)

# Optional 2D room calibration JSON (see vision/room_calib.py). Without it,
# positions are normalized frame coordinates plus a bbox-height depth proxy.
ROOM_CALIB_PATH = os.environ.get("WAMSS_ROOM_CALIB", "").strip()


# ---------------------------------------------------------------------------
# Gallery sync (Workstream 4): pick up dashboard enrollments without a
# restart. The backend is the source of truth for the gallery (embeddings
# live in its faces table); this node polls GET /faces/gallery/version and,
# when the version changes, re-fetches GET /faces/gallery, writes the
# entries as JSON sidecars into vision/faces/, and calls
# face_id.reload_gallery() so the new names take effect.
#
# Sync is OFF unless a backend is explicitly configured: it needs either
# WAMSS_GALLERY_BASE_URL, or WAMSS_BACKEND_URL set in the environment
# (the built-in localhost default alone does not enable it). It never
# runs in privacy mode and never blocks the camera loop: any HTTP or
# disk failure is logged and the local gallery is kept.
# ---------------------------------------------------------------------------
def _gallery_base_from_backend():
    """Backend base URL for gallery sync, or "" when sync stays off.

    Derived from WAMSS_BACKEND_URL only when that variable was explicitly
    set; the built-in localhost default does not enable polling on its
    own. A trailing "/event" is stripped (BACKEND_URL points at the event
    endpoint, the gallery lives at the same base).
    """
    raw = os.environ.get("WAMSS_BACKEND_URL", "").strip()
    if not raw:
        return ""
    if raw.endswith("/event"):
        raw = raw[: -len("/event")]
    return raw.rstrip("/")


# Seconds between gallery version polls. 0 disables sync.
GALLERY_POLL_SECONDS = _parse_float(
    os.environ.get("WAMSS_GALLERY_POLL_SECONDS"), 30.0)

# Explicit gallery base URL, e.g. http://192.168.1.20:8000. Overrides the
# derivation from WAMSS_BACKEND_URL. Empty means "derive or stay off".
GALLERY_BASE_URL = os.environ.get("WAMSS_GALLERY_BASE_URL", "").strip() \
    or _gallery_base_from_backend()


# ---------------------------------------------------------------------------
# Privacy mode machinery (WAMSS_PRIVACY_MODE=1)
#
# DISK-WRITE AUDIT (2026-10-30, privacy round): this file previously contained
# no cv2.imwrite, cv2.VideoWriter, or snapshot code at all, so there was
# nothing to remove. Every frame-persistence call in this file now goes
# through save_frame_snapshot(), which refuses to write in privacy mode
# unless faces are blurred first (and refuses entirely when MediaPipe is
# absent). Keep it that way: never add a raw cv2.imwrite to this file.
#
# PRIVACY GUARANTEE (do not remove): identity, gaze, and face-recognition
# models are forbidden in this pipeline. Presence ("someone is here") must
# never become identity ("who is here"). Any future code path that needs
# re-identification, face recognition, or gaze estimation must be refused by
# _assert_privacy_model_allowed() and documented in vision/README.md.
#
# Workstream 1 note: vision/face_id.py and vision/identity_cache.py are
# imported only by run_camera() (the non-privacy path). run_camera_privacy
# never imports or calls them, never constructs an IdentityCache, and
# "face_recognition" is not in _PRIVACY_ALLOWED_MODELS, so requesting it in
# privacy mode would raise.
# Workstream 4 note: the gallery sync (GalleryRefresher, built only by
# _build_gallery_refresher on the non-privacy path) is covered by the same
# rule: _build_gallery_refresher returns None in privacy mode, and the
# refresher itself refuses to poll when privacy_mode is True, so privacy
# mode performs no face identification and no gallery network fetch.
# ---------------------------------------------------------------------------

_PRIVACY_ALLOWED_MODELS = (
    "person_pose",            # coarse pose landmarks, no identity
    "face_detection_blur_only",  # face boxes used ONLY to blur, never stored
)


def _assert_privacy_model_allowed(model_kind):
    """Refuse any model kind that implies identity, gaze, or recognition."""
    if model_kind not in _PRIVACY_ALLOWED_MODELS:
        raise RuntimeError(
            f"[privacy] REFUSED model '{model_kind}': identity/gaze/"
            f"recognition models are forbidden in privacy mode"
        )


class OccupancyDebouncer:
    """Debounced occupied/vacant state from per-frame person counts.

    Reports occupied only when person_count > 0 is sustained for
    `debounce_s` seconds, and vacant only when person_count == 0 is sustained
    for `debounce_s` seconds. Brief pass-bys do not flip the state.
    """

    def __init__(self, debounce_s=5.0):
        self.debounce_s = float(debounce_s)
        self.occupied = False
        self._since = None  # when the current streak away from state began

    def update(self, person_count, now):
        """Feed a new count. Returns (occupied, changed)."""
        want_occupied = person_count > 0
        if want_occupied == self.occupied:
            self._since = None
            return self.occupied, False
        if self._since is None:
            self._since = now
        if now - self._since >= self.debounce_s:
            self.occupied = want_occupied
            self._since = None
            return self.occupied, True
        return self.occupied, False


def blur_faces(frame):
    """Return a copy of the frame with detected faces blurred (in memory).

    Uses MediaPipe face detection on-device; face boxes are used only to
    blur and are never stored or sent anywhere. Returns
    (blurred_frame, True) on success. When MediaPipe is absent, returns
    (original_frame, False) so callers know the frame must NOT be stored.
    """
    _assert_privacy_model_allowed("face_detection_blur_only")
    if mp is None:
        print("[privacy] WARNING: mediapipe not installed; cannot blur "
              "faces, so this frame must NOT be stored")
        return frame, False
    # Created per call to keep the helper stateless and testable; hoist to a
    # shared instance if this ever runs in a hot loop.
    detector = mp.solutions.face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=0.5)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    res = detector.process(rgb)
    out = frame.copy()
    if not res.detections:
        return out, True  # nothing to blur; safe to store
    h, w = out.shape[:2]
    for det in res.detections:
        box = det.location_data.relative_bounding_box
        x = max(0, int(box.xmin * w))
        y = max(0, int(box.ymin * h))
        x2 = min(w, x + int(box.width * w))
        y2 = min(h, y + int(box.height * h))
        if x2 > x and y2 > y:
            roi = out[y:y2, x:x2]
            out[y:y2, x:x2] = cv2.GaussianBlur(roi, (51, 51), 0)
    return out, True


def save_frame_snapshot(frame, path):
    """Store a frame to disk, privacy-safe. Returns True when written.

    Privacy mode: faces are blurred first via MediaPipe. If MediaPipe is
    absent the frame is NOT stored and the reason is logged (returns False).
    Outside privacy mode: stored as-is (existing behaviour preserved).
    """
    if PRIVACY_MODE:
        blurred, ok = blur_faces(frame)
        if not ok:
            print(f"[privacy] snapshot NOT stored ({path}): face blurring "
                  f"is unavailable without mediapipe")
            return False
        cv2.imwrite(path, blurred)
        print(f"[privacy] anonymized snapshot stored: {path}")
        return True
    cv2.imwrite(path, frame)
    return True


def _load_room_calibration():
    """Load the optional 4-point room calibration; None when absent/bad."""
    if _room_calib is None or not ROOM_CALIB_PATH:
        return None
    try:
        calib = _room_calib.load_calibration(ROOM_CALIB_PATH)
        print(f"[config] Room calibration loaded: {ROOM_CALIB_PATH}")
        return calib
    except Exception as exc:
        print(f"[config] WARNING: ignoring bad room calibration "
              f"({ROOM_CALIB_PATH}): {exc}")
        return None


def _identify_crop_scored(frame, bbox):
    """Best-effort (name, confidence) for a bbox crop.

    Returns ("unknown", None) when face recognition is disabled or
    unavailable, when no face matches, or on any failure. Confidence is
    a float in 0..1 for a match. Falls back to the name-only identify()
    (confidence None) for face_id modules without identify_with_confidence.
    """
    if _face_id is None or not FACE_ID_ENABLED:
        return "unknown", None
    try:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return "unknown", None
        crop = frame[y1:y2, x1:x2]
        identify_scored = getattr(_face_id, "identify_with_confidence", None)
        if callable(identify_scored):
            return identify_scored(crop)
        return _face_id.identify(crop), None
    except Exception:
        return "unknown", None


def _sanitize_gallery_name(name):
    """Filesystem-safe gallery filename stem, mirroring enroll_face.py."""
    safe = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return safe or "unnamed"


class GalleryRefresher:
    """Poll the backend gallery version and sync new enrollments to disk.

    The backend serves GET /faces/gallery/version ({"version": N, ...})
    and GET /faces/gallery ([{"name": ..., "embedding": [...]}, ...]).
    On a version change this writes one JSON sidecar per entry into the
    vision gallery dir (the format face_id._load_gallery reads) and calls
    face_id.reload_gallery(). Entries with no embedding (the backend
    stores a placeholder when no face library was available) are skipped:
    they cannot match anything. Sidecars this refresher previously wrote
    for names absent from the new gallery are deleted, so a dashboard
    delete takes effect; files it did not write are never touched.

    Never raises and never stalls the caller: every HTTP, JSON, or disk
    failure is logged and the local gallery is kept. A failed poll does
    not advance the seen version, so the next due poll retries. Never
    runs in privacy mode: enabled is False when privacy_mode is True,
    and run_camera_privacy never constructs one.
    """

    def __init__(self, base_url, poll_seconds=30.0, face_id_module=None,
                 http_get=None, privacy_mode=False):
        self.base_url = (base_url or "").rstrip("/")
        try:
            self.poll_seconds = float(poll_seconds)
        except (TypeError, ValueError):
            self.poll_seconds = 30.0
        self._face_id = face_id_module
        self._http_get = http_get  # injectable for tests; else requests.get
        self._privacy_mode = bool(privacy_mode)
        self._last_version = None
        self._last_poll = None
        self._managed = {}  # sidecar filename -> person name

    @property
    def enabled(self):
        return (not self._privacy_mode and self.poll_seconds > 0
                and bool(self.base_url) and self._face_id is not None)

    def maybe_refresh(self, now):
        """Poll when due. Returns True when the gallery was reloaded."""
        if not self.enabled:
            return False
        if (self._last_poll is not None
                and now - self._last_poll < self.poll_seconds):
            return False
        self._last_poll = now
        try:
            version = self._fetch_version()
        except Exception as exc:
            print(f"[gallery] version poll failed ({exc}); keeping "
                  f"local gallery")
            return False
        if version == self._last_version:
            return False
        try:
            entries = self._fetch_gallery()
            payloads = self._validate(entries)
        except Exception as exc:
            print(f"[gallery] gallery fetch failed ({exc}); keeping "
                  f"local gallery")
            return False
        try:
            self._apply(payloads)
        except Exception as exc:
            print(f"[gallery] gallery apply failed ({exc}); keeping "
                  f"local gallery")
            return False
        self._last_version = version
        return True

    def _get(self, path):
        get = self._http_get if self._http_get is not None else requests.get
        response = get(self.base_url + path, timeout=2.0,
                       headers=_backend_headers())
        response.raise_for_status()
        return response

    def _fetch_version(self):
        data = self._get("/faces/gallery/version").json()
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError(
                "gallery version response has no integer version")
        return version

    def _fetch_gallery(self):
        data = self._get("/faces/gallery").json()
        if not isinstance(data, list):
            raise ValueError("gallery response is not a list")
        return data

    def _validate(self, entries):
        """Validated (filename, payload) pairs; skips unmatchable entries."""
        payloads = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            embedding = entry.get("embedding")
            if (not name or not isinstance(name, str)
                    or not isinstance(embedding, list) or not embedding):
                continue
            try:
                vector = [float(v) for v in embedding]
            except (TypeError, ValueError):
                continue
            filename = _sanitize_gallery_name(name) + ".json"
            payloads.append((filename, {
                "name": name,
                "method": "insightface",
                "embedding": vector,
                "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "source": "backend-sync",
            }))
        return payloads

    def _apply(self, payloads):
        gallery_dir = self._face_id.GALLERY_DIR
        os.makedirs(gallery_dir, exist_ok=True)
        seen = set()
        for filename, payload in payloads:
            path = os.path.join(gallery_dir, filename)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            seen.add(filename)
            self._managed[filename] = payload["name"]
        removed = 0
        for filename in list(self._managed):
            if filename not in seen:
                try:
                    os.remove(os.path.join(gallery_dir, filename))
                    removed += 1
                except OSError as exc:
                    print(f"[gallery] WARNING: could not remove stale "
                          f"sidecar {filename}: {exc}")
                del self._managed[filename]
        self._face_id.reload_gallery()
        print(f"[gallery] synced {len(payloads)} entries, removed "
              f"{removed} stale")


def _build_gallery_refresher():
    """Gallery sync for the non-privacy path, or None when disabled.

    Disabled when privacy mode is on, face recognition is off, no
    gallery base URL is configured, polling is set to 0, or face_id is
    unavailable.
    """
    if PRIVACY_MODE or not FACE_ID_ENABLED:
        return None
    if not GALLERY_BASE_URL or GALLERY_POLL_SECONDS <= 0:
        return None
    if _face_id is None:
        return None
    return GalleryRefresher(GALLERY_BASE_URL, GALLERY_POLL_SECONDS,
                            _face_id, privacy_mode=PRIVACY_MODE)


# Seconds between geofence zone sync polls from backend /api/zones. 0 disables polling.
GEOFENCE_POLL_SECONDS = _parse_float(
    os.environ.get("WAMSS_GEOFENCE_POLL_SECONDS"), 30.0)


class ZoneRefresher:
    """Poll backend /api/zones and sync active zones to the GeofenceEvaluator."""

    def __init__(self, base_url, poll_seconds, evaluator, camera_id=None,
                 http_get=None, headers_fn=None):
        self.base_url = (base_url or "").rstrip("/")
        self.poll_seconds = float(poll_seconds)
        self.evaluator = evaluator
        self.camera_id = camera_id
        self._http_get = http_get or requests.get
        self._headers_fn = headers_fn or _backend_headers
        self.last_poll_time = 0.0

    def maybe_refresh(self, now):
        if self.poll_seconds <= 0 or not self.base_url or self.evaluator is None:
            return False
        if now - self.last_poll_time < self.poll_seconds:
            return False
        self.last_poll_time = now
        try:
            url = f"{self.base_url}/api/zones"
            params = {"is_active": "true"}
            if self.camera_id:
                params["camera_id"] = self.camera_id
            resp = self._http_get(url, params=params, headers=self._headers_fn(), timeout=3.0)
            if resp.status_code == 200:
                raw_zones = resp.json()
                if isinstance(raw_zones, list) and ZoneConfig is not None:
                    configs = [ZoneConfig.from_dict(z) for z in raw_zones]
                    self.evaluator.update_zones(configs)
                    return True
        except Exception as exc:
            print(f"[geofence] WARNING: could not refresh zones: {exc}")
        return False


def _build_geofence_evaluator(camera_id, zones=None, privacy_mode=False):
    """Build a GeofenceEvaluator instance or None if module unavailable."""
    if GeofenceEvaluator is None:
        return None
    return GeofenceEvaluator(
        zones=zones or [],
        camera_id=camera_id,
        privacy_mode=privacy_mode or PRIVACY_MODE,
    )


def _post_geofence_events(events, backend_events_url, headers=None, http_post=None):
    """
    Safely post geofence transition events to backend /api/zones/events.
    Catches and logs any network or request failures so camera loop is never interrupted.
    """
    if not events or not backend_events_url:
        return False
    poster = http_post or requests.post
    payload = []
    for ev in events:
        tid_int = int(ev.track_id) if str(ev.track_id).isdigit() else 0
        iso_ts = None
        if ev.timestamp:
            iso_ts = datetime.fromtimestamp(ev.timestamp, tz=timezone.utc).isoformat()

        payload.append({
            "zone_id": int(ev.zone_id),
            "transition_type": str(ev.event_type),
            "track_id": tid_int,
            "zone_name": str(ev.zone_name),
            "zone_type": str(ev.zone_type),
            "camera_id": ev.camera_id,
            "person_label": str(ev.person_label),
            "identity_name": ev.identity_name,
            "identity_id": ev.identity_id,
            "dwell_seconds": float(ev.dwell_seconds),
            "alert_triggered": bool(ev.alert_triggered),
            "timestamp": iso_ts,
        })
    try:
        req_headers = headers if headers is not None else _backend_headers()
        resp = poster(backend_events_url, json=payload, headers=req_headers, timeout=3.0)
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[geofence] WARNING: failed to POST geofence events: {exc}")
        return False




def _build_person_event(camera_id, track_key, person_name, name_confidence,
                        position_x, position_y, snapshot_path,
                        dwell_seconds, max_confidence):
    """One per-person detection event for the backend /event endpoint.

    person_name_confidence is the smoothed face-match confidence in 0..1,
    or None when no face match was attempted or none matched.
    """
    return {
        "camera_id": camera_id,
        "track_id": track_key,
        "person_name": person_name,
        "person_name_confidence": name_confidence,
        "position_x": float(position_x),
        "position_y": float(position_y),
        "snapshot_path": snapshot_path,
        "dwell_seconds": float(dwell_seconds),
        "objects": ["person"],
        "confidence": float(max_confidence),
        "zone": "UNKNOWN",
    }


def _save_person_snapshot(frame, bbox, track_key, snapshots):
    """Save a first-sighting JPEG crop of a track. Best-effort: never raises.

    Records snapshots[track_key] as a relative path like
    "snapshots/Person_1_20261102_120501.jpg" (relative to the snapshot
    dir's parent), or None when snapshots are disabled or the save failed.
    """
    snapshots[track_key] = None
    if not SNAPSHOT_DIR:
        return
    try:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return
        safe_id = re.sub(r"[^A-Za-z0-9]+", "_", track_key).strip("_")
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        full_path = os.path.join(SNAPSHOT_DIR, f"{safe_id}_{stamp}.jpg")
        # Routed through the privacy-audited disk-write helper. Privacy
        # mode never calls this: its loop stores no raw frames.
        save_frame_snapshot(frame[y1:y2, x1:x2], full_path)
        if os.path.isfile(full_path):
            rel = os.path.relpath(
                full_path, os.path.dirname(os.path.abspath(SNAPSHOT_DIR)))
            snapshots[track_key] = rel.replace(os.sep, "/")
    except Exception as exc:
        print(f"[snapshot] WARNING: could not save snapshot for "
              f"{track_key}: {exc}")
        snapshots[track_key] = None


class PrivacyDetector:
    """Privacy-mode presence pipeline: MediaPipe on-device, aggregates only.

    MediaPipe installed: FaceDetection (on-device BlazeFace) counts people
    and Pose gives a coarse standing/sitting aggregate. Neither produces
    embeddings, identities, or gaze. MediaPipe absent: graceful fallback to
    the YOLO person-class path, emitting counts only (track ids dropped).
    Frames are processed in memory and never written to disk by this class.
    """

    def __init__(self):
        self.backend = None  # "mediapipe" | "yolo_fallback"
        self._mp_face = None
        self._mp_pose = None
        self._yolo = None
        if mp is not None:
            _assert_privacy_model_allowed("face_detection_blur_only")
            _assert_privacy_model_allowed("person_pose")
            self._mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5)
            self._mp_pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5)
            self.backend = "mediapipe"
            print("[privacy] MediaPipe backend active (on-device face "
                  "detection + pose; no identity, no gaze)")
        else:
            # Graceful fallback: same YOLO weights, but only the person
            # count is used. No track ids, no boxes leave this class.
            self._yolo = YOLO(MODEL_PATH)
            self.backend = "yolo_fallback"
            print("[privacy] WARNING: mediapipe not installed; falling back "
                  "to YOLO person-count aggregates only (no track ids, "
                  "no pose)")

    def detect(self, frame):
        """Return (person_count, coarse_pose) from a BGR frame, in memory."""
        if self._mp_face is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_res = self._mp_face.process(rgb)
            count = len(face_res.detections) if face_res.detections else 0
            pose = self._coarse_pose(rgb) if count > 0 else "unknown"
            return count, pose
        results = self._yolo(frame, classes=[0], conf=0.45, verbose=False)
        boxes = results[0].boxes
        return (len(boxes) if boxes is not None else 0), "unknown"

    def _coarse_pose(self, rgb):
        """Best-effort standing/sitting/unknown from pose landmarks."""
        try:
            res = self._mp_pose.process(rgb)
            if not res.pose_landmarks:
                return "unknown"
            lm = res.pose_landmarks.landmark
            # MediaPipe pose indices: 11/12 shoulders, 23/24 hips, 27/28
            # ankles. Standing when the leg segment is long relative to the
            # torso; sitting when it is short. Coarse aggregate only.
            shoulder_y = (lm[11].y + lm[12].y) / 2
            hip_y = (lm[23].y + lm[24].y) / 2
            ankle_y = (lm[27].y + lm[28].y) / 2
            torso = abs(shoulder_y - hip_y)
            leg = abs(hip_y - ankle_y)
            if torso <= 0:
                return "unknown"
            return "standing" if leg > 0.6 * torso else "sitting"
        except Exception:
            return "unknown"


def build_privacy_event(camera_id, person_count, coarse_pose, occupied):
    """Aggregate-only occupancy event. Never contains a track id."""
    return {
        "camera_id": camera_id,
        "occupancy": "occupied" if occupied else "vacant",
        "person_count": int(person_count),
        "pose": coarse_pose,
        "privacy_mode": True,
    }


def run_camera(camera_id, stream_url):
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(stream_url)
    last_event_time = 0.0
    last_summary_time = 0.0
    frame_no = 0

    # Workstream 1 state: per-track first-seen times (dwell time),
    # first-sighting snapshot paths, and the optional room calibration.
    first_seen = {}
    snapshots = {}
    calib = _load_room_calibration()

    # Per-track identity cache (vision/identity_cache.py): identifies every
    # tracked person on first sight, re-verifies periodically, and smooths
    # names with majority voting. None when the module is unavailable or
    # face recognition is disabled; the event loop then falls back to a
    # direct per-event identify call, as before.
    identity = None
    if _identity_cache is not None and FACE_ID_ENABLED:
        identity = _identity_cache.IdentityCache(_identify_crop_scored)

    # Gallery sync (Workstream 4): dashboard enrollments reach this node
    # without a restart. None when disabled; never built in privacy mode
    # (run_camera_privacy is a separate loop that never touches it).
    gallery_refresher = _build_gallery_refresher()

    # Geofence spatial evaluator (Build #2: Real Geofence Zones)
    geofence_evaluator = _build_geofence_evaluator(camera_id, privacy_mode=PRIVACY_MODE)
    geofence_base_url = GALLERY_BASE_URL or _gallery_base_from_backend()
    geofence_events_url = f"{geofence_base_url}/api/zones/events" if geofence_base_url else ""
    geofence_refresher = (
        ZoneRefresher(geofence_base_url, GEOFENCE_POLL_SECONDS, geofence_evaluator, camera_id=camera_id)
        if (geofence_evaluator is not None and geofence_base_url and GEOFENCE_POLL_SECONDS > 0)
        else None
    )

    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Cannot open camera stream")
        return

    print(f"[{camera_id}] Camera connected (headless={HEADLESS})")

    try:
        while True:
            success, frame = cap.read()

            if not success:
                print(f"[{camera_id}] No frame received")
                time.sleep(0.2)
                continue

            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=0.45,
                verbose=False,
            )
            frame_no += 1

            boxes = results[0].boxes
            now = time.time()

            # Gallery sync check: cheap time comparison per frame; the
            # HTTP poll itself runs at most every GALLERY_POLL_SECONDS
            # and never raises (failures log and keep the local gallery).
            if gallery_refresher is not None:
                gallery_refresher.maybe_refresh(now)

            if geofence_refresher is not None:
                geofence_refresher.maybe_refresh(now)

            person_count = len(boxes) if boxes is not None else 0

            # Per-frame per-track bookkeeping: first-seen timestamps for
            # dwell time, a first-sighting snapshot for each new track, and
            # an identity observation (identify on first sight, then
            # re-verify periodically; see vision/identity_cache.py).
            # Best-effort; failures here never stop the pipeline.
            if boxes is not None and boxes.id is not None:
                track_ids = boxes.id.cpu().tolist()
                track_boxes = boxes.xyxy.cpu().tolist()
                for tid, tbox in zip(track_ids, track_boxes):
                    track_key = f"Person {int(tid)}"
                    if track_key not in first_seen:
                        first_seen[track_key] = now
                        _save_person_snapshot(frame, tbox, track_key,
                                              snapshots)
                    if identity is not None:
                        identity.observe(track_key, frame, tbox,
                                         frame_no, now)
                    if geofence_evaluator is not None:
                        p_name, _ = identity.identity(track_key) if identity is not None else (None, None)
                        gf_events = geofence_evaluator.evaluate(
                            track_id=str(int(tid)),
                            position_or_bbox=tbox,
                            timestamp=now,
                            identity_name=p_name,
                            frame_w=frame.shape[1] if len(frame.shape) > 1 else None,
                            frame_h=frame.shape[0] if len(frame.shape) > 0 else None,
                        )
                        for gf_ev in gf_events:
                            print(
                                f"[{camera_id}] GEOFENCE {gf_ev.event_type} | "
                                f"Zone: {gf_ev.zone_name} | "
                                f"Track: {gf_ev.person_label} | "
                                f"Dwell: {gf_ev.dwell_seconds:.1f}s | "
                                f"Alert: {gf_ev.alert_triggered}"
                            )
                        if gf_events and geofence_events_url:
                            _post_geofence_events(gf_events, geofence_events_url)

            # Send one event per tracked person when the interval allows.
            # Previously only the first box was reported; every person in
            # frame now gets their own event with their own identity.
            if person_count > 0 and now - last_event_time >= EVENT_INTERVAL:
                confidences = boxes.conf.cpu().tolist()
                max_conf = float(max(confidences)) if confidences else 0.0
                track_boxes = boxes.xyxy.cpu().tolist()
                if boxes.id is not None:
                    pairs = [(f"Person {int(tid)}", tbox)
                             for tid, tbox in zip(boxes.id.cpu().tolist(),
                                                  track_boxes)]
                else:
                    # No track ids (should not happen with ByteTrack
                    # persist=True): fall back to the single first box,
                    # as before.
                    pairs = [("unknown", track_boxes[0])]

                frame_h, frame_w = frame.shape[:2]
                for track_key, bbox in pairs:
                    if _room_calib is not None:
                        position_x, position_y = _room_calib.map_position(
                            bbox, frame_w, frame_h, calib)
                    else:
                        # Degraded fallback: normalized bottom-center plus a
                        # bbox-height depth proxy (same formula as
                        # room_calib.map_position without calibration).
                        x1, y1, x2, y2 = bbox
                        position_x = min(1.0, max(0.0,
                                                  ((x1 + x2) / 2) / frame_w))
                        position_y = min(1.0, max(0.0, (y2 - y1) / frame_h))

                    dwell_seconds = now - first_seen.get(track_key, now)

                    if identity is not None and track_key != "unknown":
                        person_name, name_conf = identity.identity(track_key)
                    else:
                        person_name, name_conf = _identify_crop_scored(
                            frame, bbox)

                    event = _build_person_event(
                        camera_id, track_key, person_name, name_conf,
                        position_x, position_y,
                        snapshots.get(track_key),
                        dwell_seconds, max_conf)

                    try:
                        response = requests.post(
                            BACKEND_URL,
                            json=event,
                            timeout=2.0,
                            headers=_backend_headers(),
                        )
                        response.raise_for_status()

                        data = response.json()
                        print(
                            f"[{camera_id}] Person detected | "
                            f"Backend: {data.get('decision', 'UNKNOWN')} | "
                            f"Track: {track_key} | "
                            f"Name: {event['person_name']}"
                        )

                    except requests.RequestException as exc:
                        print(f"[{camera_id}] Backend request failed: {exc}")

                last_event_time = now
                last_summary_time = now

            elif HEADLESS and person_count > 0 \
                    and now - last_summary_time >= HEADLESS_LOG_INTERVAL:
                # Headless mode: no preview window, so log a short summary
                # between event sends instead of drawing frames.
                max_conf = float(max(boxes.conf.cpu().tolist()))
                print(f"[{camera_id}] Headless summary: {person_count} "
                      f"person(s) in view, max confidence {max_conf:.2f}")
                last_summary_time = now

            if HEADLESS:
                # No display available: keep processing frames without any
                # window calls.
                continue

            # Windowed preview (only reached when a display is available)
            display = results[0].plot()

            cv2.putText(
                display,
                camera_id,
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
            )

            cv2.imshow(camera_id, display)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except Exception as exc:
        print(f"[{camera_id}] Error: {exc}")

    finally:
        cap.release()
        if not HEADLESS:
            cv2.destroyAllWindows()


def run_camera_privacy(camera_id, stream_url):
    """Privacy-mode loop: in-memory frames, aggregate events only.

    No track ids, no raw frames on disk, no preview windows (a window would
    display unblurred faces). Occupancy transitions are POSTed as aggregates;
    alert snapshots are stored only when WAMSS_PRIVACY_SNAPSHOT_DIR is set,
    with faces blurred first.
    """
    detector = PrivacyDetector()
    debouncer = OccupancyDebouncer(debounce_s=OCCUPANCY_DEBOUNCE_S)
    cap = cv2.VideoCapture(stream_url)
    last_summary_time = 0.0

    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Cannot open camera stream")
        return

    print(f"[{camera_id}] Camera connected (privacy mode, "
          f"backend={detector.backend}, "
          f"debounce={OCCUPANCY_DEBOUNCE_S}s)")

    try:
        while True:
            success, frame = cap.read()

            if not success:
                print(f"[{camera_id}] No frame received")
                time.sleep(0.2)
                continue

            now = time.time()
            person_count, coarse_pose = detector.detect(frame)
            occupied, changed = debouncer.update(person_count, now)

            if changed:
                event = build_privacy_event(
                    camera_id, person_count, coarse_pose, occupied)

                try:
                    response = requests.post(
                        BACKEND_URL,
                        json=event,
                        timeout=2.0,
                        headers=_backend_headers(),
                    )
                    response.raise_for_status()
                    print(f"[{camera_id}] occupancy -> {event['occupancy']} "
                          f"(count={person_count}, pose={coarse_pose})")

                except requests.RequestException as exc:
                    print(f"[{camera_id}] Backend request failed: {exc}")

                if occupied and PRIVACY_SNAPSHOT_DIR:
                    os.makedirs(PRIVACY_SNAPSHOT_DIR, exist_ok=True)
                    snap_path = os.path.join(
                        PRIVACY_SNAPSHOT_DIR,
                        f"{camera_id}_{int(now)}.jpg",
                    )
                    # save_frame_snapshot blurs faces first; when MediaPipe
                    # is absent it refuses to store and logs why.
                    save_frame_snapshot(frame, snap_path)

            elif now - last_summary_time >= HEADLESS_LOG_INTERVAL:
                print(f"[{camera_id}] privacy summary: count={person_count}, "
                      f"pose={coarse_pose}, occupied={debouncer.occupied}")
                last_summary_time = now

            # No preview window in privacy mode by design: showing the raw
            # frame would display unblurred faces to anyone near the screen.

    except Exception as exc:
        print(f"[{camera_id}] Error: {exc}")

    finally:
        cap.release()


def main():
    if not CAMERAS:
        print("[config] ERROR: no cameras configured and no sample default "
              "is in use. Set WAMSS_CAMERAS or WAMSS_CAM_<id>_URL.")
        return

    print(f"[config] Backend: {BACKEND_URL}")
    print(f"[config] Model: {MODEL_PATH}")
    print(f"[config] Event interval: {EVENT_INTERVAL}s, headless: {HEADLESS}")
    print(f"[config] Privacy mode: {PRIVACY_MODE}"
          + (f" (debounce={OCCUPANCY_DEBOUNCE_S}s)" if PRIVACY_MODE else ""))
    print(f"[config] Face ID: {FACE_ID_ENABLED}, snapshots: "
          f"{SNAPSHOT_DIR or 'disabled'}, room calibration: "
          f"{ROOM_CALIB_PATH or 'none'}")
    if not PRIVACY_MODE:
        print(f"[config] Gallery sync: "
              f"{GALLERY_BASE_URL or 'disabled'}"
              + (f" (poll {GALLERY_POLL_SECONDS}s)"
                 if GALLERY_BASE_URL else ""))

    threads = []

    for camera_id, stream_url in CAMERAS.items():
        target = run_camera_privacy if PRIVACY_MODE else run_camera
        thread = threading.Thread(
            target=target,
            args=(camera_id, stream_url),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.2)

    except KeyboardInterrupt:
        print("Stopping WAMSS...")

    finally:
        if not HEADLESS:
            cv2.destroyAllWindows()
        print("WAMSS stopped")


if __name__ == "__main__":
    main()
