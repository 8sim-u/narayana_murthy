"""
Vision Geofence Spatial Evaluator and Hysteresis Engine (Build #2: Real Geofence Zones).

Provides:
- Geometric point-in-polygon ray-casting test.
- Floor-anchor extraction (bottom-center feet position) from bounding boxes.
- Debounced state machine with configurable hysteresis frames to suppress jitter.
- Dwell time tracking and single-fire DWELL_EXCEEDED threshold alert.
- Inactive & camera-scoped zone isolation.
- Strict Privacy Mode enforcement (anonymized "Person N" labels, zero face ID fields).
- Dynamic zone reload preserving active track stays.
- Stale track pruning and cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("vision.geofence")


@dataclass
class ZoneConfig:
    """Configuration for a single geofence zone."""
    id: int
    name: str
    polygon: List[List[float]]  # Normalized [x, y] coordinates in [0.0, 1.0]
    zone_type: str = "restricted"  # restricted, workstation, entry_exit, custom
    office_id: str = "hq"
    camera_id: Optional[str] = None
    color: str = "#ef4444"
    alert_on_enter: bool = True
    alert_on_exit: bool = False
    dwell_threshold_seconds: Optional[int] = None
    is_active: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ZoneConfig:
        return cls(
            id=int(data["id"]),
            name=str(data["name"]),
            polygon=[[float(p[0]), float(p[1])] for p in data["polygon"]],
            zone_type=str(data.get("zone_type", "restricted")),
            office_id=str(data.get("office_id", "hq")),
            camera_id=data.get("camera_id"),
            color=str(data.get("color", "#ef4444")),
            alert_on_enter=bool(data.get("alert_on_enter", True)),
            alert_on_exit=bool(data.get("alert_on_exit", False)),
            dwell_threshold_seconds=(
                int(data["dwell_threshold_seconds"])
                if data.get("dwell_threshold_seconds") is not None
                else None
            ),
            is_active=bool(data.get("is_active", True)),
        )


@dataclass
class GeofenceEvent:
    """Transition or threshold event emitted by the GeofenceEvaluator."""
    event_type: str  # ZONE_ENTER, ZONE_EXIT, DWELL_EXCEEDED
    zone_id: int
    zone_name: str
    zone_type: str
    track_id: str
    person_label: str
    identity_name: Optional[str] = None
    identity_id: Optional[str] = None
    camera_id: Optional[str] = None
    timestamp: float = 0.0
    position: Tuple[float, float] = (0.0, 0.0)
    dwell_seconds: float = 0.0
    alert_triggered: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """
    Ray-casting algorithm to test if a 2D point (x, y) is inside a polygon.
    Works for both convex and concave/non-convex polygons in normalized coordinates.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        x_inters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= x_inters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def extract_floor_anchor(
    bbox_xyxy: Sequence[float],
    frame_w: Optional[float] = None,
    frame_h: Optional[float] = None,
) -> Tuple[float, float]:
    """
    Extracts the bottom-center floor anchor point (feet contact position)
    from a bounding box [x1, y1, x2, y2], normalized to [0.0, 1.0].
    """
    x1, y1, x2, y2 = (float(v) for v in bbox_xyxy)
    cx = (x1 + x2) / 2.0
    cy = y2

    if frame_w is not None and frame_h is not None and frame_w > 0 and frame_h > 0:
        nx = cx / frame_w
        ny = cy / frame_h
    else:
        nx = cx
        ny = cy

    return (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))


@dataclass
class _TrackZoneState:
    current_state: str = "OUTSIDE"  # "OUTSIDE" or "INSIDE"
    inside_consecutive: int = 0
    outside_consecutive: int = 0
    entered_at: Optional[float] = None
    last_inside_at: Optional[float] = None
    dwell_alerted: bool = False


class GeofenceEvaluator:
    """
    Stateful geofence evaluation and hysteresis engine.

    Monitors tracked entities against 2D polygon boundaries with temporal
    debounce, dwell alerts, and privacy enforcement.
    """

    def __init__(
        self,
        zones: Optional[Sequence[ZoneConfig]] = None,
        debounce_frames: int = 3,
        camera_id: Optional[str] = None,
        privacy_mode: bool = False,
    ) -> None:
        self.debounce_frames = max(1, debounce_frames)
        self.camera_id = camera_id
        self.privacy_mode = privacy_mode

        self._zones: Dict[int, ZoneConfig] = {}
        if zones:
            self.update_zones(zones)

        # (track_id, zone_id) -> _TrackZoneState
        self._states: Dict[Tuple[str, int], _TrackZoneState] = {}
        # track_id -> last_seen_timestamp
        self._last_seen: Dict[str, float] = {}
        # track_id -> last known position (x, y)
        self._last_position: Dict[str, Tuple[float, float]] = {}

    def update_zones(self, zones: Sequence[ZoneConfig]) -> None:
        """Update or reload active zones while preserving in-progress track states."""
        new_zones = {z.id: z for z in zones}
        old_ids = set(self._zones.keys())
        new_ids = set(new_zones.keys())

        # Prune states for zones that have been removed
        removed_ids = old_ids - new_ids
        if removed_ids:
            self._states = {
                k: v for k, v in self._states.items() if k[1] not in removed_ids
            }

        self._zones = new_zones

    def is_inside(self, track_id: str, zone_id: int) -> bool:
        """Check if a tracked entity is currently confirmed inside a zone."""
        state = self._states.get((str(track_id), zone_id))
        return bool(state and state.current_state == "INSIDE")

    def get_inside_zones(self, track_id: str) -> List[ZoneConfig]:
        """List all zones a tracked entity is currently inside."""
        tid = str(track_id)
        inside_zones = []
        for (t, zid), state in self._states.items():
            if t == tid and state.current_state == "INSIDE":
                if zid in self._zones:
                    inside_zones.append(self._zones[zid])
        return inside_zones

    def evaluate(
        self,
        track_id: str,
        position_or_bbox: Sequence[float],
        timestamp: float,
        identity_name: Optional[str] = None,
        identity_id: Optional[str] = None,
        frame_w: Optional[float] = None,
        frame_h: Optional[float] = None,
    ) -> List[GeofenceEvent]:
        """
        Evaluate a single tracked entity's position against active geofence zones.

        Returns any emitted transition events (ZONE_ENTER, ZONE_EXIT, DWELL_EXCEEDED).
        """
        tid = str(track_id)
        self._last_seen[tid] = timestamp

        # Resolve floor coordinates
        if len(position_or_bbox) == 2:
            px, py = float(position_or_bbox[0]), float(position_or_bbox[1])
        elif len(position_or_bbox) == 4:
            px, py = extract_floor_anchor(position_or_bbox, frame_w, frame_h)
        else:
            logger.warning("Invalid position/bbox coordinates: %s", position_or_bbox)
            return []

        self._last_position[tid] = (px, py)

        # Strict Privacy Mode invariant
        if self.privacy_mode:
            rep_name = None
            rep_id = None
            rep_label = f"Person {tid}"
        else:
            rep_name = identity_name if (identity_name and identity_name != "unknown") else None
            rep_id = identity_id
            rep_label = rep_name if rep_name else f"Person {tid}"

        events: List[GeofenceEvent] = []

        for zone in self._zones.values():
            # Check active status and camera scoping
            if not zone.is_active:
                continue
            if zone.camera_id is not None and self.camera_id is not None and zone.camera_id != self.camera_id:
                continue

            state_key = (tid, zone.id)
            if state_key not in self._states:
                self._states[state_key] = _TrackZoneState()
            state = self._states[state_key]

            is_in_poly = point_in_polygon(px, py, zone.polygon)

            if is_in_poly:
                state.inside_consecutive += 1
                state.outside_consecutive = 0
                state.last_inside_at = timestamp

                if state.current_state == "OUTSIDE":
                    if state.inside_consecutive >= self.debounce_frames:
                        state.current_state = "INSIDE"
                        state.entered_at = timestamp
                        state.dwell_alerted = False
                        events.append(
                            GeofenceEvent(
                                event_type="ZONE_ENTER",
                                zone_id=zone.id,
                                zone_name=zone.name,
                                zone_type=zone.zone_type,
                                track_id=tid,
                                person_label=rep_label,
                                identity_name=rep_name,
                                identity_id=rep_id,
                                camera_id=zone.camera_id or self.camera_id,
                                timestamp=timestamp,
                                position=(px, py),
                                dwell_seconds=0.0,
                                alert_triggered=zone.alert_on_enter,
                                details={"color": zone.color},
                            )
                        )
                else:  # Already INSIDE
                    elapsed = timestamp - (state.entered_at or timestamp)
                    if zone.dwell_threshold_seconds is not None and not state.dwell_alerted:
                        if elapsed >= zone.dwell_threshold_seconds:
                            state.dwell_alerted = True
                            events.append(
                                GeofenceEvent(
                                    event_type="DWELL_EXCEEDED",
                                    zone_id=zone.id,
                                    zone_name=zone.name,
                                    zone_type=zone.zone_type,
                                    track_id=tid,
                                    person_label=rep_label,
                                    identity_name=rep_name,
                                    identity_id=rep_id,
                                    camera_id=zone.camera_id or self.camera_id,
                                    timestamp=timestamp,
                                    position=(px, py),
                                    dwell_seconds=elapsed,
                                    alert_triggered=True,
                                    details={"threshold_seconds": zone.dwell_threshold_seconds},
                                )
                            )
            else:  # Outside polygon
                state.outside_consecutive += 1
                state.inside_consecutive = 0

                if state.current_state == "INSIDE":
                    if state.outside_consecutive >= self.debounce_frames:
                        state.current_state = "OUTSIDE"
                        total_dwell = (state.last_inside_at or timestamp) - (state.entered_at or timestamp)
                        events.append(
                            GeofenceEvent(
                                event_type="ZONE_EXIT",
                                zone_id=zone.id,
                                zone_name=zone.name,
                                zone_type=zone.zone_type,
                                track_id=tid,
                                person_label=rep_label,
                                identity_name=rep_name,
                                identity_id=rep_id,
                                camera_id=zone.camera_id or self.camera_id,
                                timestamp=timestamp,
                                position=(px, py),
                                dwell_seconds=max(0.0, total_dwell),
                                alert_triggered=zone.alert_on_exit,
                                details={"color": zone.color},
                            )
                        )
                        state.entered_at = None
                        state.dwell_alerted = False

        return events

    def prune_stale_tracks(self, now: float, timeout_seconds: float = 30.0) -> List[GeofenceEvent]:
        """
        Prunes track states for entities that have disappeared from view for > timeout_seconds.
        Emits ZONE_EXIT for tracks that disappeared while INSIDE.
        """
        events: List[GeofenceEvent] = []
        stale_tracks: List[str] = [
            tid for tid, last_ts in self._last_seen.items() if now - last_ts > timeout_seconds
        ]

        for tid in stale_tracks:
            pos = self._last_position.get(tid, (0.0, 0.0))
            rep_label = f"Person {tid}"

            # Check if track was inside any zone
            for zone in self._zones.values():
                state_key = (tid, zone.id)
                state = self._states.get(state_key)
                if state and state.current_state == "INSIDE":
                    total_dwell = (state.last_inside_at or now) - (state.entered_at or now)
                    events.append(
                        GeofenceEvent(
                            event_type="ZONE_EXIT",
                            zone_id=zone.id,
                            zone_name=zone.name,
                            zone_type=zone.zone_type,
                            track_id=tid,
                            person_label=rep_label,
                            identity_name=None,
                            identity_id=None,
                            camera_id=zone.camera_id or self.camera_id,
                            timestamp=now,
                            position=pos,
                            dwell_seconds=max(0.0, total_dwell),
                            alert_triggered=zone.alert_on_exit,
                            details={"reason": "track_disappeared"},
                        )
                    )

            # Cleanup track state
            self._states = {k: v for k, v in self._states.items() if k[0] != tid}
            self._last_seen.pop(tid, None)
            self._last_position.pop(tid, None)

        return events
