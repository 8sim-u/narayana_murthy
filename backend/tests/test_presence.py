"""
Unit tests for the presence correlation pure function in app.presence.

Uses synthetic vision events and activity logs to cover all four derived
states, including the ACTIVE_BUT_ABSENT anomaly. Does not touch the
database; correlate() is a pure function of its inputs.
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

# app.config requires DATABASE_URL at import time. Tests default to a
# throwaway SQLite database; never hardcode real credentials here.
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite:////tmp/wamss_test_presence.db"

from app.presence import correlate  # noqa: E402


NOW = datetime(2026, 10, 30, 3, 0, 0, tzinfo=timezone.utc)
WINDOW_SEC = 300
ZONE_MAP = {
    "RESTRICTED": "MACHINE_01",
    "LOBBY": "MACHINE_02",
    "STORE": "MACHINE_03",
    "EMPTY": "MACHINE_04",
}


def vision(zone, minutes_ago):
    return SimpleNamespace(
        zone=zone,
        timestamp=NOW - timedelta(minutes=minutes_ago),
    )


def activity(machine_id, minutes_ago, idle=False, event_type="activity", employee_id="EMP_001"):
    end = NOW - timedelta(minutes=minutes_ago)
    return SimpleNamespace(
        machine_id=machine_id,
        employee_id=employee_id,
        event_type=event_type,
        app_name="editor",
        window_title="main",
        process_name="code",
        start_time=end - timedelta(minutes=5),
        end_time=end,
        keystrokes=10 if not idle else 0,
        mouse_clicks=2 if not idle else 0,
        is_idle=idle,
    )


def by_machine(results):
    return {r["machine_id"]: r for r in results}


def test_present_and_active():
    vision_events = [vision("RESTRICTED", 2)]
    activity_logs = [activity("MACHINE_01", 1, idle=False)]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_01"]
    assert state["state"] == "PRESENT_AND_ACTIVE"
    assert state["anomaly"] is False
    assert state["person_present"] is True
    assert state["active"] is True
    assert state["zone"] == "RESTRICTED"


def test_present_but_idle():
    vision_events = [vision("LOBBY", 3)]
    activity_logs = [activity("MACHINE_02", 1, idle=True)]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_02"]
    assert state["state"] == "PRESENT_BUT_IDLE"
    assert state["anomaly"] is False
    assert state["person_present"] is True
    assert state["active"] is False


def test_present_but_idle_with_no_activity_logs():
    vision_events = [vision("LOBBY", 3)]
    results = correlate(vision_events, [], ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_02"]
    assert state["state"] == "PRESENT_BUT_IDLE"
    assert state["anomaly"] is False


def test_active_but_absent_is_anomaly():
    vision_events = []  # nobody seen in STORE
    activity_logs = [activity("MACHINE_03", 1, idle=False)]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_03"]
    assert state["state"] == "ACTIVE_BUT_ABSENT"
    assert state["anomaly"] is True
    assert state["person_present"] is False
    assert state["active"] is True


def test_absent():
    results = correlate([], [], ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_04"]
    assert state["state"] == "ABSENT"
    assert state["anomaly"] is False
    assert state["person_present"] is False
    assert state["active"] is False


def test_stale_events_outside_window_are_ignored():
    # Vision 10 minutes ago with a 5-minute window: should count as absent.
    vision_events = [vision("RESTRICTED", 10)]
    activity_logs = [activity("MACHINE_01", 10, idle=False)]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_01"]
    assert state["state"] == "ABSENT"


def test_non_activity_event_types_do_not_count_as_active():
    # An "alert" record with is_idle=False must not mark the machine active.
    vision_events = []
    activity_logs = [activity("MACHINE_03", 1, idle=False, event_type="alert")]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_03"]
    assert state["state"] == "ABSENT"
    assert state["active"] is False


def test_dict_records_are_accepted():
    # correlate() supports plain dicts as well as attribute-style records.
    vision_events = [
        {"zone": "RESTRICTED", "timestamp": NOW - timedelta(minutes=2)}
    ]
    end = NOW - timedelta(minutes=1)
    activity_logs = [
        {
            "machine_id": "MACHINE_01",
            "employee_id": "EMP_007",
            "event_type": "activity",
            "start_time": end - timedelta(minutes=5),
            "end_time": end,
            "is_idle": False,
        }
    ]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_01"]
    assert state["state"] == "PRESENT_AND_ACTIVE"
    assert state["employee_id"] == "EMP_007"


def test_all_machines_reported_and_sorted():
    results = correlate([], [], ZONE_MAP, WINDOW_SEC, now=NOW)
    machines = [r["machine_id"] for r in results]
    assert machines == sorted(machines)
    assert set(machines) == {"MACHINE_01", "MACHINE_02", "MACHINE_03", "MACHINE_04"}


def test_last_seen_fields():
    vision_events = [vision("RESTRICTED", 4), vision("RESTRICTED", 2)]
    activity_logs = [activity("MACHINE_01", 5, idle=False), activity("MACHINE_01", 1, idle=False)]
    results = correlate(vision_events, activity_logs, ZONE_MAP, WINDOW_SEC, now=NOW)
    state = by_machine(results)["MACHINE_01"]
    assert state["last_vision_seen"] == NOW - timedelta(minutes=2)
    assert state["last_activity_seen"] == NOW - timedelta(minutes=1)
