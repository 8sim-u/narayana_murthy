"""
Unit tests for vision.dataset_capture.
"""

import itertools
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from vision.dataset_capture import (
    DatasetCapture,
    DatasetCaptureConfig,
    FrameDifferenceDetector,
)


def test_frame_difference_detector_identical():
    detector = FrameDifferenceDetector(min_motion_threshold=0.015)
    frame = np.ones((100, 100, 3), dtype=np.uint8) * 128

    # First frame always returns 1.0 (baseline)
    score1 = detector.compute_motion_score(frame)
    assert score1 == 1.0
    detector.update_reference(frame)

    # Identical frame returns 0.0 motion
    score2 = detector.compute_motion_score(frame)
    assert score2 == 0.0


def test_frame_difference_detector_distinct():
    detector = FrameDifferenceDetector(min_motion_threshold=0.015)
    frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
    frame2 = np.ones((100, 100, 3), dtype=np.uint8) * 255

    detector.compute_motion_score(frame1)
    detector.update_reference(frame1)

    score = detector.compute_motion_score(frame2)
    assert score == pytest.approx(1.0, rel=1e-2)


def test_dataset_capture_run_mocked(tmp_path: Path):
    config = DatasetCaptureConfig(
        stream_url="http://fake-stream:8080/video",
        camera_id="TEST_CAM",
        session_id="test_session",
        split="eval",
        output_dir=tmp_path,
        interval_sec=0.0,
        max_frames=3,
        duplicate_motion_threshold=0.001,
    )

    capture = DatasetCapture(config)

    # Generate 3 distinct dummy frames
    frames = [
        np.ones((240, 320, 3), dtype=np.uint8) * (i * 80)
        for i in range(1, 4)
    ]

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True

    # Yield frames then repeat (False, None)
    stream_iter = itertools.chain([(True, f) for f in frames], itertools.repeat((False, None)))
    mock_cap.read.side_effect = lambda: next(stream_iter)

    with patch.object(capture, "_open_capture", return_value=mock_cap):
        saved_count = capture.run()

    assert saved_count == 3

    images_dir = tmp_path / "eval" / "images"
    labels_dir = tmp_path / "eval" / "labels"
    manifest_path = tmp_path / "eval" / "manifest_test_session.json"

    assert images_dir.exists()
    assert labels_dir.exists()
    assert manifest_path.exists()

    saved_images = list(images_dir.glob("*.jpg"))
    saved_labels = list(labels_dir.glob("*.txt"))

    assert len(saved_images) == 3
    assert len(saved_labels) == 3

    # Check manifest contents
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["session_id"] == "test_session"
    assert manifest["frames_saved"] == 3
    assert len(manifest["frames"]) == 3
    assert manifest["frames"][0]["width"] == 320
    assert manifest["frames"][0]["height"] == 240
    assert "sha256" in manifest["frames"][0]


def test_dataset_capture_skips_duplicates(tmp_path: Path):
    config = DatasetCaptureConfig(
        stream_url="http://fake-stream:8080/video",
        camera_id="TEST_CAM",
        session_id="dup_session",
        split="dev",
        output_dir=tmp_path,
        interval_sec=0.0,
        max_duration_sec=0.05,
        duplicate_motion_threshold=0.05,
    )

    capture = DatasetCapture(config)

    # 1 unique frame followed by repeated identical frames
    unique_frame = np.ones((100, 100, 3), dtype=np.uint8) * 100
    identical_frame = np.ones((100, 100, 3), dtype=np.uint8) * 100

    stream_iter = itertools.chain([(True, unique_frame)], itertools.repeat((True, identical_frame)))
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.side_effect = lambda: next(stream_iter)

    with patch.object(capture, "_open_capture", return_value=mock_cap):
        saved_count = capture.run()

    # Only 1 unique frame should be saved
    assert saved_count == 1
    assert capture.skipped_duplicates >= 1
