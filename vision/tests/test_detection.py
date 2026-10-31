"""Automated tests for Feature 2: YOLO human detection."""
import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from ultralytics import YOLO

# Ensure vision/ is in sys.path
VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

import multicamera
import detection


def test_confidence_threshold_configuration(monkeypatch):
    monkeypatch.setenv("WAMSS_CONF_THRESHOLD", "0.65")
    # Verify parsing in multicamera
    val = multicamera._parse_float(os.environ.get("WAMSS_CONF_THRESHOLD"), 0.45)
    assert val == 0.65


def test_yolo_model_initialization():
    model = YOLO("yolov8n.pt")
    assert model is not None
    # Verify COCO class 0 is 'person'
    assert model.names[0] == "person"


def test_yolo_zero_people_detection():
    model = YOLO("yolov8n.pt")
    # Clean black frame
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    results = model.predict(source=frame, classes=[0], conf=0.45, verbose=False)
    boxes = results[0].boxes
    assert len(boxes) == 0


def test_yolo_class_filtering():
    model = YOLO("yolov8n.pt")
    # Verify that requesting only class 0 returns only person detections
    dummy = np.zeros((480, 640, 3), dtype=np.uint8)
    results = model.predict(source=dummy, classes=[0], conf=0.45, verbose=False)
    if len(results[0].boxes) > 0:
        for cls_id in results[0].boxes.cls:
            assert int(cls_id) == 0


def test_yolo_bounding_box_and_plot():
    model = YOLO("yolov8n.pt")
    # Test plotting on synthetic frame
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    results = model.predict(source=frame, classes=[0], conf=0.45, verbose=False)
    plotted = results[0].plot()
    assert plotted.shape == (480, 640, 3)
    assert plotted.dtype == np.uint8
