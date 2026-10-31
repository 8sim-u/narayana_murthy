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
    from vision.workstation_objects import (
        CLASS_NAME_MAP,
        load_workstation_object_config,
    )
    from vision.association import (
        AssociationStatus,
        DetectedObject,
        PersonObjectAssociator,
        TrackedPerson,
    )
    from vision.activity_metrics import WorkstationActivityTracker
    from vision.hud import render_workstation_overlay
except ImportError:
    try:
        from workstation_objects import (
            CLASS_NAME_MAP,
            load_workstation_object_config,
        )
        from association import (
            AssociationStatus,
            DetectedObject,
            PersonObjectAssociator,
            TrackedPerson,
        )
        from activity_metrics import WorkstationActivityTracker
        from hud import render_workstation_overlay
    except ImportError:
        pass

try:
    # MediaPipe is OPTIONAL. It powers privacy-mode presence/pose (on-device)
    # and face blurring for anonymized snapshots. When absent, privacy mode
    # falls back to the YOLO aggregate path and refuses to store any frame.
    # The non-privacy path never needs it.
    import mediapipe as mp
except ImportError:
    mp = None


def _parse_and_set_env(env_path):
    """Parse a simple key=value .env file and set in os.environ without overriding."""
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as exc:
        print(f"[config] WARNING: could not read {env_path}: {exc}")


def _load_env_files():
    """Load .env files from project root, script directory, or current directory."""
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    seen = set()
    for path in candidates:
        norm = os.path.normpath(os.path.abspath(path))
        if norm not in seen and os.path.isfile(norm):
            seen.add(norm)
            _parse_and_set_env(norm)


_load_env_files()


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

    1. WAMSS_CAMERAS: a JSON object mapping camera id to stream URL, e.g.
       WAMSS_CAMERAS='{"CAM_01":"http://10.0.0.5:8080/video"}'
    2. Numbered vars: WAMSS_CAM_<id>_URL, e.g. WAMSS_CAM_01_URL=...
    3. Shorthand var: WAMSS_CAMERA_URL, e.g. WAMSS_CAMERA_URL=http://127.0.0.1:8080/video
    4. SAMPLE_CAMERAS: placeholder URLs only (never real cameras).
    """
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

    shorthand = os.environ.get("WAMSS_CAMERA_URL")
    if shorthand and shorthand.strip():
        print(f"[config] Camera from WAMSS_CAMERA_URL: CAM_01 -> {shorthand.strip()}")
        return _normalize_cameras({"CAM_01": shorthand.strip()})

    print("[config] WARNING: no cameras configured; using SAMPLE-ONLY "
          "placeholder URLs (192.0.2.x documentation addresses). Set "
          "WAMSS_CAMERAS or WAMSS_CAM_<id>_URL to run for real.")
    return dict(SAMPLE_CAMERAS)


def _normalize_cameras(cameras):
    """Convert all-digit camera URLs to int device indices and strip whitespace.

    cv2.VideoCapture("0") opens the literal file "0"; only the int form
    cv2.VideoCapture(0) addresses a local camera device. Accepting "0",
    "1", ... in WAMSS_CAMERAS lets a laptop's built-in webcam be used as
    e.g. WAMSS_CAMERAS='{"CAM_01":"0"}' without a wrapper script.
    """
    normalized = {}
    for cam_id, url in cameras.items():
        if isinstance(url, str):
            clean = url.strip()
            normalized[cam_id] = int(clean) if clean.isdigit() else clean
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

# Detection confidence threshold (default: 0.45)
CONF_THRESHOLD = _parse_float(os.environ.get("WAMSS_CONF_THRESHOLD"), 0.45)

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
        results = self._yolo(frame, classes=[0], conf=CONF_THRESHOLD, verbose=False)
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


def run_camera(camera_id, stream_url, stop_event=None):
    model = YOLO(MODEL_PATH)
    cfg = load_workstation_object_config()
    obj_conf = cfg["object_conf"]
    target_classes = cfg["target_classes"]
    obj_target_classes = [c for c in target_classes if c != 0]

    associator = PersonObjectAssociator()
    activity_tracker = WorkstationActivityTracker()

    cap = cv2.VideoCapture(stream_url)
    last_event_time = 0.0
    last_summary_time = 0.0
    consecutive_failures = 0
    max_consecutive_failures = 10
    reconnect_delay = 2.0

    while not cap.isOpened() and not (stop_event and stop_event.is_set()):
        print(f"[{camera_id}] WARNING: Cannot open camera stream ({stream_url}); retrying in {reconnect_delay}s...")
        time.sleep(reconnect_delay)
        cap.release()
        cap = cv2.VideoCapture(stream_url)

    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Camera stream could not be opened")
        cap.release()
        return

    print(f"[{camera_id}] Camera connected (headless={HEADLESS})")

    try:
        while not (stop_event and stop_event.is_set()):
            success, frame = cap.read()

            if not success or frame is None or getattr(frame, "size", 0) == 0:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    print(f"[{camera_id}] WARNING: Stream interrupted ({consecutive_failures} failed reads); reconnecting...")
                    cap.release()
                    time.sleep(reconnect_delay)
                    cap = cv2.VideoCapture(stream_url)
                    if cap.isOpened():
                        print(f"[{camera_id}] Camera reconnected successfully")
                        consecutive_failures = 0
                    else:
                        print(f"[{camera_id}] Reconnection attempt failed; will retry...")
                else:
                    if consecutive_failures == 1:
                        print(f"[{camera_id}] No frame received")
                    time.sleep(0.1)
                continue

            consecutive_failures = 0

            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=CONF_THRESHOLD,
                verbose=False,
            )

            person_boxes = results[0].boxes
            now = time.time()

            person_count = len(person_boxes) if person_boxes is not None else 0
            persons = []
            if person_count > 0:
                for idx in range(person_count):
                    track_id = (
                        str(int(person_boxes.id[idx].item()))
                        if (person_boxes.id is not None and idx < len(person_boxes.id))
                        else f"p{idx}"
                    )
                    conf = (
                        float(person_boxes.conf[idx].item())
                        if (person_boxes.conf is not None and idx < len(person_boxes.conf))
                        else 0.0
                    )
                    xyxy = [float(v) for v in person_boxes.xyxy[idx].tolist()]
                    persons.append(TrackedPerson(track_id=track_id, box=xyxy, conf=conf))

            # Detect workstation objects (laptops, phones, keyboards, mice, monitors, books)
            objects = []
            if obj_target_classes:
                obj_results = model.predict(
                    source=frame,
                    classes=obj_target_classes,
                    conf=obj_conf,
                    verbose=False,
                )
                obj_boxes = obj_results[0].boxes
                if obj_boxes is not None and len(obj_boxes) > 0:
                    for idx in range(len(obj_boxes)):
                        cid = int(obj_boxes.cls[idx].item())
                        cname = CLASS_NAME_MAP.get(cid, model.names.get(cid, f"obj_{cid}"))
                        conf = float(obj_boxes.conf[idx].item())
                        xyxy = [float(v) for v in obj_boxes.xyxy[idx].tolist()]
                        objects.append(DetectedObject(class_name=cname, class_id=cid, box=xyxy, conf=conf))

            # Perform spatial and temporal association
            associations = associator.associate(persons=persons, objects=objects)

            # Update workstation activity and engagement metrics
            summaries = activity_tracker.update(timestamp=now, persons=persons, associations=associations)

            # Send events to backend when person is detected
            if person_count > 0 and now - last_event_time >= EVENT_INTERVAL:
                for p in persons:
                    track_id = p.track_id
                    conf = p.conf

                    # Collect associated objects for this person
                    associated_objs = ["person"]
                    for a in associations:
                        if a.assigned_track_id == track_id and a.status in (
                            AssociationStatus.ASSOCIATED,
                            AssociationStatus.INTERACTING,
                            AssociationStatus.NEAR,
                        ):
                            if a.object.class_name not in associated_objs:
                                associated_objs.append(a.object.class_name)

                    zone = os.environ.get(f"WAMSS_{camera_id}_ZONE", os.environ.get("WAMSS_DEFAULT_ZONE", "RESTRICTED"))
                    event = {
                        "camera_id": camera_id,
                        "track_id": track_id,
                        "objects": associated_objs,
                        "confidence": conf,
                        "zone": zone,
                    }

                    try:
                        response = requests.post(
                            BACKEND_URL,
                            json=event,
                            timeout=2.0,
                            headers=_backend_headers(),
                        )
                        response.raise_for_status()

                        data = response.json()
                        p_summary = summaries.get(track_id)
                        state_val = p_summary.current_state.value if p_summary else "UNKNOWN"
                        print(
                            f"[{camera_id}] Person detected | "
                            f"Backend: {data.get('decision', 'UNKNOWN')} | "
                            f"Track: {track_id} | Objects: {associated_objs} | State: {state_val}"
                        )

                    except requests.RequestException as exc:
                        print(f"[{camera_id}] Backend request failed: {exc}")

                last_event_time = now
                last_summary_time = now

            elif HEADLESS and (person_count > 0 or len(objects) > 0) \
                    and now - last_summary_time >= HEADLESS_LOG_INTERVAL:
                track_ids = [p.track_id for p in persons]
                obj_names = [o.class_name for o in objects]
                primary_s = summaries.get(persons[0].track_id) if persons else None
                state_str = primary_s.current_state.value if primary_s else "NO_PERSON"
                eng_str = primary_s.engagement_label if primary_s else "N/A"
                print(
                    f"[{camera_id}] Headless summary: {person_count} person(s) (tracks: {track_ids}), "
                    f"objects: {obj_names} | State: {state_str} | {eng_str}"
                )
                last_summary_time = now

            if HEADLESS:
                continue

            # Windowed preview with rich HUD and annotations
            display = render_workstation_overlay(
                frame=frame,
                persons=persons,
                objects=objects,
                associations=associations,
                summaries=summaries,
                camera_id=camera_id,
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


def run_camera_privacy(camera_id, stream_url, stop_event=None):
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
    consecutive_failures = 0
    max_consecutive_failures = 10
    reconnect_delay = 2.0

    while not cap.isOpened() and not (stop_event and stop_event.is_set()):
        print(f"[{camera_id}] WARNING: Cannot open camera stream ({stream_url}); retrying in {reconnect_delay}s...")
        time.sleep(reconnect_delay)
        cap.release()
        cap = cv2.VideoCapture(stream_url)

    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Camera stream could not be opened")
        cap.release()
        return

    print(f"[{camera_id}] Camera connected (privacy mode, "
          f"backend={detector.backend}, "
          f"debounce={OCCUPANCY_DEBOUNCE_S}s)")

    try:
        while not (stop_event and stop_event.is_set()):
            success, frame = cap.read()

            if not success or frame is None or getattr(frame, "size", 0) == 0:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    print(f"[{camera_id}] WARNING: Stream interrupted ({consecutive_failures} failed reads); reconnecting...")
                    cap.release()
                    time.sleep(reconnect_delay)
                    cap = cv2.VideoCapture(stream_url)
                    if cap.isOpened():
                        print(f"[{camera_id}] Camera reconnected successfully")
                        consecutive_failures = 0
                    else:
                        print(f"[{camera_id}] Reconnection attempt failed; will retry...")
                else:
                    if consecutive_failures == 1:
                        print(f"[{camera_id}] No frame received")
                    time.sleep(0.1)
                continue

            consecutive_failures = 0

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

    stop_event = threading.Event()
    threads = []

    for camera_id, stream_url in CAMERAS.items():
        target = run_camera_privacy if PRIVACY_MODE else run_camera
        thread = threading.Thread(
            target=target,
            args=(camera_id, stream_url, stop_event),
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
        stop_event.set()

    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=1.0)
        if not HEADLESS:
            cv2.destroyAllWindows()
        print("WAMSS stopped")


if __name__ == "__main__":
    main()
