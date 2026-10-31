"""Automated unit and integration tests for Feature 4: Workstation objects and HUD overlay."""
import os
import sys
import numpy as np
import pytest
from ultralytics import YOLO

# Ensure vision/ is in sys.path
VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

from vision.association import (
    AssociationStatus,
    DetectedObject,
    PersonObjectAssociation,
    TrackedPerson,
)
from vision.activity_metrics import WorkstationState, WorkstationSummary
from vision.hud import render_workstation_overlay


def test_render_workstation_overlay_dimensions():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    person = TrackedPerson(track_id="1", box=[100, 100, 300, 500], conf=0.92)
    laptop = DetectedObject(class_name="laptop", class_id=63, box=[140, 320, 260, 420], conf=0.85)

    assoc = PersonObjectAssociation(
        object=laptop,
        assigned_track_id="1",
        status=AssociationStatus.INTERACTING,
        spatial_score=1.5,
        details="Interacting",
    )
    summary = WorkstationSummary(
        track_id="1",
        current_state=WorkstationState.INTERACTING,
        presence_seconds=12.5,
        interaction_seconds=10.0,
        idle_seconds=2.5,
        away_seconds=0.0,
        engagement_score=80.0,
        engagement_label="80.0% device engagement",
        associated_devices=["laptop"],
    )

    annotated = render_workstation_overlay(
        frame=frame,
        persons=[person],
        objects=[laptop],
        associations=[assoc],
        summaries={"1": summary},
        camera_id="CAM_TEST",
    )

    assert annotated.shape == (480, 640, 3)
    assert annotated.dtype == np.uint8
    # Overlay should not be all zeros since HUD and boxes were drawn
    assert np.any(annotated > 0)


def test_render_workstation_overlay_empty():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    annotated = render_workstation_overlay(
        frame=frame,
        persons=[],
        objects=[],
        associations=[],
        summaries={},
        camera_id="CAM_EMPTY",
    )

    assert annotated.shape == (480, 640, 3)
    assert np.any(annotated > 0)  # Empty HUD card header rendered
