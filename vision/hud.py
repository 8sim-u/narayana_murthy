"""
Visual HUD and Annotation Overlay for WAMSS Vision.

Renders clear, intuitive bounding boxes, association tags, and an on-screen
workstation activity HUD over OpenCV frames.
"""

from typing import Dict, List, Optional
import cv2
import numpy as np

from vision.activity_metrics import WorkstationState, WorkstationSummary
from vision.association import (
    AssociationStatus,
    DetectedObject,
    PersonObjectAssociation,
    TrackedPerson,
)

# Colors in BGR format
COLOR_PERSON = (0, 220, 0)          # Green
COLOR_INTERACTING = (0, 255, 255)    # Yellow / Bright Amber
COLOR_ASSOCIATED = (255, 180, 0)     # Sky Blue
COLOR_NEAR = (255, 100, 0)           # Blue
COLOR_UNASSOCIATED = (160, 160, 160) # Grey
COLOR_HUD_BG = (20, 20, 20)          # Dark background
COLOR_TEXT = (255, 255, 255)         # White


def render_workstation_overlay(
    frame: np.ndarray,
    persons: List[TrackedPerson],
    objects: List[DetectedObject],
    associations: List[PersonObjectAssociation],
    summaries: Dict[str, WorkstationSummary],
    camera_id: str = "CAM_01",
) -> np.ndarray:
    """Draws bounding boxes, association badges, and workstation activity HUD on frame."""
    canvas = frame.copy()
    h, w = canvas.shape[:2]

    # Map object identity to its association result
    obj_to_assoc: Dict[int, PersonObjectAssociation] = {}
    for assoc in associations:
        obj_to_assoc[id(assoc.object)] = assoc

    # 1. Draw Object bounding boxes
    for obj in objects:
        assoc = obj_to_assoc.get(id(obj))
        box = [int(v) for v in obj.box]

        if assoc and assoc.status == AssociationStatus.INTERACTING:
            color = COLOR_INTERACTING
            status_text = f"Track {assoc.assigned_track_id} [INTERACTING]"
        elif assoc and assoc.status == AssociationStatus.ASSOCIATED:
            color = COLOR_ASSOCIATED
            status_text = f"Track {assoc.assigned_track_id} [DESK]"
        elif assoc and assoc.status == AssociationStatus.NEAR:
            color = COLOR_NEAR
            status_text = f"Track {assoc.assigned_track_id} [NEAR]"
        else:
            color = COLOR_UNASSOCIATED
            status_text = "[UNASSOCIATED]"

        cv2.rectangle(canvas, (box[0], box[1]), (box[2], box[3]), color, 2)
        label = f"{obj.class_name} {obj.conf:.2f} -> {status_text}"
        cv2.putText(
            canvas,
            label,
            (box[0], max(15, box[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    # 2. Draw Person bounding boxes with Track IDs
    for p in persons:
        box = [int(v) for v in p.box]
        summary = summaries.get(p.track_id)
        state_label = summary.current_state.value if summary else "DETECTED"
        devs = ", ".join(summary.associated_devices) if summary and summary.associated_devices else "none"

        cv2.rectangle(canvas, (box[0], box[1]), (box[2], box[3]), COLOR_PERSON, 2)
        person_tag = f"ID: {p.track_id} ({p.conf:.2f}) | {state_label} | Devs: {devs}"
        cv2.putText(
            canvas,
            person_tag,
            (box[0], max(20, box[1] - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            COLOR_PERSON,
            2,
            cv2.LINE_AA,
        )

    # 3. Draw On-Screen Activity HUD Card
    hud_w, hud_h = 360, 140
    hud_x, hud_y = 15, 15

    # Draw semi-transparent background overlay
    sub_img = canvas[hud_y : hud_y + hud_h, hud_x : hud_x + hud_w]
    if sub_img.shape[0] == hud_h and sub_img.shape[1] == hud_w:
        rect = np.full(sub_img.shape, 25, dtype=np.uint8)
        res = cv2.addWeighted(sub_img, 0.25, rect, 0.75, 1.0)
        canvas[hud_y : hud_y + hud_h, hud_x : hud_x + hud_w] = res
        cv2.rectangle(canvas, (hud_x, hud_y), (hud_x + hud_w, hud_y + hud_h), (80, 80, 80), 1)

    # Populate HUD Text
    cv2.putText(
        canvas,
        f"WAMSS WORKSTATION ACTIVITY ({camera_id})",
        (hud_x + 10, hud_y + 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.50,
        (0, 255, 200),
        1,
        cv2.LINE_AA,
    )

    if persons and summaries:
        primary_p = persons[0]
        s = summaries.get(primary_p.track_id)
        if s:
            state_color = COLOR_INTERACTING if s.current_state == WorkstationState.INTERACTING else (0, 220, 0)
            cv2.putText(
                canvas,
                f"Track ID: {s.track_id} | State: {s.current_state.value}",
                (hud_x + 10, hud_y + 45),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                state_color,
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                f"Presence: {s.presence_seconds:.1f}s | Interaction: {s.interaction_seconds:.1f}s | Idle: {s.idle_seconds:.1f}s",
                (hud_x + 10, hud_y + 68),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                COLOR_TEXT,
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                f"Engagement: {s.engagement_label}",
                (hud_x + 10, hud_y + 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 200, 50),
                1,
                cv2.LINE_AA,
            )
            dev_str = ", ".join(s.associated_devices) if s.associated_devices else "None"
            cv2.putText(
                canvas,
                f"Workstation Devices: {dev_str}",
                (hud_x + 10, hud_y + 112),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (200, 200, 200),
                1,
                cv2.LINE_AA,
            )
    else:
        cv2.putText(
            canvas,
            "No active person at workstation",
            (hud_x + 10, hud_y + 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (150, 150, 150),
            1,
            cv2.LINE_AA,
        )

    return canvas
