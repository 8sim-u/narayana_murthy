"""Small, side-effect-free geofence rules plus short-lived transition state.

Polygons are stored in normalized camera-map coordinates (0..1). Track IDs
are used only in this in-memory state so a crossing can be detected; they are
never included in persisted geofence event records or alert payloads.
"""

from __future__ import annotations

import math
import time
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple


ZONE_STATE_TTL_SECONDS = 15 * 60
_BOUNDARY_EPSILON = 1e-9
_zone_state: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
_state_lock = Lock()


def normalize_position(
    x: Optional[float],
    y: Optional[float],
    bounds: Optional[Iterable[float]] = None,
) -> Optional[Tuple[float, float]]:
    """Convert a room-space point to normalized camera-map coordinates.

    Older senders omit ``bounds`` and are treated as already normalized.
    Calibrated senders provide room width/height in meters. Invalid or
    non-finite values are ignored instead of producing a false transition.
    """
    if x is None or y is None:
        return None
    try:
        px, py = float(x), float(y)
        if not math.isfinite(px) or not math.isfinite(py):
            return None
        if bounds is None:
            width, height = 1.0, 1.0
        else:
            values = list(bounds)
            if len(values) != 2:
                return None
            width, height = float(values[0]), float(values[1])
            if (not math.isfinite(width) or not math.isfinite(height)
                    or width <= 0 or height <= 0):
                return None
        return (min(1.0, max(0.0, px / width)),
                min(1.0, max(0.0, py / height)))
    except (TypeError, ValueError, OverflowError):
        return None


def point_in_polygon(point: Tuple[float, float], polygon: List[List[float]]) -> bool:
    """Return whether a point is inside or on the boundary of a polygon."""
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]

    for current_x, current_y in polygon:
        dx = current_x - previous_x
        dy = current_y - previous_y
        cross = (x - previous_x) * dy - (y - previous_y) * dx
        if abs(cross) <= _BOUNDARY_EPSILON and (
            min(previous_x, current_x) - _BOUNDARY_EPSILON <= x
            <= max(previous_x, current_x) + _BOUNDARY_EPSILON
            and min(previous_y, current_y) - _BOUNDARY_EPSILON <= y
            <= max(previous_y, current_y) + _BOUNDARY_EPSILON
        ):
            return True

        if (current_y > y) != (previous_y > y):
            crossing_x = previous_x + (y - previous_y) * dx / dy
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y

    return inside


def observe_position(
    camera_id: str,
    track_id: str,
    point: Tuple[float, float],
    zones: Iterable[Any],
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return entry/exit transitions for one observation without updating it.

    A first observation inside a zone counts as an entry. A first observation
    outside establishes an outside state without emitting an exit. Missing
    observations do not imply an exit; only a later point outside the polygon
    can produce one.
    """
    with _state_lock:
        current_time = time.monotonic() if now is None else now
        stale = [key for key, value in _zone_state.items()
                 if current_time - value["seen_at"] > ZONE_STATE_TTL_SECONDS]
        for key in stale:
            del _zone_state[key]

        transitions: List[Dict[str, Any]] = []
        for zone in zones:
            zone_id = int(zone.id)
            key = (camera_id, track_id, zone_id)
            inside = point_in_polygon(point, zone.polygon)
            previous = _zone_state.get(key)

            if previous is None:
                if inside:
                    transitions.append({
                        "zone_id": zone_id,
                        "zone_name": zone.name,
                        "transition": "entry",
                    })
            elif previous["inside"] != inside:
                transitions.append({
                    "zone_id": zone_id,
                    "zone_name": zone.name,
                    "transition": "entry" if inside else "exit",
                })

        return transitions


def remember_position(
    camera_id: str,
    track_id: str,
    point: Tuple[float, float],
    zones: Iterable[Any],
    now: Optional[float] = None,
) -> None:
    """Record an observation after any associated transition is durable."""
    with _state_lock:
        current_time = time.monotonic() if now is None else now
        for zone in zones:
            key = (camera_id, track_id, int(zone.id))
            _zone_state[key] = {
                "inside": point_in_polygon(point, zone.polygon),
                "seen_at": current_time,
            }
