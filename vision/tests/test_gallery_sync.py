"""Gallery-sync tests for vision/multicamera.py.

The GalleryRefresher is how a running edge node picks up dashboard
enrollments without a restart: it polls GET /faces/gallery/version and,
when the version changes, re-fetches GET /faces/gallery, writes JSON
sidecars into vision/faces/, and calls face_id.reload_gallery().

These tests use synthetic data only: a fake face_id module backed by a
temporary directory, and an injected fake HTTP getter. No camera, no
network, no insightface.
"""
import importlib.util
import json
import os
import sys
import types

import pytest

MULTICAMERA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "multicamera.py",
)

_ALICE = {"name": "Alice", "embedding": [1.0, 0.0, 0.0, 0.0]}
_BOB = {"name": "Bob", "embedding": [0.0, 1.0, 0.0, 0.0]}


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class _FakeFaceId:
    """Stand-in for vision/face_id.py backed by a temp gallery dir."""

    def __init__(self, gallery_dir):
        self.GALLERY_DIR = str(gallery_dir)
        self.reload_calls = 0

    def reload_gallery(self):
        self.reload_calls += 1


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeHttp:
    """Scripted GET /faces/gallery/version and GET /faces/gallery.

    versions: list of version ints returned in order (last one repeats).
    galleries: list of gallery payloads returned in order (last repeats).
    Set fail_version=True to raise on every version call.
    Every call is recorded in self.calls as the request path.
    """

    def __init__(self, versions, galleries, fail_version=False):
        self._versions = list(versions)
        self._galleries = list(galleries)
        self._fail_version = fail_version
        self.calls = []
        self._version_i = 0
        self._gallery_i = 0

    def __call__(self, url, timeout=2.0, headers=None):
        path = url.split("http://backend:8000")[-1]
        self.calls.append(path)
        if path.endswith("/faces/gallery/version"):
            if self._fail_version:
                raise ConnectionError("backend down")
            idx = min(self._version_i, len(self._versions) - 1)
            self._version_i += 1
            return _FakeResponse({"version": self._versions[idx],
                                  "updated_at": "2026-11-13T00:00:00Z"})
        if path.endswith("/faces/gallery"):
            idx = min(self._gallery_i, len(self._galleries) - 1)
            self._gallery_i += 1
            return _FakeResponse(self._galleries[idx])
        raise AssertionError(f"unexpected gallery-sync URL: {url}")


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

_counter = [0]


def _load_multicamera(monkeypatch, tmp_path, *, privacy=False,
                      gallery_base="http://backend:8000",
                      poll_seconds="30", face_id_on=True):
    """Load multicamera.py fresh with stubbed heavy deps.

    Returns (module, fake_face_id). The fake face_id is the module-level
    _face_id, so _build_gallery_refresher uses it.
    """
    _counter[0] += 1
    name = f"mc_gallery_under_test_{_counter[0]}"
    monkeypatch.delitem(sys.modules, name, raising=False)

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
    req.get = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no real HTTP in this test"))
    monkeypatch.setitem(sys.modules, "requests", req)

    gallery_dir = tmp_path / "faces"
    gallery_dir.mkdir(parents=True, exist_ok=True)
    fake_face_id = _FakeFaceId(gallery_dir)
    monkeypatch.setitem(sys.modules, "face_id", fake_face_id)

    monkeypatch.setenv(
        "WAMSS_CAMERAS", '{"CAM_01": "http://192.0.2.10:8080/video"}')
    monkeypatch.setenv("WAMSS_PRIVACY_MODE", "1" if privacy else "0")
    monkeypatch.setenv("WAMSS_FACE_ID", "1" if face_id_on else "0")
    monkeypatch.setenv("WAMSS_HEADLESS", "1")
    monkeypatch.setenv("WAMSS_GALLERY_POLL_SECONDS", poll_seconds)
    if gallery_base is None:
        monkeypatch.delenv("WAMSS_GALLERY_BASE_URL", raising=False)
        monkeypatch.delenv("WAMSS_BACKEND_URL", raising=False)
    else:
        monkeypatch.setenv("WAMSS_GALLERY_BASE_URL", gallery_base)
        monkeypatch.delenv("WAMSS_BACKEND_URL", raising=False)
    monkeypatch.delenv("WAMSS_ROOM_CALIB", raising=False)

    spec = importlib.util.spec_from_file_location(name, MULTICAMERA_PATH)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod, fake_face_id


def _sidecars(fake_face_id):
    """{filename: parsed JSON} for every sidecar in the fake gallery."""
    out = {}
    for fname in sorted(os.listdir(fake_face_id.GALLERY_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(fake_face_id.GALLERY_DIR, fname),
                      encoding="utf-8") as fh:
                out[fname] = json.load(fh)
    return out


# --------------------------------------------------------------------------
# Refresher behaviour
# --------------------------------------------------------------------------

def test_version_change_syncs_gallery(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    http = _FakeHttp(versions=[3], galleries=[[_ALICE, _BOB]])
    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=http)
    assert refresher.enabled is True

    assert refresher.maybe_refresh(1000.0) is True
    assert fake_face_id.reload_calls == 1
    sidecars = _sidecars(fake_face_id)
    assert sorted(sidecars) == ["alice.json", "bob.json"]
    assert sidecars["alice.json"]["name"] == "Alice"
    assert sidecars["alice.json"]["embedding"] == [1.0, 0.0, 0.0, 0.0]
    assert sidecars["bob.json"]["name"] == "Bob"
    assert http.calls == ["/faces/gallery/version", "/faces/gallery"]


def test_same_version_skips_gallery_fetch(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    http = _FakeHttp(versions=[3, 3], galleries=[[_ALICE]])
    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=http)

    assert refresher.maybe_refresh(1000.0) is True
    assert refresher.maybe_refresh(1031.0) is False
    assert fake_face_id.reload_calls == 1
    # Second poll asked for the version only; the gallery was not fetched.
    assert http.calls == ["/faces/gallery/version",
                          "/faces/gallery",
                          "/faces/gallery/version"]


def test_poll_interval_is_respected(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    http = _FakeHttp(versions=[1], galleries=[[_ALICE]])
    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=http)

    assert refresher.maybe_refresh(1000.0) is True
    assert refresher.maybe_refresh(1029.0) is False
    assert http.calls == ["/faces/gallery/version", "/faces/gallery"]


def test_version_failure_keeps_local_gallery(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    http = _FakeHttp(versions=[1], galleries=[[_ALICE]], fail_version=True)
    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=http)

    assert refresher.maybe_refresh(1000.0) is False
    assert fake_face_id.reload_calls == 0
    assert _sidecars(fake_face_id) == {}

    # A failed poll does not advance the seen version: the next poll
    # retries and a healthy backend syncs normally.
    http._fail_version = False
    assert refresher.maybe_refresh(1031.0) is True
    assert fake_face_id.reload_calls == 1
    assert "alice.json" in _sidecars(fake_face_id)


def test_unmatchable_entries_are_skipped(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    gallery = [
        _ALICE,
        {"name": "NoFace", "embedding": None},
        {"name": "Empty", "embedding": []},
        {"name": "BadVector", "embedding": ["not", "numbers"]},
        "not-a-dict",
        {"embedding": [1.0, 0.0]},
    ]
    http = _FakeHttp(versions=[2], galleries=[gallery])
    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=http)

    assert refresher.maybe_refresh(1000.0) is True
    assert sorted(_sidecars(fake_face_id)) == ["alice.json"]


def test_stale_sidecars_removed_on_delete(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    http = _FakeHttp(versions=[1, 2], galleries=[[_ALICE, _BOB], [_ALICE]])
    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=http)

    assert refresher.maybe_refresh(1000.0) is True
    assert sorted(_sidecars(fake_face_id)) == ["alice.json", "bob.json"]

    # Bob was deleted on the dashboard: the version bump drops his sidecar.
    assert refresher.maybe_refresh(1031.0) is True
    assert sorted(_sidecars(fake_face_id)) == ["alice.json"]
    assert fake_face_id.reload_calls == 2


def test_gallery_fetch_failure_keeps_local_gallery(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)

    def _failing_gallery(url, timeout=2.0, headers=None):
        if url.endswith("/faces/gallery/version"):
            return _FakeResponse({"version": 5,
                                  "updated_at": "2026-11-13T00:00:00Z"})
        raise ConnectionError("gallery fetch down")

    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=_failing_gallery)
    assert refresher.maybe_refresh(1000.0) is False
    assert fake_face_id.reload_calls == 0
    assert _sidecars(fake_face_id) == {}


# --------------------------------------------------------------------------
# Disabled states
# --------------------------------------------------------------------------

def test_refresher_disabled_in_privacy_mode(monkeypatch, tmp_path):
    mod, fake_face_id = _load_multicamera(monkeypatch, tmp_path)

    def _raising_http(url, timeout=2.0, headers=None):
        raise AssertionError("privacy mode must perform no gallery fetch")

    refresher = mod.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id,
        http_get=_raising_http, privacy_mode=True)
    assert refresher.enabled is False
    assert refresher.maybe_refresh(1000.0) is False


def test_build_refresher_none_in_privacy_mode(monkeypatch, tmp_path):
    mod, _ = _load_multicamera(monkeypatch, tmp_path, privacy=True)
    assert mod.PRIVACY_MODE is True
    assert mod._build_gallery_refresher() is None


def test_build_refresher_none_without_base_url(monkeypatch, tmp_path):
    mod, _ = _load_multicamera(monkeypatch, tmp_path, gallery_base=None)
    assert mod.GALLERY_BASE_URL == ""
    assert mod._build_gallery_refresher() is None


def test_build_refresher_none_when_poll_zero(monkeypatch, tmp_path):
    mod, _ = _load_multicamera(monkeypatch, tmp_path, poll_seconds="0")
    assert mod.GALLERY_POLL_SECONDS == 0
    assert mod._build_gallery_refresher() is None


def test_build_refresher_none_when_face_id_off(monkeypatch, tmp_path):
    mod, _ = _load_multicamera(monkeypatch, tmp_path, face_id_on=False)
    assert mod.FACE_ID_ENABLED is False
    assert mod._build_gallery_refresher() is None


def test_build_refresher_present_when_configured(monkeypatch, tmp_path):
    mod, _ = _load_multicamera(monkeypatch, tmp_path)
    refresher = mod._build_gallery_refresher()
    assert refresher is not None
    assert refresher.enabled is True
    assert refresher.base_url == "http://backend:8000"
