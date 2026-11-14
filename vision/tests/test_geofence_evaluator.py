"""
Unit tests for the Vision Geofence Spatial Evaluator (Stage 2: Real Geofence Zones).

Covers:
- Point-in-polygon ray-casting (convex, concave/L-shaped, boundary)
- Floor-anchor point extraction from bounding boxes (feet position)
- Hysteresis & debounce for ZONE_ENTER and ZONE_EXIT
- Brief 1-frame boundary jitter suppression
- Dwell time calculation and DWELL_EXCEEDED threshold single-fire guarantee
- Inactive and camera-scoped zone filtering
- Privacy mode identity stripping and anonymous label enforcement
- Dynamic zone configuration reload preserving track states
- Stale track pruning
"""

import pytest

from vision.geofence_evaluator import (
    GeofenceEvaluator,
    GeofenceEvent,
    ZoneConfig,
    extract_floor_anchor,
    point_in_polygon,
)


# ---------------------------------------------------------------------------
# 1. Geometric Point-in-Polygon & Floor Anchor
# ---------------------------------------------------------------------------


def test_point_in_polygon_convex_and_concave():
    # Square [0.2, 0.2] to [0.6, 0.6]
    square = [[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]]
    assert point_in_polygon(0.4, 0.4, square) is True
    assert point_in_polygon(0.1, 0.1, square) is False
    assert point_in_polygon(0.7, 0.4, square) is False

    # Concave L-shaped polygon:
    # (0,0) -> (4,0) -> (4,2) -> (2,2) -> (2,4) -> (0,4)
    # scaled by 0.1:
    l_shape = [
        [0.0, 0.0],
        [0.4, 0.0],
        [0.4, 0.2],
        [0.2, 0.2],
        [0.2, 0.4],
        [0.0, 0.4],
    ]
    # Inside the lower bar
    assert point_in_polygon(0.3, 0.1, l_shape) is True
    # Inside the vertical bar
    assert point_in_polygon(0.1, 0.3, l_shape) is True
    # In the missing cutout of the L
    assert point_in_polygon(0.3, 0.3, l_shape) is False
    # Far outside
    assert point_in_polygon(0.8, 0.8, l_shape) is False


def test_extract_floor_anchor():
    # Raw pixel bounding box [x1, y1, x2, y2] with frame dimensions
    # feet anchor is bottom-center: x = (100+300)/2 = 200, y = 600
    # in 1000x1000 frame -> (0.2, 0.6)
    anchor = extract_floor_anchor([100, 200, 300, 600], frame_w=1000, frame_h=1000)
    assert anchor == pytest.approx((0.2, 0.6), abs=1e-4)

    # Pre-normalized bounding box [x1, y1, x2, y2] where values are in [0, 1]
    norm_anchor = extract_floor_anchor([0.1, 0.2, 0.5, 0.8])
    assert norm_anchor == pytest.approx((0.3, 0.8), abs=1e-4)

    # Clamped bounds
    clamped = extract_floor_anchor([-10, -10, 1200, 1500], frame_w=1000, frame_h=1000)
    assert clamped[0] >= 0.0 and clamped[0] <= 1.0
    assert clamped[1] >= 0.0 and clamped[1] <= 1.0


# ---------------------------------------------------------------------------
# 2. Debounce & Hysteresis Stability
# ---------------------------------------------------------------------------


def test_debounce_hysteresis_entry_and_jitter():
    zone = ZoneConfig(
        id=1,
        name="Restricted Lab",
        zone_type="restricted",
        polygon=[[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]],
        alert_on_enter=True,
    )
    evaluator = GeofenceEvaluator(zones=[zone], debounce_frames=3, camera_id="CAM_01")

    # Inside point: (0.4, 0.4)
    # Outside point: (0.1, 0.1)

    # Frame 1: Inside (1/3 frames) -> No event
    events = evaluator.evaluate("1", (0.4, 0.4), timestamp=10.0)
    assert len(events) == 0
    assert evaluator.is_inside("1", zone_id=1) is False

    # Frame 2: Inside (2/3 frames) -> No event
    events = evaluator.evaluate("1", (0.4, 0.4), timestamp=10.1)
    assert len(events) == 0
    assert evaluator.is_inside("1", zone_id=1) is False

    # Frame 3: Inside (3/3 frames) -> Triggers ZONE_ENTER!
    events = evaluator.evaluate("1", (0.4, 0.4), timestamp=10.2)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "ZONE_ENTER"
    assert ev.zone_id == 1
    assert ev.zone_name == "Restricted Lab"
    assert ev.track_id == "1"
    assert ev.alert_triggered is True
    assert evaluator.is_inside("1", zone_id=1) is True

    # Frame 4: 1-frame jitter outside (e.g. bounding box noise)
    # Debounce frames is 3, so 1 frame outside must NOT trigger exit or change state
    events = evaluator.evaluate("1", (0.1, 0.1), timestamp=10.3)
    assert len(events) == 0
    assert evaluator.is_inside("1", zone_id=1) is True

    # Frame 5: Back inside -> Still inside, no duplicate ZONE_ENTER
    events = evaluator.evaluate("1", (0.4, 0.4), timestamp=10.4)
    assert len(events) == 0
    assert evaluator.is_inside("1", zone_id=1) is True


# ---------------------------------------------------------------------------
# 3. Exit & Dwell Duration
# ---------------------------------------------------------------------------


def test_exit_and_dwell_duration():
    zone = ZoneConfig(
        id=2,
        name="Server Area",
        zone_type="restricted",
        polygon=[[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]],
        alert_on_exit=True,
    )
    evaluator = GeofenceEvaluator(zones=[zone], debounce_frames=2, camera_id="CAM_01")

    # Enter at t=100.0 (2 consecutive frames)
    evaluator.evaluate("1", (0.4, 0.4), timestamp=100.0)
    events = evaluator.evaluate("1", (0.4, 0.4), timestamp=100.1)
    assert len(events) == 1
    assert events[0].event_type == "ZONE_ENTER"

    # Stay inside until t=105.0
    evaluator.evaluate("1", (0.4, 0.4), timestamp=105.0)

    # Frame outside at t=105.5 (1/2 frames outside) -> No exit yet
    events = evaluator.evaluate("1", (0.1, 0.1), timestamp=105.5)
    assert len(events) == 0
    assert evaluator.is_inside("1", zone_id=2) is True

    # Frame outside at t=105.6 (2/2 frames outside) -> Triggers ZONE_EXIT!
    events = evaluator.evaluate("1", (0.1, 0.1), timestamp=105.6)
    assert len(events) == 1
    exit_ev = events[0]
    assert exit_ev.event_type == "ZONE_EXIT"
    assert exit_ev.zone_id == 2
    assert exit_ev.track_id == "1"
    assert exit_ev.alert_triggered is True  # alert_on_exit=True
    # Total dwell time is the duration between entry (100.1) and last seen inside (105.0)
    assert exit_ev.dwell_seconds == pytest.approx(4.9, abs=0.2)
    assert evaluator.is_inside("1", zone_id=2) is False


# ---------------------------------------------------------------------------
# 4. Dwell Threshold Trigger (DWELL_EXCEEDED)
# ---------------------------------------------------------------------------


def test_dwell_threshold_exceeded_fires_once():
    zone = ZoneConfig(
        id=3,
        name="Breakroom",
        zone_type="custom",
        polygon=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        dwell_threshold_seconds=5,
    )
    evaluator = GeofenceEvaluator(zones=[zone], debounce_frames=1, camera_id="CAM_01")

    # Enter at t=10.0
    events = evaluator.evaluate("1", (0.5, 0.5), timestamp=10.0)
    assert len(events) == 1
    assert events[0].event_type == "ZONE_ENTER"

    # Still inside at t=13.0 (3s elapsed < 5s) -> No dwell event
    events = evaluator.evaluate("1", (0.5, 0.5), timestamp=13.0)
    assert len(events) == 0

    # Crosses threshold at t=15.0 (5s elapsed >= 5s) -> Emits DWELL_EXCEEDED!
    events = evaluator.evaluate("1", (0.5, 0.5), timestamp=15.0)
    assert len(events) == 1
    dwell_ev = events[0]
    assert dwell_ev.event_type == "DWELL_EXCEEDED"
    assert dwell_ev.zone_id == 3
    assert dwell_ev.alert_triggered is True
    assert dwell_ev.dwell_seconds >= 5.0

    # Stays inside at t=16.0, 17.0, 18.0 -> MUST NOT re-fire DWELL_EXCEEDED
    assert len(evaluator.evaluate("1", (0.5, 0.5), timestamp=16.0)) == 0
    assert len(evaluator.evaluate("1", (0.5, 0.5), timestamp=17.0)) == 0
    assert len(evaluator.evaluate("1", (0.5, 0.5), timestamp=18.0)) == 0

    # Exit at t=20.0
    exit_events = evaluator.evaluate("1", (0.05, 0.05), timestamp=20.0)
    assert len(exit_events) == 1
    assert exit_events[0].event_type == "ZONE_EXIT"

    # Re-enter at t=30.0 -> New stay, dwell trigger armed again
    enter_again = evaluator.evaluate("1", (0.5, 0.5), timestamp=30.0)
    assert len(enter_again) == 1
    assert enter_again[0].event_type == "ZONE_ENTER"

    # Crosses 5s threshold again at t=35.0
    dwell_again = evaluator.evaluate("1", (0.5, 0.5), timestamp=35.0)
    assert len(dwell_again) == 1
    assert dwell_again[0].event_type == "DWELL_EXCEEDED"


# ---------------------------------------------------------------------------
# 5. Inactive & Camera-Scoped Zones
# ---------------------------------------------------------------------------


def test_inactive_and_camera_scoped_zones():
    z_inactive = ZoneConfig(
        id=10,
        name="Inactive Zone",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        is_active=False,
    )
    z_other_cam = ZoneConfig(
        id=11,
        name="Camera 2 Zone",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        camera_id="CAM_02",
        is_active=True,
    )
    z_matching_cam = ZoneConfig(
        id=12,
        name="Camera 1 Zone",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        camera_id="CAM_01",
        is_active=True,
    )
    z_global = ZoneConfig(
        id=13,
        name="Global Zone",
        zone_type="workstation",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        camera_id=None,
        is_active=True,
    )

    evaluator = GeofenceEvaluator(
        zones=[z_inactive, z_other_cam, z_matching_cam, z_global],
        debounce_frames=1,
        camera_id="CAM_01",
    )

    # Point (0.5, 0.5) is inside all 4 polygons, but:
    # - z_inactive is ignored (is_active=False)
    # - z_other_cam is ignored (CAM_02 != CAM_01)
    # - z_matching_cam matches (CAM_01)
    # - z_global matches (camera_id=None means all cameras)
    events = evaluator.evaluate("1", (0.5, 0.5), timestamp=10.0)
    event_zone_ids = {ev.zone_id for ev in events}
    assert event_zone_ids == {12, 13}
    assert 10 not in event_zone_ids
    assert 11 not in event_zone_ids


# ---------------------------------------------------------------------------
# 6. Privacy Mode Invariant
# ---------------------------------------------------------------------------


def test_privacy_mode_enforcement():
    zone = ZoneConfig(
        id=20,
        name="Exec Office",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    )

    # 1. Non-privacy evaluator
    eval_standard = GeofenceEvaluator(
        zones=[zone], debounce_frames=1, privacy_mode=False
    )
    events_std = eval_standard.evaluate(
        "7", (0.5, 0.5), timestamp=10.0, identity_name="Alice", identity_id="alice-id"
    )
    assert len(events_std) == 1
    assert events_std[0].identity_name == "Alice"
    assert events_std[0].identity_id == "alice-id"
    assert events_std[0].person_label == "Alice"

    # 2. Strict Privacy Mode evaluator
    eval_privacy = GeofenceEvaluator(zones=[zone], debounce_frames=1, privacy_mode=True)
    events_priv = eval_privacy.evaluate(
        "7", (0.5, 0.5), timestamp=10.0, identity_name="Alice", identity_id="alice-id"
    )
    assert len(events_priv) == 1
    assert events_priv[0].identity_name is None
    assert events_priv[0].identity_id is None
    assert events_priv[0].person_label == "Person 7"


# ---------------------------------------------------------------------------
# 7. Zone Reload & Stale Track Pruning
# ---------------------------------------------------------------------------


def test_update_zones_preserves_track_state():
    z1 = ZoneConfig(
        id=1,
        name="Zone 1",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
    )
    evaluator = GeofenceEvaluator(zones=[z1], debounce_frames=1)

    # Person 1 enters Zone 1
    events = evaluator.evaluate("1", (0.2, 0.2), timestamp=10.0)
    assert len(events) == 1
    assert evaluator.is_inside("1", zone_id=1) is True

    # Reload zones: update Zone 1's name and add Zone 2
    z1_renamed = ZoneConfig(
        id=1,
        name="Zone 1 Renamed",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
    )
    z2 = ZoneConfig(
        id=2,
        name="Zone 2",
        zone_type="workstation",
        polygon=[[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]],
    )
    evaluator.update_zones([z1_renamed, z2])

    # Person 1 is STILL inside Zone 1 without re-triggering ZONE_ENTER
    assert evaluator.is_inside("1", zone_id=1) is True
    no_events = evaluator.evaluate("1", (0.2, 0.2), timestamp=11.0)
    assert len(no_events) == 0


def test_stale_track_pruning():
    z = ZoneConfig(
        id=1,
        name="Zone",
        zone_type="restricted",
        polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    )
    evaluator = GeofenceEvaluator(zones=[z], debounce_frames=1)

    evaluator.evaluate("track-10", (0.5, 0.5), timestamp=10.0)
    assert evaluator.is_inside("track-10", zone_id=1) is True

    # Prune with timeout 5s at t=12s -> Not stale yet
    evaluator.prune_stale_tracks(now=12.0, timeout_seconds=5.0)
    assert evaluator.is_inside("track-10", zone_id=1) is True

    # Prune with timeout 5s at t=16s -> Stale! Cleans up state
    pruned_events = evaluator.prune_stale_tracks(now=16.0, timeout_seconds=5.0)
    # Emits ZONE_EXIT for tracks disappeared while inside
    assert len(pruned_events) == 1
    assert pruned_events[0].event_type == "ZONE_EXIT"
    assert pruned_events[0].track_id == "track-10"
    assert evaluator.is_inside("track-10", zone_id=1) is False


def test_zone_refresher_sync():
    """ZoneRefresher fetches active zones from GET /api/zones and loads them into evaluator."""
    from vision.multicamera import ZoneRefresher

    evaluator = GeofenceEvaluator(zones=[], camera_id="CAM_01")
    assert len(evaluator._zones) == 0

    mock_zones_payload = [
        {
            "id": 101,
            "name": "Synced Restricted Zone",
            "zone_type": "restricted",
            "polygon": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
            "office_id": "hq",
            "camera_id": "CAM_01",
            "color": "#ef4444",
            "alert_on_enter": True,
            "alert_on_exit": False,
            "dwell_threshold_seconds": 60,
            "is_active": True,
        }
    ]

    class FakeResponse:
        status_code = 200
        def json(self):
            return mock_zones_payload

    def fake_http_get(url, params=None, headers=None, timeout=None):
        assert "/api/zones" in url
        assert params.get("is_active") == "true"
        assert params.get("camera_id") == "CAM_01"
        return FakeResponse()

    refresher = ZoneRefresher(
        base_url="http://localhost:8000",
        poll_seconds=10.0,
        evaluator=evaluator,
        camera_id="CAM_01",
        http_get=fake_http_get,
    )

    # First poll at t=10.0 -> updates evaluator
    refreshed = refresher.maybe_refresh(now=10.0)
    assert refreshed is True
    assert 101 in evaluator._zones
    assert evaluator._zones[101].name == "Synced Restricted Zone"

    # Second check at t=15.0 (only 5s elapsed < 10s poll) -> skips
    assert refresher.maybe_refresh(now=15.0) is False

    # Third check at t=21.0 -> polls again
    assert refresher.maybe_refresh(now=21.0) is True

