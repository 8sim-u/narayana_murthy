"""
Observable Workstation Activity and Engagement Analyzer for WAMSS Vision.

Computes transparent, observable indicators from camera detections:
- Presence duration at workstation (seconds)
- Device interaction duration (when active overlap/interaction is observed)
- Idle/passive presence duration (person present, but not interacting with device)
- Device detected duration (cumulative time devices are present in frame)
- Away/absent duration (time spent outside camera view)
- Unobserved/gap duration (when stream interrupts or frame drops exceed gap threshold)
- Observable workstation engagement ratio: (Interaction Time / Presence Time) * 100

CRITICAL SAFEGUARDS:
- Does NOT claim to measure employee productivity, focus, intelligence, or work value.
- Replaces subjective scoring with objective, verifiable duration ratios.
- Displays "Insufficient evidence" when observation time is under threshold or no
  devices are visible.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from vision.association import (
    AssociationStatus,
    PersonObjectAssociation,
    TrackedPerson,
)
from vision.workstation_objects import is_interactive_device


class WorkstationState(str, Enum):
    INTERACTING = "INTERACTING"
    PRESENT_IDLE = "PRESENT_IDLE"
    PRESENT_NO_DEVICE = "PRESENT_NO_DEVICE"
    AWAY = "AWAY"


ENGAGEMENT_DISCLAIMER = (
    "Observable Workstation Engagement measures camera-observed physical interaction "
    "ratio with workstation devices. It does NOT evaluate employee productivity, "
    "cognitive effort, task quality, or offline reading/ideation."
)


@dataclass
class WorkstationSummary:
    track_id: str
    current_state: WorkstationState
    presence_seconds: float
    interaction_seconds: float
    idle_seconds: float
    away_seconds: float
    engagement_score: Optional[float]
    engagement_label: str
    device_detected_seconds: float = 0.0
    unobserved_seconds: float = 0.0
    associated_devices: List[str] = field(default_factory=list)
    disclaimer: str = ENGAGEMENT_DISCLAIMER


class _PersonActivityRecord:
    def __init__(self, track_id: str, start_time: float):
        self.track_id = track_id
        self.first_seen = start_time
        self.last_update = start_time
        self.last_seen = start_time
        self.presence_seconds = 0.0
        self.interaction_seconds = 0.0
        self.idle_seconds = 0.0
        self.away_seconds = 0.0
        self.device_detected_seconds = 0.0
        self.unobserved_seconds = 0.0
        self.current_state = WorkstationState.PRESENT_NO_DEVICE
        self.seen_interactive_device = False
        self.current_devices: List[str] = []


class WorkstationActivityTracker:
    """Maintains continuous workstation activity records per ByteTrack person track ID."""

    def __init__(
        self,
        min_presence_sec: float = 5.0,
        away_timeout_sec: float = 3.0,
        max_frame_gap_sec: float = 2.0,
    ):
        self.min_presence_sec = min_presence_sec
        self.away_timeout_sec = away_timeout_sec
        self.max_frame_gap_sec = max_frame_gap_sec
        self.records: Dict[str, _PersonActivityRecord] = {}
        self.last_timestamp: Optional[float] = None

    def update(
        self,
        timestamp: float,
        persons: List[TrackedPerson],
        associations: List[PersonObjectAssociation],
    ) -> Dict[str, WorkstationSummary]:
        if self.last_timestamp is None:
            self.last_timestamp = timestamp

        raw_dt = max(0.0, timestamp - self.last_timestamp)
        self.last_timestamp = timestamp

        # Frame gap handling: if raw_dt exceeds max_frame_gap_sec, do not let missing frames inflate active metrics
        effective_dt = min(self.max_frame_gap_sec, raw_dt)
        unobserved_gap = max(0.0, raw_dt - self.max_frame_gap_sec)

        active_track_ids = {p.track_id for p in persons}

        # Group associations by track ID
        track_to_assocs: Dict[str, List[PersonObjectAssociation]] = {tid: [] for tid in active_track_ids}
        for a in associations:
            if a.assigned_track_id and a.assigned_track_id in track_to_assocs:
                track_to_assocs[a.assigned_track_id].append(a)

        # Update active persons
        for tid in active_track_ids:
            if tid not in self.records:
                self.records[tid] = _PersonActivityRecord(tid, timestamp)

            rec = self.records[tid]
            rec.last_seen = timestamp
            rec.presence_seconds += effective_dt
            rec.unobserved_seconds += unobserved_gap

            assocs = track_to_assocs.get(tid, [])
            rec.current_devices = [a.object.class_name for a in assocs]

            # Determine state
            has_interacting = any(a.status == AssociationStatus.INTERACTING for a in assocs)
            has_devices = len(assocs) > 0

            if has_devices:
                rec.device_detected_seconds += effective_dt

            if has_interacting:
                rec.current_state = WorkstationState.INTERACTING
                rec.interaction_seconds += effective_dt
                rec.seen_interactive_device = True
            elif has_devices:
                rec.current_state = WorkstationState.PRESENT_IDLE
                rec.idle_seconds += effective_dt
                if any(is_interactive_device(a.object.class_id) for a in assocs):
                    rec.seen_interactive_device = True
            else:
                rec.current_state = WorkstationState.PRESENT_NO_DEVICE
                rec.idle_seconds += effective_dt

            rec.last_update = timestamp

        # Update absent persons
        for tid, rec in self.records.items():
            if tid not in active_track_ids:
                rec.current_state = WorkstationState.AWAY
                rec.away_seconds += raw_dt
                rec.current_devices = []
                rec.last_update = timestamp

        # Return current summaries
        return {tid: self.get_summary(tid) for tid in self.records}

    def get_summary(self, track_id: str) -> Optional[WorkstationSummary]:
        rec = self.records.get(track_id)
        if not rec:
            return None

        # Calculate engagement score
        score: Optional[float] = None
        label: str

        if rec.presence_seconds < self.min_presence_sec:
            score = None
            label = f"Insufficient evidence (observed {rec.presence_seconds:.1f}s < {self.min_presence_sec:.1f}s threshold)"
        elif not rec.seen_interactive_device:
            score = None
            label = "Insufficient evidence (no workstation devices detected)"
        else:
            pct = (rec.interaction_seconds / rec.presence_seconds) * 100.0 if rec.presence_seconds > 0 else 0.0
            score = round(min(100.0, max(0.0, pct)), 1)
            label = f"{score:.1f}% device engagement"

        return WorkstationSummary(
            track_id=rec.track_id,
            current_state=rec.current_state,
            presence_seconds=round(rec.presence_seconds, 2),
            interaction_seconds=round(rec.interaction_seconds, 2),
            idle_seconds=round(rec.idle_seconds, 2),
            away_seconds=round(rec.away_seconds, 2),
            device_detected_seconds=round(rec.device_detected_seconds, 2),
            unobserved_seconds=round(rec.unobserved_seconds, 2),
            engagement_score=score,
            engagement_label=label,
            associated_devices=list(rec.current_devices),
        )
