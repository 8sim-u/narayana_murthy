import pytest
from vision.association import (
    PersonObjectAssociator,
    TrackedPerson,
    DetectedObject,
    AssociationStatus,
)


def test_single_person_interacting_laptop():
    associator = PersonObjectAssociator()

    person = TrackedPerson(
        track_id="1",
        box=[100.0, 100.0, 300.0, 500.0],  # W=200, H=400
        conf=0.90,
    )
    laptop = DetectedObject(
        class_name="laptop",
        class_id=63,
        box=[140.0, 320.0, 260.0, 420.0],  # Directly in lower lap/desk zone of person
        conf=0.85,
    )

    associations = associator.associate(persons=[person], objects=[laptop])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.object == laptop
    assert assoc.assigned_track_id == "1"
    assert assoc.status == AssociationStatus.INTERACTING


def test_single_person_nearby_phone_not_interacting():
    associator = PersonObjectAssociator()

    person = TrackedPerson(
        track_id="1",
        box=[100.0, 100.0, 300.0, 500.0],
        conf=0.90,
    )
    # Phone placed to the side on desk table, not overlapping person's body
    phone = DetectedObject(
        class_name="cell phone",
        class_id=67,
        box=[310.0, 350.0, 350.0, 420.0],
        conf=0.75,
    )

    associations = associator.associate(persons=[person], objects=[phone])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.assigned_track_id == "1"
    # Near/Associated, but crucially NOT INTERACTING because proximity != usage
    assert assoc.status in (AssociationStatus.NEAR, AssociationStatus.ASSOCIATED)
    assert assoc.status != AssociationStatus.INTERACTING


def test_distant_object_remains_unassociated():
    associator = PersonObjectAssociator()

    person = TrackedPerson(
        track_id="1",
        box=[50.0, 50.0, 150.0, 300.0],
        conf=0.90,
    )
    # Laptop placed far across the room on the opposite side of camera
    distant_laptop = DetectedObject(
        class_name="laptop",
        class_id=63,
        box=[550.0, 380.0, 630.0, 470.0],
        conf=0.80,
    )

    associations = associator.associate(persons=[person], objects=[distant_laptop])

    assert len(associations) == 1
    assoc = associations[0]
    assert assoc.assigned_track_id is None
    assert assoc.status == AssociationStatus.UNASSOCIATED


def test_multi_person_disambiguation_synthetic():
    associator = PersonObjectAssociator()

    person1 = TrackedPerson(track_id="1", box=[50.0, 100.0, 200.0, 400.0], conf=0.88)
    person2 = TrackedPerson(track_id="2", box=[420.0, 100.0, 570.0, 400.0], conf=0.91)

    laptop1 = DetectedObject(class_name="laptop", class_id=63, box=[70.0, 260.0, 160.0, 340.0], conf=0.85)
    laptop2 = DetectedObject(class_name="laptop", class_id=63, box=[440.0, 260.0, 530.0, 340.0], conf=0.82)

    associations = associator.associate(persons=[person1, person2], objects=[laptop1, laptop2])

    assoc_map = {a.object.box[0]: a for a in associations}
    assert assoc_map[laptop1.box[0]].assigned_track_id == "1"
    assert assoc_map[laptop2.box[0]].assigned_track_id == "2"


def test_ambiguous_equidistant_object_remains_unassociated():
    associator = PersonObjectAssociator()

    # Two people equidistant from an object in the middle
    person1 = TrackedPerson(track_id="1", box=[100.0, 100.0, 200.0, 400.0], conf=0.9)
    person2 = TrackedPerson(track_id="2", box=[400.0, 100.0, 500.0, 400.0], conf=0.9)

    # Object precisely centered in the hallway/gap between them with no overlap
    middle_item = DetectedObject(class_name="laptop", class_id=63, box=[280.0, 250.0, 320.0, 320.0], conf=0.8)

    associations = associator.associate(persons=[person1, person2], objects=[middle_item])

    assert len(associations) == 1
    # Because distance to both is virtually identical and no overlap exists, cannot arbitrarily assign
    assert associations[0].assigned_track_id is None or associations[0].status == AssociationStatus.AMBIGUOUS


def test_temporal_smoothing_maintains_association():
    associator = PersonObjectAssociator(buffer_size=5)

    person = TrackedPerson(track_id="1", box=[100.0, 100.0, 300.0, 500.0], conf=0.90)
    laptop = DetectedObject(class_name="laptop", class_id=63, box=[140.0, 320.0, 260.0, 420.0], conf=0.85)

    # Feed 3 consecutive frames with person and laptop
    for _ in range(3):
        associator.associate([person], [laptop])

    # In frame 4, laptop coordinates slightly jitter or brief detection gap
    jitter_laptop = DetectedObject(class_name="laptop", class_id=63, box=[145.0, 325.0, 265.0, 425.0], conf=0.80)
    res = associator.associate([person], [jitter_laptop])

    assert res[0].assigned_track_id == "1"
    assert res[0].status == AssociationStatus.INTERACTING
