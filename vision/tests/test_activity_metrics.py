"""Unit tests for Observable Workstation Activity and Engagement Metrics.

Covers all 6 conditions specified in Part 4 of the verification spec:
1. Person present near a workstation.
2. Person leaving the camera view.
3. Person returning to the workstation.
4. Device visible without a person.
5. Person visible without a detectable device.
6. Situations where interaction cannot be determined / missing frames gap handling.
"""

import pytest

from vision.association import (
    AssociationStatus,
    DetectedObject,
    PersonObjectAssociation,
    TrackedPerson,
)
from vision.activity_metrics import (
    WorkstationActivityTracker,
    WorkstationState,
)


def test_1_person_present_and_interacting():
    """Condition 1: Person present near a workstation and interacting with laptop."""
    tracker = WorkstationActivityTracker(min_presence_sec=3.0)

    t0 = 1000.0
    person = TrackedPerson(track_id="1", box=[100, 100, 300, 500], conf=0.9)
    laptop = DetectedObject(class_name="laptop", class_id=63, box=[140, 320, 260, 420], conf=0.85)

    assoc_interacting = PersonObjectAssociation(
        object=laptop,
        assigned_track_id="1",
        status=AssociationStatus.INTERACTING,
        spatial_score=1.5,
        details="",
    )

    # 4 seconds of interaction (at 1s intervals)
    for step in range(5):
        tracker.update(timestamp=t0 + float(step), persons=[person], associations=[assoc_interacting])

    summary = tracker.get_summary("1")
    assert summary is not None
    assert summary.presence_seconds == pytest.approx(4.0)
    assert summary.interaction_seconds == pytest.approx(4.0)
    assert summary.device_detected_seconds == pytest.approx(4.0)
    assert summary.current_state == WorkstationState.INTERACTING
    assert summary.engagement_score == pytest.approx(100.0)


def test_2_and_3_person_leaves_and_returns():
    """Conditions 2 & 3: Person leaving camera view and returning."""
    tracker = WorkstationActivityTracker(min_presence_sec=2.0)

    t0 = 1000.0
    person = TrackedPerson(track_id="1", box=[100, 100, 300, 500], conf=0.9)

    # Step A: Person present for 3 seconds
    tracker.update(timestamp=t0, persons=[person], associations=[])
    tracker.update(timestamp=t0 + 3.0, persons=[person], associations=[])

    s1 = tracker.get_summary("1")
    assert s1.current_state == WorkstationState.PRESENT_NO_DEVICE
    assert s1.presence_seconds == pytest.approx(2.0 if tracker.max_frame_gap_sec < 3.0 else 3.0)

    # Step B: Person leaves for 10 seconds
    tracker.update(timestamp=t0 + 13.0, persons=[], associations=[])
    s2 = tracker.get_summary("1")
    assert s2.current_state == WorkstationState.AWAY
    assert s2.away_seconds >= 10.0

    # Step C: Person returns at t0 + 15.0s
    tracker.update(timestamp=t0 + 15.0, persons=[person], associations=[])
    tracker.update(timestamp=t0 + 16.0, persons=[person], associations=[])
    s3 = tracker.get_summary("1")
    assert s3.current_state == WorkstationState.PRESENT_NO_DEVICE
    # Presence resumed accumulating
    assert s3.presence_seconds > s1.presence_seconds


def test_4_device_visible_without_person():
    """Condition 4: Device visible without a person."""
    tracker = WorkstationActivityTracker(min_presence_sec=2.0)

    t0 = 1000.0
    laptop = DetectedObject(class_name="laptop", class_id=63, box=[140, 320, 260, 420], conf=0.85)

    # Unassociated object (no person in view)
    unassoc = PersonObjectAssociation(
        object=laptop,
        assigned_track_id=None,
        status=AssociationStatus.UNASSOCIATED,
        spatial_score=0.0,
        details="No person",
    )

    summaries = tracker.update(timestamp=t0, persons=[], associations=[unassoc])
    assert len(summaries) == 0  # No persons tracked
    tracker.update(timestamp=t0 + 5.0, persons=[], associations=[unassoc])
    assert len(tracker.records) == 0


def test_5_person_visible_without_detectable_device():
    """Condition 5: Person visible without any detectable workstation devices."""
    tracker = WorkstationActivityTracker(min_presence_sec=2.0)

    t0 = 1000.0
    person = TrackedPerson(track_id="1", box=[100, 100, 300, 500], conf=0.9)

    # 4 seconds of person with zero objects
    for step in range(5):
        tracker.update(timestamp=t0 + float(step), persons=[person], associations=[])

    summary = tracker.get_summary("1")
    assert summary is not None
    assert summary.presence_seconds == pytest.approx(4.0)
    assert summary.current_state == WorkstationState.PRESENT_NO_DEVICE
    assert summary.engagement_score is None
    assert "no workstation devices" in summary.engagement_label.lower() or "insufficient" in summary.engagement_label.lower()


def test_6_missing_frames_do_not_inflate_metrics():
    """Condition 6: Missing frames / stream pause does not inflate active duration."""
    tracker = WorkstationActivityTracker(min_presence_sec=2.0, max_frame_gap_sec=1.5)

    t0 = 1000.0
    person = TrackedPerson(track_id="1", box=[100, 100, 300, 500], conf=0.9)

    tracker.update(timestamp=t0, persons=[person], associations=[])
    # Camera stream drops out or pauses for 60 seconds!
    tracker.update(timestamp=t0 + 60.0, persons=[person], associations=[])

    summary = tracker.get_summary("1")
    assert summary is not None
    # Presence should be capped at max_frame_gap_sec (1.5s), NOT 60s!
    assert summary.presence_seconds == pytest.approx(1.5)
    # The remainder (58.5s) is recorded as unobserved/gap duration
    assert summary.unobserved_seconds == pytest.approx(58.5)
