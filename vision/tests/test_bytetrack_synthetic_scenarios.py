"""Controlled automated unit and scenario tests for ByteTrack tracking.

NOTE: These are automated/synthetic detection tests designed to evaluate
multi-person tracking edge cases without requiring two physical people
in front of the webcam. They evaluate the project's actual BYTETracker
instance and configuration (bytetrack.yaml).
"""
import pytest
import torch
import numpy as np
from ultralytics import YOLO
from ultralytics.engine.results import Boxes


def _get_tracker():
    """Instantiate the project's real YOLO ByteTrack tracker instance."""
    model = YOLO("yolov8n.pt")
    dummy = np.zeros((480, 640, 3), dtype=np.uint8)
    _ = model.track(dummy, persist=True, tracker="bytetrack.yaml", classes=[0], verbose=False)
    tracker = model.predictor.trackers[0]
    tracker.reset()
    return tracker


def _make_boxes(detections):
    """Convert list of (x1, y1, x2, y2, conf, cls) into Boxes object."""
    if not detections:
        empty = torch.zeros((0, 6), dtype=torch.float32)
        return Boxes(empty, (480, 640)).numpy()
    tensor = torch.tensor(detections, dtype=torch.float32)
    return Boxes(tensor, (480, 640)).numpy()


def test_scenario_a_distinct_ids_for_two_simultaneous_people():
    """Scenario A: Two distinct people in the same frame must receive distinct track IDs."""
    tracker = _get_tracker()
    # Person 1 on left, Person 2 on right
    det_f1 = [
        [50., 100., 150., 350., 0.85, 0.],
        [400., 100., 500., 350., 0.90, 0.]
    ]
    tracked_1 = tracker.update(_make_boxes(det_f1))
    assert len(tracked_1) == 2, f"Expected 2 tracks, got {len(tracked_1)}"

    id_1 = int(tracked_1[0][4])
    id_2 = int(tracked_1[1][4])
    assert id_1 != id_2, f"Track IDs must be distinct, got {id_1} and {id_2}"


def test_scenario_b_trajectories_tracked_across_frames():
    """Scenario B: Two moving people across successive frames maintain their respective track IDs."""
    tracker = _get_tracker()
    # Step 1: Initial positions
    p1_x = 100.0
    p2_x = 400.0

    p1_history = []
    p2_history = []

    for t in range(15):
        # Both people move 2 pixels right each frame
        box1 = [p1_x + t * 2.0, 100.0, p1_x + t * 2.0 + 80.0, 300.0, 0.88, 0.]
        box2 = [p2_x + t * 2.0, 100.0, p2_x + t * 2.0 + 80.0, 300.0, 0.91, 0.]
        out = tracker.update(_make_boxes([box1, box2]))
        assert len(out) == 2, f"Frame {t}: expected 2 tracks, got {len(out)}"

        # Find which track belongs to p1 (x < 300) and p2 (x > 300)
        track_map = {int(row[4]): float(row[0]) for row in out}
        assert len(track_map) == 2, f"Frame {t}: duplicate track IDs found: {track_map}"

        p1_id = [tid for tid, x in track_map.items() if x < 300.0][0]
        p2_id = [tid for tid, x in track_map.items() if x >= 300.0][0]

        p1_history.append(p1_id)
        p2_history.append(p2_id)

    # Assert stable ID for person 1 and person 2 across all 15 frames
    assert len(set(p1_history)) == 1, f"Person 1 ID switched: {p1_history}"
    assert len(set(p2_history)) == 1, f"Person 2 ID switched: {p2_history}"
    assert p1_history[0] != p2_history[0], "Person 1 and Person 2 must have distinct IDs"


def test_scenario_c_temporary_disappearance_and_reappearance():
    """Scenario C: One person disappears while other stays -> visible ID stable, missing track re-acquired within buffer."""
    tracker = _get_tracker()
    # Frames 0-4: both present
    for t in range(5):
        box1 = [100.0 + t, 100.0, 180.0 + t, 300.0, 0.85, 0.]
        box2 = [400.0 + t, 100.0, 480.0 + t, 300.0, 0.90, 0.]
        out = tracker.update(_make_boxes([box1, box2]))
        p1_init_id = int([r[4] for r in out if r[0] < 300][0])
        p2_init_id = int([r[4] for r in out if r[0] >= 300][0])

    # Frames 5-14 (10 frames): Person 2 disappears, only Person 1 visible
    for t in range(5, 15):
        box1 = [100.0 + t, 100.0, 180.0 + t, 300.0, 0.85, 0.]
        out = tracker.update(_make_boxes([box1]))
        assert len(out) == 1, f"Frame {t}: expected only 1 track, got {len(out)}"
        assert int(out[0][4]) == p1_init_id, f"Frame {t}: Person 1 ID unstable"

    # Frame 15: Person 2 reappears at previous position (within track_buffer=30)
    box1 = [100.0 + 15, 100.0, 180.0 + 15, 300.0, 0.85, 0.]
    box2 = [400.0 + 15, 100.0, 480.0 + 15, 300.0, 0.90, 0.]
    out = tracker.update(_make_boxes([box1, box2]))
    assert len(out) == 2, f"Reappearance frame: expected 2 tracks, got {len(out)}"
    track_ids = [int(r[4]) for r in out]
    assert p1_init_id in track_ids, "Person 1 ID must persist"
    # Person 2 should retain ID via track_buffer
    assert p2_init_id in track_ids, f"Person 2 ID should be retained after 10 frames (buffer=30), got {track_ids}"


def test_scenario_d_long_disappearance_and_reappearance():
    """Scenario D: Both disappear beyond track_buffer (30 frames) -> lost tracks expire.

    When people reappear, ByteTrack confirms active tracks over 2 consecutive frames
    (preventing false-positive single-frame noise).
    """
    tracker = _get_tracker()
    # Establish tracks
    box1 = [100.0, 100.0, 180.0, 300.0, 0.85, 0.]
    box2 = [400.0, 100.0, 480.0, 300.0, 0.90, 0.]
    out = tracker.update(_make_boxes([box1, box2]))
    init_ids = {int(r[4]) for r in out}

    # 40 frames of empty scene (> track_buffer=30)
    for _ in range(40):
        out_empty = tracker.update(_make_boxes([]))
        assert len(out_empty) == 0

    # Tracks have expired; when people reappear, ByteTrack registers tentative tracks on frame 1
    # and confirms active tracks on frame 2
    tracker.update(_make_boxes([box1, box2]))
    out_confirmed = tracker.update(_make_boxes([box1, box2]))
    assert len(out_confirmed) == 2, f"Expected 2 confirmed tracks, got {len(out_confirmed)}"
    new_ids = {int(r[4]) for r in out_confirmed}
    assert len(new_ids) == 2, "Reappearing tracks must have distinct IDs"


def test_scenario_e_edge_cases_and_robustness():
    """Scenario E: Empty detections, low-confidence detections, single person handled safely."""
    tracker = _get_tracker()
    # 1. Empty detections
    out = tracker.update(_make_boxes([]))
    assert len(out) == 0

    # 2. Low-confidence detections (< track_low_thresh=0.1)
    low_conf = [[100.0, 100.0, 200.0, 300.0, 0.05, 0.]]
    out = tracker.update(_make_boxes(low_conf))
    assert len(out) == 0, "Detections with conf < 0.1 should not produce tracks"

    # 3. Single person (confirmed over 2 frames after frame_id > 1)
    single = [[100.0, 100.0, 200.0, 300.0, 0.88, 0.]]
    tracker.update(_make_boxes(single))
    out = tracker.update(_make_boxes(single))
    assert len(out) == 1
    assert int(out[0][4]) > 0


def test_scenario_f_update_integrity_and_no_duplicate_ids():
    """Scenario F: Tracker updates once per frame, no duplicate IDs for simultaneous tracks."""
    tracker = _get_tracker()
    dets = [
        [50., 50., 150., 200., 0.80, 0.],
        [200., 50., 300., 200., 0.85, 0.],
        [350., 50., 450., 200., 0.90, 0.],
    ]
    for frame_idx in range(5):
        out = tracker.update(_make_boxes(dets))
        assert len(out) == 3, f"Frame {frame_idx}: expected 3 tracks, got {len(out)}"
        ids = [int(r[4]) for r in out]
        assert len(ids) == len(set(ids)), f"Frame {frame_idx}: duplicate IDs assigned: {ids}"
