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
AGGREGATE_BACKEND_URL = os.environ.get("WAMSS_AGGREGATE_BACKEND_URL", "").strip()
if not AGGREGATE_BACKEND_URL:
    _backend_base = BACKEND_URL.rstrip("/")
    if _backend_base.endswith("/event"):
        _backend_base = _backend_base[:-len("/event")]
    AGGREGATE_BACKEND_URL = _backend_base.rstrip("/") + "/event/aggregate"

# Shared secret for the backend API key auth (docs/AUTH.md). Sent as the
# X-API-Key header when set; when empty, auth stays disabled on the backend.
BACKEND_API_KEY = os.environ.get("WAMSS_API_KEY", "")


def _backend_headers():
    """Headers for backend POSTs; includes X-API-Key only when configured."""
    return {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else {}


# Privacy mode (WAMSS_PRIVACY_MODE=1): aggregate occupancy only.
# Frames stay in memory and are never written to disk; only a person count and
# occupancy transition leave the camera node. No identity, pose, or gaze data. See "Privacy mode" in vision/README.md.
PRIVACY_MODE = _parse_bool(os.environ.get("WAMSS_PRIVACY_MODE"), False)

# Occupancy debounce: seconds of sustained presence (person count > 0)
# before a zone reports "occupied", and seconds of sustained absence before
# it reports "vacant" again.
OCCUPANCY_DEBOUNCE_S = _parse_float(
    os.environ.get("WAMSS_OCCUPANCY_DEBOUNCE_S"), 5.0)

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
# Vision edge extensions: consent-scoped face recognition, 2D room position,
# and dwell time. The active camera loop does not save first-sighting images.
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

# Retained for the legacy snapshot helper only. The active camera loop does
# not save first-sighting person snapshots.
SNAPSHOT_DIR = os.environ.get("WAMSS_SNAPSHOT_DIR", "data/snapshots").strip()

# Face recognition on the non-privacy path. Requires a fresh, per-camera
# consent allow-list from the backend; local sidecars never authorize a match.
FACE_ID_ENABLED = _parse_bool(os.environ.get("WAMSS_FACE_ID"), True)

# Optional 2D room calibration JSON (see vision/room_calib.py). Without it,
# positions are normalized frame coordinates plus a bbox-height depth proxy.
ROOM_CALIB_PATH = os.environ.get("WAMSS_ROOM_CALIB", "").strip()


# ---------------------------------------------------------------------------
# Camera-scoped consent allow-list. The active loop requests the gallery for
# each exact camera ID on a short refresh interval. It never writes the
# response to local gallery sidecars, and any request error empties the
# allow-list so a stale name cannot be emitted.
# ---------------------------------------------------------------------------
def _gallery_base_from_backend():
    """Backend base URL for the consent gallery, or "" when unavailable.

    Derived from WAMSS_BACKEND_URL only when that variable was explicitly
    set; the built-in localhost default does not enable identity on its own.
    A trailing "/event" is stripped because the gallery lives at the same base.
    """
    raw = os.environ.get("WAMSS_BACKEND_URL", "").strip()
    if not raw:
        return ""
    if raw.endswith("/event"):
        raw = raw[: -len("/event")]
    return raw.rstrip("/")


# Seconds between camera-scoped consent gallery refreshes.
# A failed request clears the local allow-list, so a stale cached identity
# cannot be emitted after consent withdrawal or loss of backend connectivity.
CONSENT_POLL_SECONDS = max(0.1, _parse_float(
    os.environ.get("WAMSS_CONSENT_POLL_SECONDS"), 1.0))

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
# through save_frame_snapshot(). Privacy mode refuses all frame writes; the
# aggregate pipeline never stores snapshots. Keep it that way: never add a raw
# cv2.imwrite to the privacy path.
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
# The legacy GalleryRefresher is not constructed by the camera loop; the
# active CameraGallery path is also absent in privacy mode, which performs
# no face identification and no gallery network fetch.
# ---------------------------------------------------------------------------

_PRIVACY_ALLOWED_MODELS = (
    "face_detection_count_only",  # detection count only; no boxes leave the node
    "face_detection_blur_only",   # legacy in-memory helper; never used for privacy storage
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
        self._since = None
        self._pending = None
        self.pending_count = None

    def update(self, person_count, now):
        """Return a pending transition until acknowledge() confirms delivery."""
        if self._pending is not None:
            return self._pending, True
        want_occupied = person_count > 0
        if want_occupied == self.occupied:
            self._since = None
            return self.occupied, False
        if self._since is None:
            self._since = now
        if now - self._since >= self.debounce_s:
            self._pending = want_occupied
            self.pending_count = max(0, int(person_count)) if want_occupied else 0
            return self._pending, True
        return self.occupied, False

    def acknowledge(self):
        """Commit the pending state only after the aggregate POST succeeds."""
        if self._pending is None:
            return
        self.occupied = self._pending
        self._pending = None
        self.pending_count = None
        self._since = None


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
    """Store a non-privacy-mode frame. Privacy mode always refuses snapshots."""
    if PRIVACY_MODE:
        print("[privacy] snapshot NOT stored: privacy mode permits aggregate data only")
        return False
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


def _identify_crop_scored(frame, bbox, gallery_entries=None):
    """Best-effort (name, confidence) for a bbox crop.

    Returns ("unknown", None) when face recognition is disabled or
    unavailable, when no camera-authorized gallery is supplied, when no face
    matches, or on any failure. Confidence is a float in 0..1 for a match.
    Legacy matchers that cannot accept a restricted gallery are not used.
    """
    if (_face_id is None or not FACE_ID_ENABLED
            or not gallery_entries):
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
            return identify_scored(crop, gallery_entries=gallery_entries)
        return "unknown", None
    except Exception:
        return "unknown", None


class GalleryRefresher:
    """Deprecated, unscoped gallery API retained as an inert compatibility shim.

    The active camera loop uses CameraGallery, whose backend request is scoped
    to a camera and whose entries are filtered by active employee consent.
    """

    def __init__(self, base_url, poll_seconds=30.0, face_id_module=None,
                 http_get=None, privacy_mode=False):
        self.base_url = (base_url or "").rstrip("/")
        self.poll_seconds = poll_seconds
        self._face_id = face_id_module
        self._http_get = http_get
        self._privacy_mode = bool(privacy_mode)

    @property
    def enabled(self):
        return False

    def maybe_refresh(self, now):
        return False


class CameraGallery:
    """Live allow-list from the backend for one camera.

    The backend filters names and embeddings by active employee consent and
    camera mode. Errors clear the allow-list (fail closed); a successful
    refresh is required before a cached name can be emitted.
    """

    def __init__(self, camera_id, base_url, poll_seconds=1.0, http_get=None):
        self.camera_id = str(camera_id)
        self.base_url = (base_url or "").rstrip("/")
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._http_get = http_get
        self._last_poll = None
        self.entries = []

    def maybe_refresh(self, now, force=False):
        if not self.base_url:
            changed = bool(self.entries)
            self.entries = []
            return changed
        if (not force and self._last_poll is not None
                and now - self._last_poll < self.poll_seconds):
            return False
        self._last_poll = now
        old_key = self._key(self.entries)
        try:
            get = self._http_get if self._http_get is not None else requests.get
            response = get(
                self.base_url + "/faces/gallery",
                params={"camera_id": self.camera_id},
                timeout=2.0,
                headers=_backend_headers(),
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("camera gallery response is not a list")
            entries = []
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                name, embedding = entry.get("name"), entry.get("embedding")
                if (not isinstance(name, str) or not name.strip()
                        or not isinstance(embedding, list) or not embedding):
                    continue
                try:
                    vector = [float(value) for value in embedding]
                except (TypeError, ValueError):
                    continue
                entries.append({
                    "name": name.strip(),
                    "embedding": vector,
                    "method": "insightface",
                })
            self.entries = entries
        except Exception as exc:
            print(f"[consent] camera gallery unavailable for "
                  f"{self.camera_id}; identification disabled ({type(exc).__name__})")
            self.entries = []
        return old_key != self._key(self.entries)

    @staticmethod
    def _key(entries):
        return tuple(sorted(
            (entry.get("name", ""), tuple(entry.get("embedding", [])))
            for entry in entries
        ))


def _build_gallery_refresher():
    """Legacy unscoped gallery sync is disabled for consent safety.

    run_camera() uses CameraGallery, which fetches a camera-specific allow-list
    and clears it when the backend is unavailable.
    """
    return None


def _build_person_event(camera_id, track_key, person_name, name_confidence,
                        position_x, position_y, snapshot_path,
                        dwell_seconds, max_confidence,
                        position_bounds=(1.0, 1.0)):
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
        "position_bounds": [float(v) for v in position_bounds],
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
    """Privacy-mode presence pipeline: on-device count only, no person records.

    MediaPipe counts face detections locally; the fallback counts YOLO person
    detections. Neither sends boxes, pose, tracks, or frames off the node.
    Frames remain in memory and are never written to disk by this class.
    """

    def __init__(self):
        self.backend = None  # "mediapipe" | "yolo_fallback"
        self._mp_face = None
        self._yolo = None
        if mp is not None:
            _assert_privacy_model_allowed("face_detection_count_only")
            self._mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5)
            self.backend = "mediapipe"
            print("[privacy] MediaPipe face-count backend active; "
                  "no identity, pose, or gaze data is generated")
        else:
            # Graceful fallback: same YOLO weights, but only the person
            # count is used. No track ids, no boxes leave this class.
            self._yolo = YOLO(MODEL_PATH)
            self.backend = "yolo_fallback"
            print("[privacy] WARNING: mediapipe not installed; falling back "
                  "to YOLO person-count aggregates only (no track ids, "
                  "no pose)")

    def detect(self, frame):
        """Return only an in-memory person count; no pose or identity is retained."""
        if self._mp_face is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_res = self._mp_face.process(rgb)
            return len(face_res.detections) if face_res.detections else 0
        results = self._yolo(frame, classes=[0], conf=0.45, verbose=False)
        boxes = results[0].boxes
        return len(boxes) if boxes is not None else 0


def build_privacy_event(camera_id, person_count, occupied):
    """Aggregate-only occupancy event. Never contains identity or pose fields."""
    return {
        "camera_id": camera_id,
        "occupancy": "occupied" if occupied else "vacant",
        "person_count": int(person_count),
    }


def run_camera(camera_id, stream_url):
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(stream_url)
    last_event_time = 0.0
    last_summary_time = 0.0
    frame_no = 0

    # Per-track first-seen times (dwell time) and optional room calibration.
    first_seen = {}
    snapshots = {}
    calib = _load_room_calibration()

    # Per-camera allow-list: an absent backend/configuration means no identity
    # matching, even if an old local gallery happens to exist.
    camera_gallery = (CameraGallery(
        camera_id, GALLERY_BASE_URL, CONSENT_POLL_SECONDS)
        if FACE_ID_ENABLED and not PRIVACY_MODE else None)

    # Per-track identity cache (vision/identity_cache.py): identifies only
    # against the camera's current allow-list, re-verifies periodically, and
    # smooths names with majority voting. No unrestricted fallback exists.
    identity = None
    if _identity_cache is not None and camera_gallery is not None:
        identity = _identity_cache.IdentityCache(
            lambda frame, bbox: _identify_crop_scored(
                frame, bbox, gallery_entries=camera_gallery.entries))

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

            # Refresh active camera consent before any new identity lookup.
            # A change (including an error/revocation) clears sticky track
            # names so none can survive beyond the current authorization.
            if camera_gallery is not None:
                if camera_gallery.maybe_refresh(now) and identity is not None:
                    identity.reset()

            person_count = len(boxes) if boxes is not None else 0

            # Per-frame per-track bookkeeping: first-seen timestamps for
            # dwell time and an identity observation (identify on first sight,
            # then re-verify periodically; see vision/identity_cache.py).
            # Best-effort; failures here never stop the pipeline.
            if boxes is not None and boxes.id is not None:
                track_ids = boxes.id.cpu().tolist()
                track_boxes = boxes.xyxy.cpu().tolist()
                for tid, tbox in zip(track_ids, track_boxes):
                    track_key = f"Person {int(tid)}"
                    if track_key not in first_seen:
                        first_seen[track_key] = now
                    if identity is not None and camera_gallery.entries:
                        identity.observe(track_key, frame, tbox,
                                         frame_no, now)
                    elif identity is not None:
                        identity.reset()

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
                if camera_gallery is not None:
                    if camera_gallery.maybe_refresh(now, force=True) and identity is not None:
                        identity.reset()
                allowed_names = ({entry["name"].casefold()
                                  for entry in camera_gallery.entries}
                                 if camera_gallery is not None else set())
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
                    position_bounds = (
                        calib.get("room_size", (1.0, 1.0))
                        if isinstance(calib, dict) else (1.0, 1.0)
                    )

                    dwell_seconds = now - first_seen.get(track_key, now)

                    if identity is not None and track_key != "unknown":
                        person_name, name_conf = identity.identity(track_key)
                    elif camera_gallery is not None and camera_gallery.entries:
                        person_name, name_conf = _identify_crop_scored(
                            frame, bbox,
                            gallery_entries=camera_gallery.entries)
                    else:
                        person_name, name_conf = "unknown", None

                    if (not person_name
                            or person_name.casefold() not in allowed_names):
                        # Anonymous or unconsented tracks are never POSTed as
                        # person events and cannot create movement or alerts.
                        continue

                    event = _build_person_event(
                        camera_id, track_key, person_name, name_conf,
                        position_x, position_y,
                        None,
                        dwell_seconds, max_conf, position_bounds)

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
            person_count = detector.detect(frame)
            occupied, changed = debouncer.update(person_count, now)

            if changed:
                event = build_privacy_event(
                    camera_id, debouncer.pending_count or 0, occupied)

                try:
                    response = requests.post(
                        AGGREGATE_BACKEND_URL,
                        json=event,
                        timeout=2.0,
                        headers=_backend_headers(),
                    )
                    response.raise_for_status()
                    debouncer.acknowledge()
                    print(f"[{camera_id}] occupancy -> {event['occupancy']} "
                          f"(aggregate count={person_count})")

                except requests.RequestException as exc:
                    # Keep the transition pending; the next frame retries it.
                    print(f"[{camera_id}] Aggregate backend request failed: {exc}")

            elif now - last_summary_time >= HEADLESS_LOG_INTERVAL:
                print(f"[{camera_id}] privacy summary: aggregate_count={person_count}, "
                      f"occupied={debouncer.occupied}")
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
    print(f"[config] Face ID: {FACE_ID_ENABLED}, camera-loop snapshots: disabled, "
          f"room calibration: "
          f"{ROOM_CALIB_PATH or 'none'}")
    if not PRIVACY_MODE:
        print(f"[config] Camera consent gallery: "
              f"{GALLERY_BASE_URL or 'disabled'}"
              + (f" (refresh {CONSENT_POLL_SECONDS}s)"
                 if GALLERY_BASE_URL else " (identity unavailable)"))

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
