"""Synthetic re-identification tests for the WAMSS identity build.

This is the core promise of the identity work: the same enrolled face,
seen again under a NEW ByteTrack track id, must be recognised as the same
person. ByteTrack ids are temporary (they live only while a person stays
in view), so a returning person always arrives under a fresh track id;
the enrolled gallery is the only memory that survives that change.

These tests exercise the real path, with no camera and no insightface
installed (neither is available on this VM):

- the real vision/face_id.py cosine matcher, with its insightface backend
  stubbed at the _face_app boundary (a fake FaceAnalysis whose get()
  returns scripted normed embeddings), given an explicit camera-authorized
  gallery list;
- the real vision/identity_cache.py majority voter, fed by an identify
  function that calls the real face_id.identify_with_confidence;
- one multicamera-level test proving the crop path
  (multicamera._identify_crop_scored) feeds the real face_id module.

Embeddings here are short 4-d unit vectors instead of real 512-d ArcFace
vectors, so the cosine arithmetic is exact and checkable by hand.
"""
import importlib.util
import json
import math
import os
import sys
import types

import numpy as np
import pytest

VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_ID_PATH = os.path.join(VISION_DIR, "face_id.py")
IDENTITY_CACHE_PATH = os.path.join(VISION_DIR, "identity_cache.py")
MULTICAMERA_PATH = os.path.join(VISION_DIR, "multicamera.py")

ALICE_EMB = [1.0, 0.0, 0.0, 0.0]
BOB_EMB = [0.0, 1.0, 0.0, 0.0]
# Cosine 0.4 against ALICE_EMB: below the 0.5 match threshold.
WEAK_EMB = [0.4, math.sqrt(1.0 - 0.16), 0.0, 0.0]


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------

def _load_face_id(monkeypatch, tmp_path):
    """Load the real face_id.py with a stubbed insightface backend.

    Returns (module, fake_app). The fake app's embedding can be changed
    between calls via fake_app.embedding; setting it to None makes get()
    return no faces at all.
    """
    # `import cv2` must fail exactly as it does on a box without OpenCV.
    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.delitem(sys.modules, "insightface", raising=False)
    monkeypatch.setenv("WAMSS_FACE_SIM_THRESHOLD", "0.5")

    gallery_dir = tmp_path / "faces"
    gallery_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.delitem(sys.modules, "face_id_reid_under_test",
                        raising=False)
    spec = importlib.util.spec_from_file_location(
        "face_id_reid_under_test", FACE_ID_PATH)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "face_id_reid_under_test", mod)
    spec.loader.exec_module(mod)

    mod.GALLERY_DIR = str(gallery_dir)

    class _FakeFace:
        def __init__(self, embedding):
            self.bbox = [0, 0, 10, 10]
            self.normed_embedding = list(embedding)

    class _FakeFaceApp:
        def __init__(self):
            self.embedding = list(ALICE_EMB)

        def get(self, image):
            if self.embedding is None:
                return []
            return [_FakeFace(self.embedding)]

    fake_app = _FakeFaceApp()
    mod._backend = "insightface"
    mod._backend_ready = True
    mod._face_app = fake_app
    mod._gallery = None
    return mod, fake_app


def _enroll(face_id_mod, name, embedding):
    """Create a synthetic enrollment and return its authorized gallery item."""
    entry = {
        "name": name,
        "method": "insightface",
        "embedding": list(embedding),
        "enrolled_at": "2026-11-13T00:00:00Z",
    }
    safe = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    path = os.path.join(face_id_mod.GALLERY_DIR, safe + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entry, fh)
    return entry


def _load_identity_cache():
    spec = importlib.util.spec_from_file_location(
        "identity_cache_reid_under_test", IDENTITY_CACHE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["identity_cache_reid_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _crop():
    """A non-empty synthetic BGR crop; the fake backend ignores content."""
    return np.zeros((20, 20, 3), dtype=np.uint8)


def _observe_twice(cache, track_key, frame):
    """Two observations one frame apart (recheck every frame), returning
    the resolved identity after the second."""
    cache.observe(track_key, frame, [0, 0, 10, 10], 1, 100.0)
    return cache.observe(track_key, frame, [0, 0, 10, 10], 2, 100.0)


# --------------------------------------------------------------------------
# face_id level: matching is by embedding, not by track
# --------------------------------------------------------------------------


def test_matching_embedding_identifies(monkeypatch, tmp_path):
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    fake_app.embedding = list(ALICE_EMB)
    # A local sidecar by itself does not authorize identification.
    assert face_id_mod.identify_with_confidence(_crop()) == ("unknown", None)
    assert face_id_mod.identify_with_confidence(_crop(), gallery) == \
        ("Alice", pytest.approx(1.0))


def test_non_matching_embedding_stays_unknown(monkeypatch, tmp_path):
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    fake_app.embedding = list(BOB_EMB)  # orthogonal: cosine 0
    assert face_id_mod.identify_with_confidence(_crop(), gallery) == \
        ("unknown", None)


def test_below_threshold_embedding_stays_unknown(monkeypatch, tmp_path):
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    fake_app.embedding = list(WEAK_EMB)  # cosine 0.4 < 0.5 threshold
    assert face_id_mod.identify_with_confidence(_crop(), gallery) == \
        ("unknown", None)


def test_no_face_in_crop_stays_unknown(monkeypatch, tmp_path):
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    fake_app.embedding = None  # the backend found no face
    assert face_id_mod.identify_with_confidence(_crop(), gallery) == \
        ("unknown", None)

# --------------------------------------------------------------------------
# Re-identification across track ids (the core promise)
# --------------------------------------------------------------------------


def test_same_face_new_track_is_same_person(monkeypatch, tmp_path):
    """The same authorized identity is matched under a new track id."""
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        lambda frame, bbox: face_id_mod.identify_with_confidence(frame, gallery),
        recheck_frames=1, recheck_seconds=5.0)
    frame = _crop()
    fake_app.embedding = list(ALICE_EMB)
    assert _observe_twice(cache, "Person 1", frame) == \
        ("Alice", pytest.approx(1.0))
    assert _observe_twice(cache, "Person 7", frame) == \
        ("Alice", pytest.approx(1.0))
    assert cache.identity("Person 1") == ("Alice", pytest.approx(1.0))
    assert cache.identity("Person 7") == ("Alice", pytest.approx(1.0))


def test_non_matching_face_new_track_stays_unknown(monkeypatch, tmp_path):
    """A stranger under a new track id is not given an enrolled name."""
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        lambda frame, bbox: face_id_mod.identify_with_confidence(frame, gallery),
        recheck_frames=1, recheck_seconds=5.0)
    fake_app.embedding = list(BOB_EMB)
    assert _observe_twice(cache, "Person 3", _crop()) == ("unknown", None)
    assert cache.identity("Person 3") == ("unknown", None)


def test_reidentified_track_keeps_name_across_rechecks(monkeypatch, tmp_path):
    """A named track remains stable while an authorized match is transiently absent."""
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        lambda frame, bbox: face_id_mod.identify_with_confidence(frame, gallery),
        recheck_frames=1, recheck_seconds=5.0)
    frame = _crop()
    fake_app.embedding = list(ALICE_EMB)
    assert _observe_twice(cache, "Person 2", frame)[0] == "Alice"
    fake_app.embedding = None
    for frame_no in (3, 4, 5):
        name, conf = cache.observe("Person 2", frame, [0, 0, 10, 10],
                                   frame_no, 100.0)
        assert name == "Alice"
        assert conf == pytest.approx(1.0)


def test_authorized_gallery_update_names_new_person_on_new_track(monkeypatch, tmp_path):
    """A refreshed camera allow-list can authorize a new identity immediately."""
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    ic = _load_identity_cache()
    cache = ic.IdentityCache(
        lambda frame, bbox: face_id_mod.identify_with_confidence(frame, gallery),
        recheck_frames=1, recheck_seconds=5.0)
    frame = _crop()
    fake_app.embedding = list(ALICE_EMB)
    assert _observe_twice(cache, "Person 1", frame)[0] == "Alice"
    gallery.append(_enroll(face_id_mod, "Bob", BOB_EMB))
    fake_app.embedding = list(BOB_EMB)
    assert _observe_twice(cache, "Person 9", frame) == ("Bob", pytest.approx(1.0))
    assert cache.identity("Person 1") == ("Alice", pytest.approx(1.0))

# --------------------------------------------------------------------------
# Multicamera crop path feeds the real face_id module
# --------------------------------------------------------------------------

def _load_multicamera_with_real_face_id(monkeypatch, face_id_mod):
    """Load multicamera.py with stubbed heavy deps and the real face_id."""
    monkeypatch.setenv(
        "WAMSS_CAMERAS", '{"CAM_01": "http://192.0.2.10:8080/video"}')
    monkeypatch.setenv("WAMSS_PRIVACY_MODE", "0")
    monkeypatch.setenv("WAMSS_HEADLESS", "1")
    monkeypatch.setenv("WAMSS_FACE_ID", "1")
    monkeypatch.delenv("WAMSS_BACKEND_URL", raising=False)
    monkeypatch.delenv("WAMSS_GALLERY_BASE_URL", raising=False)
    monkeypatch.delenv("WAMSS_ROOM_CALIB", raising=False)

    cv2 = types.ModuleType("cv2")
    cv2.COLOR_BGR2RGB = 4
    cv2.FONT_HERSHEY_SIMPLEX = 0
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    ultra = types.ModuleType("ultralytics")

    class _DummyYOLO:
        def __init__(self, path):
            pass

    ultra.YOLO = _DummyYOLO
    monkeypatch.setitem(sys.modules, "ultralytics", ultra)
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    req = types.ModuleType("requests")
    req.post = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no backend posts in this test"))
    monkeypatch.setitem(sys.modules, "requests", req)
    monkeypatch.setitem(sys.modules, "face_id", face_id_mod)

    monkeypatch.delitem(sys.modules, "mc_reid_under_test", raising=False)
    spec = importlib.util.spec_from_file_location(
        "mc_reid_under_test", MULTICAMERA_PATH)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "mc_reid_under_test", mod)
    spec.loader.exec_module(mod)
    return mod



def test_multicamera_crop_path_reidentifies(monkeypatch, tmp_path):
    """The crop path matches only against an explicitly supplied camera gallery."""
    face_id_mod, fake_app = _load_face_id(monkeypatch, tmp_path)
    gallery = [_enroll(face_id_mod, "Alice", ALICE_EMB)]
    mod = _load_multicamera_with_real_face_id(monkeypatch, face_id_mod)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    fake_app.embedding = list(ALICE_EMB)
    assert mod._identify_crop_scored(
        frame, [10, 10, 50, 80], gallery_entries=gallery) == \
        ("Alice", pytest.approx(1.0))
    assert mod._identify_crop_scored(
        frame, [60, 10, 100, 80], gallery_entries=gallery) == \
        ("Alice", pytest.approx(1.0))
    fake_app.embedding = list(BOB_EMB)
    assert mod._identify_crop_scored(
        frame, [10, 10, 50, 80], gallery_entries=gallery) == ("unknown", None)
