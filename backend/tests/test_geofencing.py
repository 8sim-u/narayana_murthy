from types import SimpleNamespace

import pytest

from app import geofencing


@pytest.fixture(autouse=True)
def clear_geofence_state():
    geofencing._zone_state.clear()
    yield
    geofencing._zone_state.clear()


def _zone():
    return SimpleNamespace(
        id=7,
        name="Reception",
        polygon=[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
    )


def test_point_in_polygon_includes_inside_and_boundary():
    polygon = _zone().polygon
    assert geofencing.point_in_polygon((0.5, 0.5), polygon)
    assert geofencing.point_in_polygon((0.2, 0.5), polygon)
    assert not geofencing.point_in_polygon((0.1, 0.5), polygon)


def test_calibrated_position_is_scaled_to_normalized_room_map():
    assert geofencing.normalize_position(2.0, 1.0, [8.0, 4.0]) == (0.25, 0.25)
    assert geofencing.normalize_position(0.5, 0.75) == (0.5, 0.75)
    assert geofencing.normalize_position(0.5, 0.75, [0.0, 1.0]) is None


def test_observed_inside_outside_transitions_are_emitted_once():
    zone = _zone()
    assert geofencing.observe_position("CAM_01", "track-1", (0.5, 0.5), [zone], now=1) == [
        {"zone_id": 7, "zone_name": "Reception", "transition": "entry"}
    ]
    assert geofencing.observe_position("CAM_01", "track-1", (0.6, 0.5), [zone], now=2) == []
    assert geofencing.observe_position("CAM_01", "track-1", (0.1, 0.5), [zone], now=3) == [
        {"zone_id": 7, "zone_name": "Reception", "transition": "exit"}
    ]
    assert geofencing.observe_position("CAM_01", "track-1", (0.5, 0.5), [zone], now=4) == [
        {"zone_id": 7, "zone_name": "Reception", "transition": "entry"}
    ]
