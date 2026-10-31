"""Automated unit tests for Feature 3: ByteTrack human tracking."""
import os
import sys
import numpy as np
import pytest
from ultralytics import YOLO

# Ensure vision/ is in sys.path
VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

import multicamera
import tracking


def test_bytetrack_initialization():
    model = YOLO("yolov8n.pt")
    assert model is not None


def test_bytetrack_blank_frame_has_no_tracks():
    model = YOLO("yolov8n.pt")
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    results = model.track(source=blank, persist=True, tracker="bytetrack.yaml", classes=[0], conf=0.45, verbose=False)
    boxes = results[0].boxes
    assert len(boxes) == 0
    assert boxes.id is None


def test_bytetrack_track_id_uniqueness():
    model = YOLO("yolov8n.pt")
    dummy = np.full((480, 640, 3), 128, dtype=np.uint8)
    results = model.track(source=dummy, persist=True, tracker="bytetrack.yaml", classes=[0], conf=0.45, verbose=False)
    boxes = results[0].boxes
    if boxes is not None and boxes.id is not None:
        ids = boxes.id.cpu().tolist()
        assert len(ids) == len(set(ids)), "Track IDs must be unique within a single frame"


def test_bytetrack_annotated_plot():
    model = YOLO("yolov8n.pt")
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    results = model.track(source=frame, persist=True, tracker="bytetrack.yaml", classes=[0], conf=0.45, verbose=False)
    plotted = results[0].plot()
    assert plotted.shape == (480, 640, 3)
    assert plotted.dtype == np.uint8
