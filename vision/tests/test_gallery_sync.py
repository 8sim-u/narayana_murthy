"""Regression checks for the deprecated, unscoped gallery refresher."""

import importlib.util
import os
import sys
import types


MULTICAMERA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "multicamera.py",
)


class _FakeFaceId:
    def __init__(self, gallery_dir):
        self.GALLERY_DIR = str(gallery_dir)
        self.reload_calls = 0

    def reload_gallery(self):
        self.reload_calls += 1


def _load_multicamera(monkeypatch, tmp_path):
    name = "mc_unscoped_gallery_disabled_test"
    monkeypatch.delitem(sys.modules, name, raising=False)

    cv2 = types.ModuleType("cv2")
    cv2.COLOR_BGR2RGB = 4
    cv2.FONT_HERSHEY_SIMPLEX = 0
    monkeypatch.setitem(sys.modules, "cv2", cv2)

    ultra = types.ModuleType("ultralytics")
    ultra.YOLO = lambda _path: None
    monkeypatch.setitem(sys.modules, "ultralytics", ultra)
    monkeypatch.setitem(sys.modules, "mediapipe", None)

    requests = types.ModuleType("requests")
    requests.post = lambda *args, **kwargs: None
    requests.get = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "requests", requests)

    gallery_dir = tmp_path / "faces"
    gallery_dir.mkdir(parents=True, exist_ok=True)
    fake_face_id = _FakeFaceId(gallery_dir)
    monkeypatch.setitem(sys.modules, "face_id", fake_face_id)

    monkeypatch.setenv(
        "WAMSS_CAMERAS", '{"CAM_01":"http://192.0.2.10:8080/video"}')
    monkeypatch.setenv("WAMSS_PRIVACY_MODE", "0")
    monkeypatch.setenv("WAMSS_FACE_ID", "1")
    monkeypatch.setenv("WAMSS_HEADLESS", "1")
    monkeypatch.setenv("WAMSS_GALLERY_BASE_URL", "http://backend:8000")
    monkeypatch.delenv("WAMSS_BACKEND_URL", raising=False)
    monkeypatch.delenv("WAMSS_ROOM_CALIB", raising=False)

    spec = importlib.util.spec_from_file_location(name, MULTICAMERA_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module, fake_face_id


def test_unscoped_gallery_refresher_is_inert(monkeypatch, tmp_path):
    module, fake_face_id = _load_multicamera(monkeypatch, tmp_path)
    calls = []

    def _unexpected_http(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unscoped gallery requests must stay disabled")

    refresher = module.GalleryRefresher(
        "http://backend:8000", 30.0, fake_face_id, http_get=_unexpected_http)

    assert refresher.enabled is False
    assert refresher.maybe_refresh(1000.0) is False
    assert calls == []
    assert fake_face_id.reload_calls == 0
    assert module._build_gallery_refresher() is None
