"""Controlled Synthetic Multi-Person Association Automated Test Suite.

Independently verifies the 7 scenarios required by Part 3 of the verification spec:
1. Two tracked people with separate laptops.
2. Two people with different nearby objects.
3. Two people crossing or changing positions.
4. One object positioned between two people.
5. An object with no clear associated person.
6. One person temporarily disappearing.
7. An object disappearing and reappearing.

NOTE: All tests in this module use controlled synthetic detections to evaluate
multi-person association algorithms, as physical live testing is constrained
to a single human operator.
"""

import pytest

from vision.association import (
    AssociationStatus,
    DetectedObject,
    PersonObjectAssociation,
    PersonObjectAssociator,
    TrackedPerson,
)
from vision.activity_metrics import WorkstationActivityTracker, WorkstationState


def test_scenario_1_two_people_separate_laptops():
    """Scenario 1: Two tracked people with separate laptops."""
    associator = PersonObjectAssociator()

    p1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 200.0, 400.0], conf=0.90)
    laptop1 = DetectedObject(class_name="laptop", class_id=63, box=[80.0, 260.0, 180.0, 360.0], conf=0.88)

    p2 = TrackedPerson(track_id="2", box=[450.0, 100.0, 600.0, 400.0], conf=0.92)
    laptop2 = DetectedObject(class_name="laptop", class_id=63, box=[470.0, 260.0, 570.0, 360.0], conf=0.85)

    assocs = associator.associate([p1, p2], [laptop1, laptop2])
    mapping = {a.object.box[0]: a for a in assocs}

    assert mapping[laptop1.box[0]].assigned_track_id == "1"
    assert mapping[laptop1.box[0]].status == AssociationStatus.INTERACTING
    assert mapping[laptop2.box[0]].assigned_track_id == "2"
    assert mapping[laptop2.box[0]].status == AssociationStatus.INTERACTING


def test_scenario_2_two_people_different_nearby_objects():
    """Scenario 2: Two people with different nearby objects."""
    associator = PersonObjectAssociator()

    # Person 1 has a phone nearby (desk side)
    p1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 200.0, 400.0], conf=0.90)
    phone1 = DetectedObject(class_name="cell phone", class_id=67, box=[210.0, 280.0, 250.0, 350.0], conf=0.78)

    # Person 2 has a desktop monitor (tv) and keyboard
    p2 = TrackedPerson(track_id="2", box=[450.0, 100.0, 600.0, 400.0], conf=0.92)
    monitor2 = DetectedObject(class_name="tv", class_id=62, box=[470.0, 120.0, 580.0, 280.0], conf=0.85)
    keyboard2 = DetectedObject(class_name="keyboard", class_id=66, box=[480.0, 320.0, 570.0, 380.0], conf=0.80)

    assocs = associator.associate([p1, p2], [phone1, monitor2, keyboard2])

    p1_items = [a.object.class_name for a in assocs if a.assigned_track_id == "1"]
    p2_items = [a.object.class_name for a in assocs if a.assigned_track_id == "2"]

    assert "cell phone" in p1_items
    assert "tv" in p2_items
    assert "keyboard" in p2_items


def test_scenario_3_two_people_crossing_positions():
    """Scenario 3: Two people crossing or changing positions."""
    associator = PersonObjectAssociator()

    desk1_laptop = DetectedObject(class_name="laptop", class_id=63, box=[80.0, 260.0, 180.0, 360.0], conf=0.88)
    desk2_laptop = DetectedObject(class_name="laptop", class_id=63, box=[470.0, 260.0, 570.0, 360.0], conf=0.85)

    # Frame 1: Person 1 at Desk 1, Person 2 at Desk 2
    p1_f1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 200.0, 400.0], conf=0.9)
    p2_f1 = TrackedPerson(track_id="2", box=[450.0, 100.0, 600.0, 400.0], conf=0.9)
    assocs_f1 = associator.associate([p1_f1, p2_f1], [desk1_laptop, desk2_laptop])
    map1 = {a.object.box[0]: a for a in assocs_f1}
    assert map1[desk1_laptop.box[0]].assigned_track_id == "1"
    assert map1[desk2_laptop.box[0]].assigned_track_id == "2"

    # Frame 2: Person 1 and 2 swap desks (Person 1 now at Desk 2, Person 2 at Desk 1)
    p1_f2 = TrackedPerson(track_id="1", box=[450.0, 100.0, 600.0, 400.0], conf=0.9)
    p2_f2 = TrackedPerson(track_id="2", box=[50.0, 100.0, 200.0, 400.0], conf=0.9)
    assocs_f2 = associator.associate([p1_f2, p2_f2], [desk1_laptop, desk2_laptop])
    map2 = {a.object.box[0]: a for a in assocs_f2}
    assert map2[desk1_laptop.box[0]].assigned_track_id == "2"
    assert map2[desk2_laptop.box[0]].assigned_track_id == "1"


def test_scenario_4_one_object_positioned_between_two_people():
    """Scenario 4: One object positioned between two people."""
    associator = PersonObjectAssociator()

    p1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 150.0, 400.0], conf=0.9)
    p2 = TrackedPerson(track_id="2", box=[490.0, 100.0, 590.0, 400.0], conf=0.9)

    # Object positioned right in the hallway midway between both (x=300..340)
    mid_device = DetectedObject(class_name="laptop", class_id=63, box=[300.0, 260.0, 340.0, 320.0], conf=0.80)

    assocs = associator.associate([p1, p2], [mid_device])
    assert len(assocs) == 1
    # Because distance is identical and no overlap exists, it must not be arbitrarily assigned
    assert assocs[0].assigned_track_id is None
    assert assocs[0].status in (AssociationStatus.UNASSOCIATED, AssociationStatus.AMBIGUOUS)


def test_scenario_5_object_with_no_clear_associated_person():
    """Scenario 5: An object with no clear associated person."""
    associator = PersonObjectAssociator()

    p1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 150.0, 400.0], conf=0.9)

    # Object placed in the far bottom-right corner across the room
    far_device = DetectedObject(class_name="laptop", class_id=63, box=[550.0, 400.0, 630.0, 470.0], conf=0.85)

    assocs = associator.associate([p1], [far_device])
    assert len(assocs) == 1
    assert assocs[0].assigned_track_id is None
    assert assocs[0].status == AssociationStatus.UNASSOCIATED


def test_scenario_6_one_person_temporarily_disappearing():
    """Scenario 6: One person temporarily disappearing."""
    tracker = WorkstationActivityTracker(min_presence_sec=2.0)
    associator = PersonObjectAssociator()

    p1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 200.0, 400.0], conf=0.9)
    p2 = TrackedPerson(track_id="2", box=[450.0, 100.0, 600.0, 400.0], conf=0.9)
    laptop2 = DetectedObject(class_name="laptop", class_id=63, box=[470.0, 260.0, 570.0, 360.0], conf=0.85)

    # Step A: Both people present at t=1000s to 1003s
    assocs_a = associator.associate([p1, p2], [laptop2])
    tracker.update(1000.0, [p1, p2], assocs_a)
    for sec in range(1, 4):
        tracker.update(1000.0 + float(sec), [p1, p2], assocs_a)

    s1_a = tracker.get_summary("1")
    s2_a = tracker.get_summary("2")
    assert s1_a.current_state == WorkstationState.PRESENT_NO_DEVICE
    assert s2_a.current_state == WorkstationState.INTERACTING

    # Step B: Person 1 steps out (absent from t=1004s to 1010s), Person 2 remains
    assocs_b = associator.associate([p2], [laptop2])
    for sec in range(4, 11):
        tracker.update(1000.0 + float(sec), [p2], assocs_b)

    s1_b = tracker.get_summary("1")
    s2_b = tracker.get_summary("2")
    assert s1_b.current_state == WorkstationState.AWAY
    assert s1_b.away_seconds >= 7.0
    assert s2_b.current_state == WorkstationState.INTERACTING
    assert s2_b.presence_seconds == pytest.approx(10.0)


def test_scenario_7_object_disappearing_and_reappearing():
    """Scenario 7: An object disappearing and reappearing."""
    associator = PersonObjectAssociator(buffer_size=5)

    p1 = TrackedPerson(track_id="1", box=[100.0, 100.0, 300.0, 500.0], conf=0.90)
    laptop = DetectedObject(class_name="laptop", class_id=63, box=[140.0, 320.0, 260.0, 420.0], conf=0.85)

    # Frame 1: Person with laptop
    assocs1 = associator.associate([p1], [laptop])
    assert assocs1[0].assigned_track_id == "1"
    assert assocs1[0].status == AssociationStatus.INTERACTING

    # Frame 2: Laptop occluded / missed detection for 1 frame
    assocs2 = associator.associate([p1], [])
    assert len(assocs2) == 0  # No objects detected in this frame

    # Frame 3: Laptop reappears
    assocs3 = associator.associate([p1], [laptop])
    assert len(assocs3) == 1
    assert assocs3[0].assigned_track_id == "1"
    assert assocs3[0].status == AssociationStatus.INTERACTING
