"""Unit tests for camera configuration and input pipeline in vision/multicamera.py."""
import os
import sys
import tempfile
import threading
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Ensure vision/ is in sys.path
VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

import multicamera


def test_normalize_cameras_whitespace_and_device_indices():
    raw = {
        "CAM_01": "0",
        "CAM_02": " 1 ",
        "CAM_03": "http://127.0.0.1:8080/video",
        "CAM_04": " http://10.0.0.5:8080/video ",
    }
    normalized = multicamera._normalize_cameras(raw)
    assert normalized["CAM_01"] == 0
    assert normalized["CAM_02"] == 1
    assert normalized["CAM_03"] == "http://127.0.0.1:8080/video"
    assert normalized["CAM_04"] == "http://10.0.0.5:8080/video"


def test_load_cameras_from_wamss_camera_url(monkeypatch):
    monkeypatch.delenv("WAMSS_CAMERAS", raising=False)
    monkeypatch.setenv("WAMSS_CAMERA_URL", "http://127.0.0.1:8080/video")
    # Clean any numbered vars
    for k in list(os.environ.keys()):
        if k.startswith("WAMSS_CAM_") and k.endswith("_URL"):
            monkeypatch.delenv(k, raising=False)

    cams = multicamera._load_cameras()
    assert cams == {"CAM_01": "http://127.0.0.1:8080/video"}


def test_load_cameras_from_wamss_cameras_json(monkeypatch):
    monkeypatch.setenv(
        "WAMSS_CAMERAS",
        '{"CAM_FRONT": "http://127.0.0.1:8080/video", "CAM_DESK": "0"}',
    )
    cams = multicamera._load_cameras()
    assert cams["CAM_FRONT"] == "http://127.0.0.1:8080/video"
    assert cams["CAM_DESK"] == 0


def test_load_cameras_fallback_sample(monkeypatch):
    monkeypatch.delenv("WAMSS_CAMERAS", raising=False)
    monkeypatch.delenv("WAMSS_CAMERA_URL", raising=False)
    for k in list(os.environ.keys()):
        if k.startswith("WAMSS_CAM_") and k.endswith("_URL"):
            monkeypatch.delenv(k, raising=False)

    cams = multicamera._load_cameras()
    assert cams == multicamera.SAMPLE_CAMERAS


def test_load_env_file_does_not_override_existing(monkeypatch):
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write("TEST_CAM_VAR=from_file\nEXISTING_VAR=from_file\n")
        env_path = tf.name

    try:
        monkeypatch.setenv("EXISTING_VAR", "from_shell")
        monkeypatch.delenv("TEST_CAM_VAR", raising=False)

        multicamera._parse_and_set_env(env_path)
        assert os.environ.get("TEST_CAM_VAR") == "from_file"
        assert os.environ.get("EXISTING_VAR") == "from_shell"
    finally:
        os.unlink(env_path)


def test_run_camera_respects_stop_event():
    stop_event = threading.Event()
    stop_event.set()

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True

    with patch.object(multicamera.cv2, "VideoCapture", return_value=mock_cap):
        with patch.object(multicamera, "YOLO"):
            multicamera.run_camera("CAM_01", "http://127.0.0.1:8080/video", stop_event=stop_event)

    assert mock_cap.release.called


def test_run_camera_reconnects_after_failures():
    stop_event = threading.Event()

    # Sequence of reads: 12 failures (triggers reconnect), then stop
    call_count = 0

    class MockCap:
        def __init__(self, url):
            self.url = url
            self.released = False

        def isOpened(self):
            return True

        def read(self):
            nonlocal call_count
            call_count += 1
            if call_count >= 13:
                stop_event.set()
            return False, None

        def release(self):
            self.released = True

    caps = []

    def mock_vc(url):
        c = MockCap(url)
        caps.append(c)
        return c

    with patch.object(multicamera.cv2, "VideoCapture", side_effect=mock_vc):
        with patch.object(multicamera, "YOLO"):
            with patch("time.sleep"):  # skip actual sleep
                multicamera.run_camera("CAM_01", "http://127.0.0.1:8080/video", stop_event=stop_event)

    # Should have re-instantiated VideoCapture after 10 failures
    assert len(caps) >= 2
    assert caps[0].released is True
