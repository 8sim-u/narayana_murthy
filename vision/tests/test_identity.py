"""Identity tests for vision/identity_cache.py and the identity wiring in
vision/multicamera.py.

Two layers, both runnable without a camera and without insightface:

1. IdentityCache unit tests: the cache is pure Python, loaded straight
   from its file, with a stub identify function standing in for face_id.
2. Multicamera integration tests: multicamera.py is loaded from its file
   path with stubbed heavy deps (cv2, ultralytics, mediapipe, requests)
   in the same style as test_privacy.py, plus a scripted face_id stub and
   the real identity_cache module. run_camera() is driven with fake
   ByteTrack boxes until the YOLO stub raises to end the loop.

Synthetic numpy frames only: person 1's box region is filled with value
10 (the face stub reads it as Alice) and person 2's with value 200
(Bob), so multi-person naming is exercised end to end.
"""
import importlib.util
import os
import sys
import types

import numpy as np
import pytest

VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDENTITY_CACHE_PATH = os.path.join(VISION_DIR, "identity_cache.py")
MULTICAMERA_PATH = os.path.join(VISION_DIR, "multicamera.py")


# --------------------------------------------------------------------------
# IdentityCache unit tests (no stubs needed: pure stdlib module)
# --------------------------------------------------------------------------

def _load_identity_cache():
    spec = importlib.util.spec_from_file_location(
        "identity_cache_under_test", IDENTITY_CACHE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["identity_cache_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _scripted_identify(results):
    """identify_fn returning the next scripted (name, confidence) each call.

    results: list of (name, confidence); repeats the last entry forever.
    """
    calls = {"n": 0}

    def identify_fn(frame, bbox):
        idx = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[idx]

    identify_fn.calls = calls
    return identify_fn


def _bbox_identify(frame, bbox):
    """identify_fn naming by box position: left box is Alice, right is Bob."""
    x1 = bbox[0]
    return ("Alice", 0.92) if x1 < 50 else ("Bob", 0.88)


def test_first_vote_alone_does_not_name():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(_scripted_identify([("Alice", 0.9)]),
                             recheck_frames=30, recheck_seconds=5.0)
    # First sight: one vote is not enough agreement to assign a name.
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0) == \
        ("unknown", None)
    # Second agreeing vote (re-check due after 30 frames) names the track.
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 31, 100.0) == \
        ("Alice", pytest.approx(0.9))


def test_confidence_is_mean_of_winning_votes():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        _scripted_identify([("Alice", 0.9), ("Alice", 0.7)]),
        recheck_frames=1, recheck_seconds=5.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0)
    name, conf = cache.observe("Person 1", None, [0, 0, 10, 10], 2, 100.0)
    assert name == "Alice"
    assert conf == pytest.approx(0.8)


def test_single_bad_frame_does_not_flip():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        _scripted_identify([("Alice", 0.9), ("Alice", 0.9),
                            ("Bob", 0.9), ("Bob", 0.9)]),
        recheck_frames=1, recheck_seconds=5.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0)
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 2, 100.0)[0] \
        == "Alice"
    # One bad frame: still Alice.
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 3, 100.0)[0] \
        == "Alice"
    # A second bad frame ties the vote; the name sticks instead of
    # flickering to unknown or to Bob.
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 4, 100.0)[0] \
        == "Alice"


def test_majority_wins_over_time():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        _scripted_identify([("Alice", 0.9), ("Alice", 0.9),
                            ("Bob", 0.9), ("Bob", 0.9), ("Bob", 0.9)]),
        recheck_frames=1, recheck_seconds=5.0)
    for frame_no in (1, 2, 3, 4):
        cache.observe("Person 1", None, [0, 0, 10, 10], frame_no, 100.0)
    assert cache.identity("Person 1")[0] == "Alice"  # 2-2 tie keeps Alice
    # Third Bob vote: Bob holds 3 of 5 votes, a strict majority, so the
    # track flips to Bob.
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 5, 100.0)[0] \
        == "Bob"


def test_unknown_stays_unknown():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        _scripted_identify([("unknown", None)] * 5),
        recheck_frames=1, recheck_seconds=5.0)
    for frame_no in range(1, 6):
        assert cache.observe("Person 1", None, [0, 0, 10, 10],
                             frame_no, 100.0) == ("unknown", None)


def test_single_stray_name_never_names_unknown_track():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        _scripted_identify([("unknown", None), ("unknown", None),
                            ("Alice", 0.95), ("unknown", None),
                            ("unknown", None)]),
        recheck_frames=1, recheck_seconds=5.0)
    for frame_no in range(1, 6):
        name, conf = cache.observe("Person 1", None, [0, 0, 10, 10],
                                   frame_no, 100.0)
        assert name == "unknown"
        assert conf is None


def test_low_confidence_votes_do_not_name():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        _scripted_identify([("Alice", 0.3), ("Alice", 0.3)]),
        recheck_frames=1, recheck_seconds=5.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0)
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 2, 100.0) == \
        ("unknown", None)


def test_two_tracks_get_different_names():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(_bbox_identify,
                             recheck_frames=1, recheck_seconds=5.0)
    left = [10, 10, 50, 80]
    right = [60, 10, 100, 80]
    cache.observe("Person 1", None, left, 1, 100.0)
    cache.observe("Person 2", None, right, 1, 100.0)
    cache.observe("Person 1", None, left, 2, 100.0)
    cache.observe("Person 2", None, right, 2, 100.0)
    assert cache.identity("Person 1") == ("Alice", pytest.approx(0.92))
    assert cache.identity("Person 2") == ("Bob", pytest.approx(0.88))
    # Observing one track never changes the other's identity.
    cache.observe("Person 1", None, left, 3, 100.0)
    assert cache.identity("Person 2") == ("Bob", pytest.approx(0.88))


def test_recheck_cadence_frames():
    ic = _load_identity_cache()
    identify_fn = _scripted_identify([("Alice", 0.9)])
    cache = ic.IdentityCache(identify_fn,
                             recheck_frames=30, recheck_seconds=5.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0)
    assert identify_fn.calls["n"] == 1
    for frame_no in range(2, 31):
        cache.observe("Person 1", None, [0, 0, 10, 10], frame_no, 100.0)
    assert identify_fn.calls["n"] == 1  # no re-check inside 30 frames
    cache.observe("Person 1", None, [0, 0, 10, 10], 31, 100.0)
    assert identify_fn.calls["n"] == 2  # re-check due at 30 frames


def test_recheck_cadence_seconds():
    ic = _load_identity_cache()
    identify_fn = _scripted_identify([("Alice", 0.9)])
    cache = ic.IdentityCache(identify_fn,
                             recheck_frames=30, recheck_seconds=5.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 2, 101.0)
    assert identify_fn.calls["n"] == 1  # only 1 s and 1 frame elapsed
    cache.observe("Person 1", None, [0, 0, 10, 10], 3, 105.5)
    assert identify_fn.calls["n"] == 2  # 5 s elapsed: re-check due


def test_identify_exception_is_an_unknown_vote():
    ic = _load_identity_cache()

    def flaky(frame, bbox):
        if flaky.calls["n"] == 0:
            flaky.calls["n"] += 1
            raise RuntimeError("backend exploded")
        flaky.calls["n"] += 1
        return "Alice", 0.9

    flaky.calls = {"n": 0}
    cache = ic.IdentityCache(flaky, recheck_frames=1, recheck_seconds=5.0)
    # The exception must not escape observe(), and counts as unknown.
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0) == \
        ("unknown", None)
    # One good vote is not enough to name the track; the second is.
    cache.observe("Person 1", None, [0, 0, 10, 10], 2, 100.0)
    assert cache.identity("Person 1") == ("unknown", None)
    assert cache.observe("Person 1", None, [0, 0, 10, 10], 3, 100.0)[0] \
        == "Alice"


def test_stale_tracks_are_purged():
    ic = _load_identity_cache()
    cache = ic.IdentityCache(_scripted_identify([("Alice", 0.9)]),
                             recheck_frames=1, recheck_seconds=5.0,
                             stale_seconds=10.0)
    cache.observe("Person 1", None, [0, 0, 10, 10], 1, 100.0)
    assert len(cache) == 1
    # A sighting of another track 20 s later purges the stale one.
    cache.observe("Person 2", None, [0, 0, 10, 10], 1, 120.0)
    assert len(cache) == 1
    assert cache.identity("Person 1") == ("unknown", None)


# --------------------------------------------------------------------------
# Multicamera integration tests (stubbed heavy deps, real identity_cache)
# --------------------------------------------------------------------------

class _FakeScalar:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class _FakeRow:
    def __init__(self, row):
        self._row = row

    def cpu(self):
        return self

    def tolist(self):
        return list(self._row)


class _FakeTensor:
    """Minimal stand-in for a torch tensor: cpu().tolist(), [i], len()."""

    def __init__(self, data):
        self._data = list(data)

    def cpu(self):
        return self

    def tolist(self):
        return [list(v) if isinstance(v, list) else v for v in self._data]

    def __len__(self):
        return len(self._data)

    def __getitem__(self, idx):
        value = self._data[idx]
        if isinstance(value, list):
            return _FakeRow(value)
        return _FakeScalar(value)


class _FakeBoxes:
    """Stand-in for ultralytics Boxes: .id/.xyxy/.conf tensors, len()."""

    def __init__(self, ids, xyxy, conf):
        self.id = _FakeTensor(ids)
        self.xyxy = _FakeTensor(xyxy)
        self.conf = _FakeTensor(conf)

    def __len__(self):
        return len(self.xyxy)


class _StubVideoCapture:
    """Serves one synthetic frame per read, then raises to end the loop."""
    frames_to_serve = 3
    frame = None

    def __init__(self, url):
        self._reads = 0

    def isOpened(self):
        return True

    def read(self):
        self._reads += 1
        if self._reads > type(self).frames_to_serve:
            raise RuntimeError("test end")
        return True, type(self).frame.copy()

    def release(self):
        pass


class _ScriptedYOLO:
    """Serves one scripted Boxes per track() call, then raises."""
    boxes_seq = []

    def __init__(self, path):
        self._calls = 0

    def __call__(self, frame, **kwargs):
        return [types.SimpleNamespace(boxes=None)]

    def track(self, **kwargs):
        self._calls += 1
        if self._calls > len(type(self).boxes_seq):
            raise RuntimeError("test end")
        return [types.SimpleNamespace(
            boxes=type(self).boxes_seq[self._calls - 1])]


class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"decision": "ALLOW"}


class _FakeGalleryResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return [
            {"name": "Alice", "embedding": [1.0, 0.0]},
            {"name": "Bob", "embedding": [0.0, 1.0]},
        ]


def _make_cv2_stub(imwrite_log):
    cv2 = types.ModuleType("cv2")
    cv2.COLOR_BGR2RGB = 4
    cv2.FONT_HERSHEY_SIMPLEX = 0

    def cvtColor(frame, code):  # noqa: N802 - mirrors the cv2 API name
        return frame[..., ::-1].copy() if frame.ndim == 3 else frame

    def GaussianBlur(src, ksize, sigma):  # noqa: N802
        return np.full_like(src, int(src.mean()))

    def imwrite(path, frame):  # noqa: N802
        # Actually create the file so os.path.isfile() in the snapshot
        # helper sees it, and log the call for assertions.
        with open(path, "wb") as fh:
            fh.write(b"")
        imwrite_log.append((path, frame.copy()))
        return True

    cv2.cvtColor = cvtColor
    cv2.GaussianBlur = GaussianBlur
    cv2.imwrite = imwrite
    cv2.VideoCapture = _StubVideoCapture
    return cv2


def _make_ultralytics_stub():
    mod = types.ModuleType("ultralytics")
    mod.YOLO = _ScriptedYOLO
    return mod


def _make_requests_stub():
    req = types.ModuleType("requests")
    req.posts = []
    req.gets = []

    def post(url, json=None, timeout=None, headers=None):
        req.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    def get(url, params=None, timeout=None, headers=None):
        req.gets.append({"url": url, "params": params, "headers": headers})
        return _FakeGalleryResponse()

    req.post = post
    req.get = get
    return req


def _make_face_id_stub(mode):
    """Scripted face_id module.

    named: crop mean < 100 -> Alice 0.92, otherwise Bob 0.88.
    unknown: always ("unknown", None).
    forbidden: raises if called at all (privacy-mode tests).
    legacy: name-only identify(), no identify_with_confidence.
    """
    mod = types.ModuleType("face_id")
    mod.calls = []

    def identify_with_confidence(crop, gallery_entries=None):
        mean = float(crop.mean())
        mod.calls.append(mean)
        if mode == "named":
            name, confidence = ("Alice", 0.92) if mean < 100 else ("Bob", 0.88)
            allowed = {item.get("name") for item in (gallery_entries or [])}
            return (name, confidence) if name in allowed else ("unknown", None)
        if mode == "unknown":
            return "unknown", None
        if mode == "forbidden":
            raise AssertionError(
                "face_id must not be called in privacy mode")
        raise AssertionError(f"unknown stub mode {mode}")

    if mode == "legacy":
        def identify(crop):
            mod.calls.append(float(crop.mean()))
            return "Alice" if float(crop.mean()) < 100 else "Bob"

        mod.identify = identify
    else:
        mod.identify_with_confidence = identify_with_confidence
        mod.identify = lambda crop: identify_with_confidence(crop)[0]
    return mod


def _two_person_frame():
    """120x160 BGR frame: Person 1 region is value 10, Person 2 is 200."""
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    frame[10:80, 10:50] = 10
    frame[10:80, 60:100] = 200
    return frame


def _two_person_boxes():
    return _FakeBoxes(ids=[1, 2],
                      xyxy=[[10, 10, 50, 80], [60, 10, 100, 80]],
                      conf=[0.9, 0.85])


def _load_multicamera(monkeypatch, *, face_mode="named", frames=3,
                      snapshot_dir="", privacy=False, recheck_frames=1,
                      event_interval="0", face_id_enabled=True,
                      gallery_enabled=True,
                      boxes_seq=None, frame=None):
    """Load multicamera.py fresh with stubbed deps and a controlled env.

    Returns a SimpleNamespace with the module (mod), the requests stub
    (req), the imwrite log, the face_id stub (face_mod) and the real
    identity_cache module (ic_mod).
    """
    monkeypatch.setenv(
        "WAMSS_CAMERAS", '{"CAM_01": "http://192.0.2.10:8080/video"}')
    monkeypatch.setenv("WAMSS_PRIVACY_MODE", "1" if privacy else "0")
    monkeypatch.setenv("WAMSS_HEADLESS", "1")
    monkeypatch.setenv("WAMSS_FACE_ID", "1" if face_id_enabled else "0")
    monkeypatch.setenv("WAMSS_EVENT_INTERVAL", event_interval)
    monkeypatch.setenv("WAMSS_IDENTITY_RECHECK_FRAMES", str(recheck_frames))
    monkeypatch.setenv("WAMSS_IDENTITY_RECHECK_SECONDS", "5.0")
    monkeypatch.setenv("WAMSS_SNAPSHOT_DIR", snapshot_dir)
    if gallery_enabled:
        monkeypatch.setenv("WAMSS_BACKEND_URL", "http://localhost:8000/event")
    else:
        monkeypatch.delenv("WAMSS_BACKEND_URL", raising=False)
        monkeypatch.delenv("WAMSS_GALLERY_BASE_URL", raising=False)
    monkeypatch.delenv("WAMSS_ROOM_CALIB", raising=False)
    monkeypatch.delenv("WAMSS_PRIVACY_SNAPSHOT_DIR", raising=False)

    imwrite_log = []
    monkeypatch.setitem(sys.modules, "cv2", _make_cv2_stub(imwrite_log))
    monkeypatch.setitem(sys.modules, "ultralytics",
                        _make_ultralytics_stub())
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    req = _make_requests_stub()
    monkeypatch.setitem(sys.modules, "requests", req)
    face_mod = _make_face_id_stub(face_mode)
    monkeypatch.setitem(sys.modules, "face_id", face_mod)

    # The real identity_cache module, so multicamera's fallback
    # `import identity_cache` picks it up.
    monkeypatch.delitem(sys.modules, "identity_cache", raising=False)
    ic_spec = importlib.util.spec_from_file_location(
        "identity_cache", IDENTITY_CACHE_PATH)
    ic_mod = importlib.util.module_from_spec(ic_spec)
    monkeypatch.setitem(sys.modules, "identity_cache", ic_mod)
    ic_spec.loader.exec_module(ic_mod)

    _StubVideoCapture.frames_to_serve = frames
    _StubVideoCapture.frame = frame if frame is not None \
        else _two_person_frame()
    _ScriptedYOLO.boxes_seq = boxes_seq if boxes_seq is not None else []

    monkeypatch.delitem(sys.modules, "mc_identity_under_test",
                        raising=False)
    spec = importlib.util.spec_from_file_location(
        "mc_identity_under_test", MULTICAMERA_PATH)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "mc_identity_under_test", mod)
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, req=req, imwrite_log=imwrite_log,
                                 face_mod=face_mod, ic_mod=ic_mod)


# --------------------------------------------------------------------------
# Event payload helper and scored identify
# --------------------------------------------------------------------------

def test_build_person_event_payload(monkeypatch):
    loaded = _load_multicamera(monkeypatch)
    event = loaded.mod._build_person_event(
        "CAM_01", "Person 3", "Alice", 0.87, 0.25, 0.5,
        "snaps/Person_3_x.jpg", 12.5, 0.9)
    assert event == {
        "camera_id": "CAM_01",
        "track_id": "Person 3",
        "person_name": "Alice",
        "person_name_confidence": 0.87,
        "position_x": 0.25,
        "position_y": 0.5,
        "snapshot_path": "snaps/Person_3_x.jpg",
        "dwell_seconds": 12.5,
        "objects": ["person"],
        "confidence": 0.9,
        "zone": "UNKNOWN",
    }


def test_build_person_event_unknown_confidence_is_none(monkeypatch):
    loaded = _load_multicamera(monkeypatch)
    event = loaded.mod._build_person_event(
        "CAM_01", "Person 1", "unknown", None, 0.1, 0.2, None, 0.0, 0.9)
    assert event["person_name"] == "unknown"
    assert event["person_name_confidence"] is None


def test_identify_crop_scored_uses_face_id(monkeypatch):
    loaded = _load_multicamera(monkeypatch, face_mode="named")
    frame = _two_person_frame()
    assert loaded.mod._identify_crop_scored(
        frame, [10, 10, 50, 80]) == ("unknown", None)
    allowed = [
        {"name": "Alice", "embedding": [1.0]},
        {"name": "Bob", "embedding": [0.0]},
    ]
    assert loaded.mod._identify_crop_scored(
        frame, [10, 10, 50, 80], gallery_entries=allowed) == ("Alice", 0.92)
    assert loaded.mod._identify_crop_scored(
        frame, [60, 10, 100, 80], gallery_entries=allowed) == ("Bob", 0.88)
    # Degenerate crop: no identification attempted.
    assert loaded.mod._identify_crop_scored(
        frame, [50, 50, 50, 80], gallery_entries=allowed) == \
        ("unknown", None)


def test_identify_crop_scored_disabled_without_calling_face_id(monkeypatch):
    loaded = _load_multicamera(monkeypatch, face_mode="forbidden",
                               face_id_enabled=False)
    frame = _two_person_frame()
    assert loaded.mod._identify_crop_scored(frame, [10, 10, 50, 80]) == \
        ("unknown", None)
    assert loaded.face_mod.calls == []


def test_identify_crop_scored_legacy_face_id(monkeypatch):
    """A legacy name-only matcher is refused because it cannot be restricted."""
    loaded = _load_multicamera(monkeypatch, face_mode="legacy")
    frame = _two_person_frame()
    allowed = [{"name": "Alice", "embedding": [1.0]}]
    assert loaded.mod._identify_crop_scored(
        frame, [10, 10, 50, 80], gallery_entries=allowed) == ("unknown", None)
    assert loaded.face_mod.calls == []


# --------------------------------------------------------------------------
# run_camera() end to end (fake camera, fake tracker, stubbed backend)
# --------------------------------------------------------------------------

def test_run_camera_names_every_person(monkeypatch, tmp_path):
    snap_dir = tmp_path / "snaps"
    boxes = [_two_person_boxes() for _ in range(3)]
    loaded = _load_multicamera(
        monkeypatch, face_mode="named", frames=3,
        snapshot_dir=str(snap_dir), boxes_seq=boxes)
    loaded.mod.run_camera("CAM_01", "http://192.0.2.10:8080/video")

    posts = loaded.req.posts
    assert len(posts) == 6  # 3 frames x 2 persons, one event each
    assert len(loaded.face_mod.calls) == 6  # identified once per track/frame

    # Round 1: first sight, a single vote each, so both are still unknown.
    assert posts[0]["json"]["track_id"] == "Person 1"
    assert posts[0]["json"]["person_name"] == "unknown"
    assert posts[0]["json"]["person_name_confidence"] is None
    assert posts[1]["json"]["track_id"] == "Person 2"
    assert posts[1]["json"]["person_name"] == "unknown"
    assert posts[1]["json"]["person_name_confidence"] is None

    # Round 2: the second agreeing vote names both tracks, each with its
    # own confidence, and the names stick in round 3.
    assert posts[2]["json"]["person_name"] == "Alice"
    assert posts[2]["json"]["person_name_confidence"] == pytest.approx(0.92)
    assert posts[3]["json"]["person_name"] == "Bob"
    assert posts[3]["json"]["person_name_confidence"] == pytest.approx(0.88)
    assert posts[4]["json"]["person_name"] == "Alice"
    assert posts[5]["json"]["person_name"] == "Bob"

    # Positions come from the degraded fallback (normalized bottom-center
    # x, bbox-height depth proxy y), one per person.
    assert posts[2]["json"]["position_x"] == pytest.approx(30 / 160)
    assert posts[2]["json"]["position_y"] == pytest.approx(70 / 120)
    assert posts[3]["json"]["position_x"] == pytest.approx(80 / 160)
    assert posts[3]["json"]["position_y"] == pytest.approx(70 / 120)

    # YOLO detection confidence and dwell time are unchanged.
    assert posts[0]["json"]["confidence"] == pytest.approx(0.9)
    assert posts[0]["json"]["dwell_seconds"] >= 0
    assert posts[0]["json"]["zone"] == "UNKNOWN"

    # The camera loop does not write snapshots of people without consent.
    assert all(post["json"]["snapshot_path"] is None for post in posts)
    assert loaded.imwrite_log == []


def test_run_camera_unknown_stays_unknown_without_snapshot(monkeypatch,
                                                           tmp_path):
    snap_dir = tmp_path / "snaps"
    boxes = [_two_person_boxes() for _ in range(3)]
    loaded = _load_multicamera(
        monkeypatch, face_mode="unknown", frames=3,
        snapshot_dir=str(snap_dir), boxes_seq=boxes)
    loaded.mod.run_camera("CAM_01", "http://192.0.2.10:8080/video")

    posts = loaded.req.posts
    assert len(posts) == 6
    assert len(loaded.face_mod.calls) == 6  # identification was attempted
    for post in posts:
        event = post["json"]
        assert event["person_name"] == "unknown"
        assert event["person_name_confidence"] is None
        assert event["snapshot_path"] is None
    assert loaded.imwrite_log == []


def test_run_camera_does_not_identify_without_camera_gallery(monkeypatch):
    boxes = [_two_person_boxes() for _ in range(2)]
    loaded = _load_multicamera(
        monkeypatch, face_mode="named", frames=2, gallery_enabled=False,
        boxes_seq=boxes)
    loaded.mod.run_camera("CAM_01", "http://192.0.2.10:8080/video")

    assert loaded.face_mod.calls == []
    assert len(loaded.req.posts) == 4
    assert all(post["json"]["person_name"] == "unknown"
               for post in loaded.req.posts)


# --------------------------------------------------------------------------
# Privacy mode: no identification path may run
# --------------------------------------------------------------------------

def test_privacy_mode_never_identifies(monkeypatch, tmp_path):
    boxes = [_two_person_boxes() for _ in range(2)]
    loaded = _load_multicamera(
        monkeypatch, face_mode="forbidden", frames=2,
        snapshot_dir=str(tmp_path / "snaps"), boxes_seq=boxes,
        privacy=True)

    def _forbidden_cache(*args, **kwargs):
        raise AssertionError(
            "IdentityCache must not be constructed in privacy mode")

    monkeypatch.setattr(loaded.ic_mod, "IdentityCache", _forbidden_cache)
    loaded.mod.run_camera_privacy("CAM_01", "http://192.0.2.10:8080/video")

    assert loaded.face_mod.calls == []
    assert loaded.req.posts == []
    assert loaded.imwrite_log == []
