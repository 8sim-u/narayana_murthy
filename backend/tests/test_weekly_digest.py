"""Unit tests for backend/scripts/weekly_digest.py (Agent K)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from weekly_digest import build_digest  # noqa: E402


def _rows():
    activity_rows = [
        {"employee_id": "E001", "is_idle": False, "strain_score": 55},
        {"employee_id": "E001", "is_idle": True, "strain_score": None},
        {"employee_id": "E002", "is_idle": False, "strain_score": 82},
        {"employee_id": "E003", "is_idle": False, "strain_score": 25},
    ]
    focus_rows = [
        {"employee_id": "E001", "focus_score": 72.5, "break_compliant": True, "streak_days": 4},
        {"employee_id": "E002", "focus_score": 58.0, "break_compliant": False, "streak_days": 0},
        {"employee_id": "E003", "focus_score": 91.0, "break_compliant": True, "streak_days": 12},
    ]
    strain_rows = [
        {"employee_id": "E001", "strain_level": "amber", "strain_score": 55},
        {"employee_id": "E002", "strain_level": "red", "strain_score": 82},
        {"employee_id": "E003", "strain_level": "green", "strain_score": 25},
    ]
    policy_flags = [
        {"employee_id": "E002", "flag": "EXCESSIVE_OVERTIME", "detail": "logged 62h"},
        {"employee_id": "E001", "flag": "UNAUTHORIZED_APP", "detail": "game client"},
    ]
    presence_rows = [
        {"employee_id": "E001", "status": "present", "anomaly": None},
        {"employee_id": "E002", "status": "present", "anomaly": "no badge-out after 22:00"},
        {"employee_id": "E003", "status": "absent", "anomaly": None},
    ]
    return activity_rows, focus_rows, strain_rows, policy_flags, presence_rows


def test_top_strain_employee_appears_in_plain():
    plain, _ = build_digest(*_rows())
    assert "E002" in plain
    assert "RED" in plain


def test_policy_flags_listed_in_plain():
    plain, _ = build_digest(*_rows())
    assert "EXCESSIVE_OVERTIME" in plain
    assert "UNAUTHORIZED_APP" in plain


def test_html_contains_stats_and_flags():
    _, html = build_digest(*_rows())
    assert "<html>" in html
    assert "E002" in html
    assert "EXCESSIVE_OVERTIME" in html
    assert "no badge-out after 22:00" in html


def test_empty_inputs_degrade_gracefully():
    plain, html = build_digest([], [], [], [], [])
    assert "no data" in plain
    assert "none reported" in plain
    assert "none raised" in plain
    assert "none detected" in plain
    assert "<html>" in html


# ---------------------------------------------------------------- live fetch tests

from unittest import mock  # noqa: E402

from weekly_digest import DigestFetchError, fetch_live_data  # noqa: E402


def _fake_api():
    """Canned API responses keyed by URL fragment, for fetch_live_data."""

    def _fake(url, timeout=15.0):
        if "/activity/strain-summary" in url:
            return [
                {"machine_id": "M1", "employee_id": "E001", "strain_score": 55,
                 "strain_level": "amber", "last_seen": None, "is_idle": False}
            ]
        if "/activity?" in url:
            return [
                {"employee_id": "E001", "is_idle": False, "keystrokes": 100,
                 "mouse_clicks": 20, "strain_score": 55},
                {"employee_id": "E001", "is_idle": True, "keystrokes": 0,
                 "mouse_clicks": 0, "strain_score": None},
            ]
        if "/focus/daily" in url:
            return {"employee_id": "E001", "date": "2026-10-30",
                    "focus_score": 72, "breakdown": {},
                    "break_compliance": {"compliant": True}}
        if "/focus/streaks" in url:
            return {"employee_id": "E001", "focus_streak_days": 4,
                    "break_streak_days": 2, "badges": []}
        if "/security/alerts" in url:
            return [
                {"id": 1, "event_type": "usb_insert", "severity": "high",
                 "reason": "unknown USB device", "machine_id": "M1",
                 "employee_id": "E001", "timestamp": "2026-10-30T00:00:00Z",
                 "detail": {}}
            ]
        if "/policy/violations" in url:
            return {"employee_id": "E001", "date": "2026-10-30",
                    "violations": [{"policy": "NO_WORK_AFTER_HOURS",
                                    "detail": "active at 23:10",
                                    "severity": "amber"}],
                    "segment_count": 5}
        if url.rstrip("/").endswith("/presence"):
            return [
                {"machine_id": "M1", "zone": "RESTRICTED",
                 "state": "PRESENT_AND_ACTIVE", "anomaly": False,
                 "person_present": True, "active": True,
                 "last_vision_seen": None, "last_activity_seen": None,
                 "employee_id": "E001", "window_sec": 300}
            ]
        raise AssertionError(f"unexpected URL: {url}")

    return _fake


def test_fetch_live_data_maps_api_shapes():
    with mock.patch("weekly_digest._get_json", side_effect=_fake_api()):
        data = fetch_live_data("http://localhost:8000", lookback_hours=24)
    (activity_rows, focus_rows, strain_rows, policy_flags,
     presence_rows, security_alerts) = data
    assert activity_rows[0]["employee_id"] == "E001"
    assert activity_rows[0]["keystrokes"] == 100
    assert focus_rows[0]["focus_score"] == 72
    assert focus_rows[0]["break_compliant"] is True
    assert focus_rows[0]["streak_days"] == 4
    assert strain_rows[0]["strain_level"] == "amber"
    assert policy_flags[0]["flag"] == "NO_WORK_AFTER_HOURS"
    assert presence_rows[0]["status"] == "present"
    assert presence_rows[0]["anomaly"] is None
    assert security_alerts[0]["severity"] == "high"
    assert security_alerts[0]["event_type"] == "usb_insert"


def test_fetch_live_data_team_failure_raises():
    with mock.patch("weekly_digest._get_json",
                    side_effect=DigestFetchError("GET http://x failed: refused")):
        try:
            fetch_live_data("http://x")
        except DigestFetchError:
            return
        raise AssertionError("expected DigestFetchError")


def test_fetch_live_data_per_employee_failure_is_best_effort():
    def _fake(url, timeout=15.0):
        if "/activity?" in url:
            return [{"employee_id": "E001", "is_idle": False,
                     "keystrokes": 10, "mouse_clicks": 1, "strain_score": 20}]
        if "/activity/strain-summary" in url:
            return []
        if url.rstrip("/").endswith("/presence"):
            return []
        if "/security/alerts" in url:
            return []
        raise DigestFetchError(f"GET {url} failed: boom")

    with mock.patch("weekly_digest._get_json", side_effect=_fake):
        (activity_rows, focus_rows, strain_rows, policy_flags,
         presence_rows, security_alerts) = fetch_live_data("http://localhost:8000")
    assert len(activity_rows) == 1
    # per-employee endpoints failed, but focus row defaults still exist
    assert focus_rows[0]["focus_score"] is None
    assert focus_rows[0]["streak_days"] == 0
    assert policy_flags == []
    assert security_alerts == []
    assert presence_rows == []


def test_build_digest_security_section():
    alerts = [{"employee_id": "E001", "severity": "high",
               "event_type": "usb_insert", "reason": "unknown USB device",
               "timestamp": "t"}]
    plain, html = build_digest(*_rows(), alerts)
    assert "Security alerts" in plain
    assert "usb_insert" in plain and "unknown USB device" in plain
    assert "[HIGH]" in plain
    assert "Security alerts" in html and "usb_insert" in html


def test_build_digest_security_defaults_to_empty():
    plain, html = build_digest(*_rows())
    assert "Security alerts" in plain
    assert "none raised" in plain
    assert "none raised" in html
