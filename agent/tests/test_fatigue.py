"""Unit tests for session-to-session fatigue detection (agent/fatigue.py).

All input is synthetic. No real user data, no I/O.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fatigue import FatigueTracker, robust_z  # noqa: E402

STABLE = {"k_mean": 0.25, "k_std": 0.05, "m_rate": 12.0, "burst": 0.10}
SHIFTED = {"k_mean": 0.60, "k_std": 0.20, "m_rate": 45.0, "burst": 0.50}


def test_stable_sessions_raise_no_event():
    tracker = FatigueTracker(employee_id="emp-1")
    for _ in range(8):
        assert tracker.add_session(dict(STABLE)) is None
    assert tracker.session_count == 8


def test_shifted_distribution_triggers_fatigue_suspected():
    tracker = FatigueTracker(employee_id="emp-1")
    for _ in range(4):
        assert tracker.add_session(dict(STABLE)) is None
    event = tracker.add_session(dict(SHIFTED))
    assert event is not None
    assert event["type"] == "fatigue_suspected"
    assert event["employee_id"] == "emp-1"
    assert event["severity"] in ("moderate", "high")
    detail = event["detail"]
    assert detail["sessions_compared"] == 4
    assert detail["k"] == 3
    assert detail["z_threshold"] == 2.5
    assert detail["knn_distance"] > 0


def test_needs_four_prior_sessions_before_flagging():
    tracker = FatigueTracker(employee_id="emp-2")
    for _ in range(3):
        assert tracker.add_session(dict(STABLE)) is None
    # Shifted, but only 3 prior sessions: not enough history to judge.
    assert tracker.add_session(dict(SHIFTED)) is None


def test_degenerate_feature_dicts_are_ignored():
    tracker = FatigueTracker(employee_id="emp-3")
    assert tracker.add_session({}) is None
    assert tracker.add_session({"a": "text", "b": None}) is None
    assert tracker.add_session("not-a-dict") is None  # type: ignore
    assert tracker.session_count == 0
    # Tracker still works afterwards.
    for _ in range(4):
        tracker.add_session(dict(STABLE))
    assert tracker.add_session(dict(SHIFTED))["type"] == "fatigue_suspected"


def test_later_dicts_align_to_first_key_order():
    tracker = FatigueTracker(employee_id="emp-4")
    tracker.add_session({"x": 1.0, "y": 2.0})
    # Missing key fills 0.0, extra key ignored, non-numeric ignored.
    assert tracker.add_session({"x": 1.0, "z": 99.0, "junk": "hi"}) is None
    assert tracker.session_count == 2


def test_history_bounded_by_max_sessions():
    tracker = FatigueTracker(employee_id="emp-5", max_sessions=3)
    for _ in range(5):
        tracker.add_session(dict(STABLE))
    assert tracker.session_count == 3


def test_robust_z_zero_spread():
    assert robust_z(5.0, [1.0, 1.0, 1.0, 1.0]) == float("inf")
    assert robust_z(1.0, [1.0, 1.0, 1.0, 1.0]) == 0.0
    assert robust_z(0.0, []) == 0.0


def test_robust_z_typical():
    z = robust_z(10.0, [1.0, 2.0, 3.0, 2.0, 1.0])
    assert z > 2.5
    z2 = robust_z(2.1, [1.0, 2.0, 3.0, 2.0, 1.0])
    assert abs(z2) < 2.5


def test_reset_clears_state():
    tracker = FatigueTracker(employee_id="emp-6")
    tracker.add_session(dict(STABLE))
    tracker.reset()
    assert tracker.session_count == 0
