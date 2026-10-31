"""
Spatial and Temporal Person-Object Association Engine for WAMSS Vision.

Associates detected workstation objects (laptops, phones, keyboards, mice,
monitors, books) with ByteTrack person IDs based on bounding box geometry,
Intersection-over-Object (IoO), proximity thresholds, and temporal smoothing.

Crucially distinguishes between:
  1. UNASSOCIATED: Object is distant or ambiguous.
  2. NEAR: Object is in proximity, but not in immediate workstation position.
  3. ASSOCIATED: Object is situated in the person's workstation space.
  4. INTERACTING: Active device operation observed/estimated via direct overlap
     and spatial positioning in the lap/desk interaction zone.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Dict, List, Optional, Tuple

from vision.workstation_objects import is_interactive_device


class AssociationStatus(str, Enum):
    UNASSOCIATED = "UNASSOCIATED"
    AMBIGUOUS = "AMBIGUOUS"
    NEAR = "NEAR"
    ASSOCIATED = "ASSOCIATED"
    INTERACTING = "INTERACTING"


@dataclass
class TrackedPerson:
    track_id: str
    box: List[float]  # [x1, y1, x2, y2]
    conf: float


@dataclass
class DetectedObject:
    class_name: str
    class_id: int
    box: List[float]  # [x1, y1, x2, y2]
    conf: float


@dataclass
class PersonObjectAssociation:
    object: DetectedObject
    assigned_track_id: Optional[str]
    status: AssociationStatus
    spatial_score: float
    details: str


def compute_box_center(box: List[float]) -> Tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def compute_box_area(box: List[float]) -> float:
    w = max(0.0, box[2] - box[0])
    h = max(0.0, box[3] - box[1])
    return w * h


def compute_intersection_area(b1: List[float], b2: List[float]) -> float:
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def compute_ioo(person_box: List[float], object_box: List[float]) -> float:
    """Intersection over Object Area (IoO)."""
    obj_area = compute_box_area(object_box)
    if obj_area <= 0.0:
        return 0.0
    inter = compute_intersection_area(person_box, object_box)
    return inter / obj_area


class PersonObjectAssociator:
    """Performs spatial association and temporal smoothing of objects to tracked persons."""

    def __init__(self, buffer_size: int = 8, max_norm_distance: float = 1.25):
        self.buffer_size = buffer_size
        self.max_norm_distance = max_norm_distance
        # History map: (object_class_id, rounded_x, rounded_y) -> deque of recent track_ids
        self._history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.buffer_size))

    def associate(
        self,
        persons: List[TrackedPerson],
        objects: List[DetectedObject],
    ) -> List[PersonObjectAssociation]:
        results: List[PersonObjectAssociation] = []

        if not objects:
            return results

        if not persons:
            # All objects unassociated when no persons are visible
            for obj in objects:
                results.append(
                    PersonObjectAssociation(
                        object=obj,
                        assigned_track_id=None,
                        status=AssociationStatus.UNASSOCIATED,
                        spatial_score=0.0,
                        details="No person detected in view",
                    )
                )
            return results

        for obj in objects:
            assoc = self._associate_single_object(obj, persons)
            results.append(assoc)

        return results

    def _associate_single_object(
        self,
        obj: DetectedObject,
        persons: List[TrackedPerson],
    ) -> PersonObjectAssociation:
        obj_center = compute_box_center(obj.box)
        candidates: List[Tuple[TrackedPerson, float, float, float]] = []

        for p in persons:
            pw = max(1.0, p.box[2] - p.box[0])
            ph = max(1.0, p.box[3] - p.box[1])
            person_scale = max(pw, ph)

            # Person desk reference point (lower 70% of person box)
            desk_center = (
                (p.box[0] + p.box[2]) / 2.0,
                p.box[1] + 0.70 * ph,
            )

            dx = obj_center[0] - desk_center[0]
            dy = obj_center[1] - desk_center[1]
            dist_pixels = math.hypot(dx, dy)
            norm_dist = dist_pixels / person_scale

            ioo = compute_ioo(p.box, obj.box)

            # Scoring: combination of IoO (overlap) and inverse normalized distance
            # Higher score = stronger association
            score = (ioo * 3.0) + max(0.0, 1.5 - norm_dist)
            candidates.append((p, norm_dist, ioo, score))

        # Sort by best score descending
        candidates.sort(key=lambda x: x[3], reverse=True)
        best_person, best_dist, best_ioo, best_score = candidates[0]

        # Check for unassociated distance cutoff:
        # If object is beyond max_norm_distance or has zero overlap and is beyond 1.0 person scale,
        # it is strictly UNASSOCIATED (ensuring distant objects are never falsely assigned).
        if best_dist > self.max_norm_distance or (best_ioo < 0.05 and best_dist > 1.0):
            return PersonObjectAssociation(
                object=obj,
                assigned_track_id=None,
                status=AssociationStatus.UNASSOCIATED,
                spatial_score=best_score,
                details=f"Distant object (norm_dist={best_dist:.2f} > cutoff)",
            )

        # Check for ambiguity between multiple persons
        if len(candidates) > 1:
            second_person, second_dist, second_ioo, second_score = candidates[1]
            # If distance to both is nearly identical and neither has meaningful overlap
            if abs(best_dist - second_dist) < 0.12 and best_ioo < 0.08 and second_ioo < 0.08:
                return PersonObjectAssociation(
                    object=obj,
                    assigned_track_id=None,
                    status=AssociationStatus.AMBIGUOUS,
                    spatial_score=best_score,
                    details=f"Ambiguous between Track {best_person.track_id} and {second_person.track_id}",
                )

        # Classify relationship level
        interactive = is_interactive_device(obj.class_id)
        status = AssociationStatus.ASSOCIATED

        # Interaction criteria:
        # 1. Must be an interactive device (laptop, keyboard, mouse, cell phone).
        # 2. Must have actual visual overlap with the person (IoO >= 0.10) to confirm interaction/holding/typing.
        # Proximity alone (even close norm_dist) without overlap classifies as ASSOCIATED or NEAR,
        # ensuring spatial proximity is not conflated with active device operation.
        if interactive:
            if best_ioo >= 0.10:
                status = AssociationStatus.INTERACTING
            elif best_dist <= 0.85:
                status = AssociationStatus.ASSOCIATED
            else:
                status = AssociationStatus.NEAR
        else:
            if best_dist <= 0.85 or best_ioo >= 0.15:
                status = AssociationStatus.ASSOCIATED
            else:
                status = AssociationStatus.NEAR

        # Temporal smoothing key
        obj_key = f"{obj.class_id}_{round(obj_center[0] / 30.0)}_{round(obj_center[1] / 30.0)}"
        self._history[obj_key].append(best_person.track_id)

        return PersonObjectAssociation(
            object=obj,
            assigned_track_id=best_person.track_id,
            status=status,
            spatial_score=round(best_score, 2),
            details=f"Assoc Track {best_person.track_id} (ioo={best_ioo:.2f}, norm_dist={best_dist:.2f})",
        )
