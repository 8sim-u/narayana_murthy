"""Privacy-mode tests for vision/multicamera.py.

Synthetic numpy frames only: no cameras, no model downloads. The heavy
optional deps (cv2, ultralytics, mediapipe) are stubbed through sys.modules
before the module under test is loaded from its file path.
"""
import importlib.util
import os
import sys
import types

import numpy as np
import pytest

MULTICAMERA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "multicamera.py",
)


# --------------------------------------------------------------------------
# Dependency stubs
# --------------------------------------------------------------------------

def _make_cv2_stub(imwrite_log):
    cv2 = types.ModuleType("cv2")
    cv2.COLOR_BGR2RGB = 4
    cv2.FONT_HERSHEY_SIMPLEX = 0

    def cvtColor(frame, code):  # noqa: N802 - mirrors the cv2 API name
        return frame[..., ::-1].copy() if frame.ndim == 3 else frame

    def GaussianBlur(src, ksize, sigma):  # noqa: N802
        # Deterministic stand-in for a blur: uniform mean colour, so a
        # non-uniform region is detectably different after "blurring".
        return np.full_like(src, int(src.mean()))

    def imwrite(path, frame):  # noqa: N802
        imwrite_log.append((path, frame.copy()))
        return True

    cv2.cvtColor = cvtColor
    cv2.GaussianBlur = GaussianBlur
    cv2.imwrite = imwrite
    return cv2


def _make_ultralytics_stub():
    mod = types.ModuleType("ultralytics")

    class YOLO:
        def __init__(self, path):
            self.path = path

        def __call__(self, frame, **kwargs):
            # No detections in the synthetic fallback path.
            return [types.SimpleNamespace(boxes=None)]

        def track(self, **kwargs):
            return [types.SimpleNamespace(boxes=None)]

    mod.YOLO = YOLO
    return mod


def _make_mediapipe_stub(detections):
    """Fake mediapipe with a scripted face-detection result list.

    detections: iterable of relative boxes as (xmin, ymin, w, h) tuples.
    """
    mp = types.ModuleType("mediapipe")
    solutions = types.ModuleType("mediapipe.solutions")
    fd = types.ModuleType("mediapipe.solutions.face_detection")
    pose_mod = types.ModuleType("mediapipe.solutions.pose")

    class _FaceDetection:
        def __init__(self, **kwargs):
            pass

        def process(self, rgb):
            dets = []
            for (xmin, ymin, w, h) in detections:
                box = types.SimpleNamespace(
                    xmin=xmin, ymin=ymin, width=w, height=h)
                dets.append(types.SimpleNamespace(
                    location_data=types.SimpleNamespace(
                        relative_bounding_box=box)))
            return types.SimpleNamespace(detections=dets)

    class _Pose:
        def __init__(self, **kwargs):
            pass

        def process(self, rgb):
            return types.SimpleNamespace(pose_landmarks=None)

    fd.FaceDetection = _FaceDetection
    pose_mod.Pose = _Pose
    solutions.face_detection = fd
    solutions.pose = pose_mod
    mp.solutions = solutions
    return mp


def _load_multicamera(monkeypatch, *, privacy, mediapipe_detections="absent",
                      imwrite_log=None):
    """Load multicamera.py fresh with stubbed deps and a controlled env.

    mediapipe_detections="absent" simulates mediapipe not being installed;
    otherwise it is a list of face boxes for the stub detector.
    """
    imwrite_log = imwrite_log if imwrite_log is not None else []
    module_name = "mc_under_test"
    sys.modules.pop(module_name, None)

    monkeypatch.setitem(sys.modules, "cv2", _make_cv2_stub(imwrite_log))
    monkeypatch.setitem(sys.modules, "ultralytics", _make_ultralytics_stub())
    if mediapipe_detections == "absent":
        # `import mediapipe` then resolves to None, exactly like the
        # except ImportError branch in the module.
        monkeypatch.setitem(sys.modules, "mediapipe", None)
    else:
        monkeypatch.setitem(
            sys.modules, "mediapipe",
            _make_mediapipe_stub(mediapipe_detections))

    if isinstance(privacy, bool):
        monkeypatch.setenv("WAMSS_PRIVACY_MODE", "1" if privacy else "0")
    else:
        # Raw string: exercises the real env parsing for odd values.
        monkeypatch.setenv("WAMSS_PRIVACY_MODE", privacy)
    monkeypatch.setenv(
        "WAMSS_CAMERAS", '{"CAM_01": "http://192.0.2.10:8080/video"}')
    monkeypatch.delenv("WAMSS_PRIVACY_SNAPSHOT_DIR", raising=False)

    spec = importlib.util.spec_from_file_location(
        module_name, MULTICAMERA_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _synthetic_frame():
    """120x160 BGR frame; face-sized region carries a gradient (non-uniform)."""
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:60, 0:80]
    frame[30:90, 40:120] = np.stack(
        [xx % 256, yy % 256, (xx + yy) % 256], axis=-1).astype(np.uint8)
    return frame


# --------------------------------------------------------------------------
# Env-flag parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("1", True),
    ("true", True),
    ("TRUE", True),
    ("yes", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("", False),
    ("banana", False),
])
def test_privacy_flag_parsing(monkeypatch, value, expected):
    mod = _load_multicamera(monkeypatch, privacy=value)
    assert mod.PRIVACY_MODE is expected


def test_privacy_flag_default_off(monkeypatch):
    mod = _load_multicamera(monkeypatch, privacy=False)
    assert mod.PRIVACY_MODE is False


def test_privacy_flag_on(monkeypatch):
    mod = _load_multicamera(monkeypatch, privacy=True)
    assert mod.PRIVACY_MODE is True


def test_occupancy_debounce_default(monkeypatch):
    monkeypatch.delenv("WAMSS_OCCUPANCY_DEBOUNCE_S", raising=False)
    mod = _load_multicamera(monkeypatch, privacy=True)
    assert mod.OCCUPANCY_DEBOUNCE_S == 5.0


# --------------------------------------------------------------------------
# Disk-write suppression
# --------------------------------------------------------------------------

def test_privacy_refuses_snapshot_without_mediapipe(monkeypatch):
    """Without mediapipe, privacy mode must NOT store the frame.

    cv2.imwrite is replaced with a raiser so any regression that calls it
    fails the test loudly.
    """
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")

    def _forbidden_imwrite(path, frame):
        raise AssertionError(
            "cv2.imwrite must not be called in privacy mode "
            "when mediapipe is absent")

    monkeypatch.setattr(sys.modules["cv2"], "imwrite", _forbidden_imwrite)
    stored = mod.save_frame_snapshot(_synthetic_frame(), "/tmp/snap.jpg")
    assert stored is False


def test_privacy_snapshot_blurs_faces_when_stored(monkeypatch):
    """With mediapipe, a stored snapshot must have its face region blurred."""
    imwrite_log = []
    mod = _load_multicamera(
        monkeypatch, privacy=True,
        mediapipe_detections=[(0.25, 0.25, 0.5, 0.5)],
        imwrite_log=imwrite_log)
    frame = _synthetic_frame()
    stored = mod.save_frame_snapshot(frame, "/tmp/snap.jpg")
    assert stored is True
    assert len(imwrite_log) == 1
    path, written = imwrite_log[0]
    assert path == "/tmp/snap.jpg"
    # Face region (rows 30:90, cols 40:120) was non-uniform; after blurring
    # it must differ from the original.
    assert not np.array_equal(written[30:90, 40:120], frame[30:90, 40:120])
    # Untouched corners must be identical.
    assert np.array_equal(written[0:10, 0:10], frame[0:10, 0:10])


def test_privacy_snapshot_no_faces_stores_copy(monkeypatch):
    imwrite_log = []
    mod = _load_multicamera(
        monkeypatch, privacy=True, mediapipe_detections=[],
        imwrite_log=imwrite_log)
    frame = _synthetic_frame()
    assert mod.save_frame_snapshot(frame, "/tmp/snap.jpg") is True
    assert len(imwrite_log) == 1
    assert np.array_equal(imwrite_log[0][1], frame)


def test_non_privacy_snapshot_writes_as_is(monkeypatch):
    imwrite_log = []
    mod = _load_multicamera(
        monkeypatch, privacy=False, mediapipe_detections="absent",
        imwrite_log=imwrite_log)
    frame = _synthetic_frame()
    assert mod.save_frame_snapshot(frame, "/tmp/snap.jpg") is True
    assert len(imwrite_log) == 1
    assert np.array_equal(imwrite_log[0][1], frame)


# --------------------------------------------------------------------------
# MediaPipe fallback
# --------------------------------------------------------------------------

def test_mediapipe_absent_falls_back_to_yolo(monkeypatch):
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")
    assert mod.mp is None
    detector = mod.PrivacyDetector()
    assert detector.backend == "yolo_fallback"
    count, pose = detector.detect(_synthetic_frame())
    assert count == 0
    assert pose == "unknown"


def test_mediapipe_present_backend(monkeypatch):
    mod = _load_multicamera(
        monkeypatch, privacy=True,
        mediapipe_detections=[(0.25, 0.25, 0.5, 0.5)])
    assert mod.mp is not None
    detector = mod.PrivacyDetector()
    assert detector.backend == "mediapipe"
    count, pose = detector.detect(_synthetic_frame())
    assert count == 1
    assert pose == "unknown"  # stub pose returns no landmarks


def test_mediapipe_present_no_faces(monkeypatch):
    mod = _load_multicamera(
        monkeypatch, privacy=True, mediapipe_detections=[])
    detector = mod.PrivacyDetector()
    count, pose = detector.detect(_synthetic_frame())
    assert count == 0
    assert pose == "unknown"


# --------------------------------------------------------------------------
# Identity / gaze / recognition guard
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [
    "face_recognition",
    "reidentification",
    "gaze_estimation",
    "person_reid",
    "face_embedding",
])
def test_identity_models_refused(monkeypatch, kind):
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")
    with pytest.raises(RuntimeError, match="forbidden in privacy mode"):
        mod._assert_privacy_model_allowed(kind)


@pytest.mark.parametrize("kind", ["person_pose", "face_detection_blur_only"])
def test_allowed_models_pass(monkeypatch, kind):
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")
    mod._assert_privacy_model_allowed(kind)  # must not raise


# --------------------------------------------------------------------------
# Occupancy debounce (5 s default)
# --------------------------------------------------------------------------

def test_occupancy_debounce(monkeypatch):
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")
    d = mod.OccupancyDebouncer(debounce_s=5.0)

    assert d.update(1, 0.0) == (False, False)     # streak starts
    assert d.update(1, 4.9) == (False, False)     # not yet sustained
    assert d.update(1, 5.0) == (True, True)       # flips to occupied
    assert d.update(1, 9.0) == (True, False)      # stays occupied
    assert d.update(0, 9.1) == (True, False)      # absence streak starts
    assert d.update(0, 14.0) == (True, False)     # not yet sustained
    assert d.update(0, 14.1) == (False, True)     # flips to vacant


def test_occupancy_brief_passby_ignored(monkeypatch):
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")
    d = mod.OccupancyDebouncer(debounce_s=5.0)
    assert d.update(1, 0.0) == (False, False)
    assert d.update(1, 2.0) == (False, False)
    assert d.update(0, 3.0) == (False, False)     # streak reset
    assert d.update(0, 10.0) == (False, False)
    assert d.occupied is False


# --------------------------------------------------------------------------
# Aggregate-only event payload
# --------------------------------------------------------------------------

def test_privacy_event_has_no_track_id(monkeypatch):
    mod = _load_multicamera(monkeypatch, privacy=True,
                            mediapipe_detections="absent")
    event = mod.build_privacy_event("CAM_01", 2, "standing", True)
    assert event == {
        "camera_id": "CAM_01",
        "occupancy": "occupied",
        "person_count": 2,
        "pose": "standing",
        "privacy_mode": True,
    }
    assert "track_id" not in event
    assert "boxes" not in event
