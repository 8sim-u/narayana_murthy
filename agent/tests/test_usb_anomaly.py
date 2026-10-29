"""Unit tests for the USB anomaly detector in usb_monitor.py (synthetic only).

No real USB hardware is touched. State and allowlist files live in pytest
tmp dirs; the Windows WMI / EventID 6416 and Linux pyudev serial paths are
documented in the module but intentionally not exercised here (they need a
real machine).
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from usb_monitor import UsbAnomalyDetector  # noqa: E402

NIGHT = datetime(2026, 10, 30, 22, 15, tzinfo=timezone.utc)  # 22:15 UTC
MORNING = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)  # 10:00 UTC


def _detector(tmp_path, monkeypatch, allowlist_content="", **kwargs):
    state = tmp_path / "usb_seen.json"
    allowlist = tmp_path / "usb_allowlist.txt"
    allowlist.write_text(allowlist_content, encoding="utf-8")
    monkeypatch.setenv("WAMSS_USB_STATE", str(state))
    monkeypatch.setenv("WAMSS_USB_ALLOWLIST", str(allowlist))
    params = dict(
        user="test-user",
        state_path=str(state),
        allowlist_path=str(allowlist),
        work_hours="09:00-18:00",
        tz_name="UTC",
    )
    params.update(kwargs)
    return UsbAnomalyDetector(**params)


def _types(events):
    return {e["event_type"] for e in events}


def test_first_seen_serial_emits_medium_event(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    device = {"serial": "SN-NEW-1", "device_class": "mass_storage",
              "label": "flash drive", "mountpoint": "/media/usb"}
    events = det.check_device(device, now=MORNING)
    first = [e for e in events if e["event_type"] == "usb_first_seen"]
    assert len(first) == 1
    assert first[0]["severity"] == "medium"
    assert first[0]["serial"] == "SN-NEW-1"


def test_known_serial_does_not_re_emit_first_seen(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    device = {"serial": "SN-KNOWN-1", "device_class": "mass_storage",
              "label": "flash drive", "mountpoint": "/media/usb"}
    det.check_device(device, now=MORNING)
    # Fresh detector instance reads the persisted state file.
    det2 = _detector(tmp_path, monkeypatch)
    events = det2.check_device(device, now=MORNING)
    assert "usb_first_seen" not in _types(events)


def test_allowlisted_serial_is_not_flagged_unauthorized(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch, allowlist_content="# comment line\n\nSN-OK-1\n")
    events = det.check_device({"serial": "SN-OK-1", "device_class": "mass_storage",
                               "label": "corp drive", "mountpoint": "/media/usb"},
                              now=MORNING)
    assert "usb_unauthorized" not in _types(events)
    assert "usb_first_seen" in _types(events)  # first-seen still fires


def test_non_allowlisted_serial_emits_high_unauthorized(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    events = det.check_device({"serial": "SN-BAD-1", "device_class": "mass_storage",
                               "label": "unknown drive", "mountpoint": "/media/usb"},
                              now=MORNING)
    unauth = [e for e in events if e["event_type"] == "usb_unauthorized"]
    assert len(unauth) == 1
    assert unauth[0]["severity"] == "high"


def test_first_seen_hid_keyboard_emits_spoof_suspected_high(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    events = det.check_device({"serial": "SN-HID-1", "device_class": "hid_keyboard",
                               "label": "mystery keyboard"},
                              now=MORNING)
    spoof = [e for e in events if e["event_type"] == "usb_hid_spoof_suspected"]
    assert len(spoof) == 1
    assert spoof[0]["severity"] == "high"
    assert "Heuristic" in spoof[0]["reason"]


def test_known_hid_device_does_not_emit_spoof(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    device = {"serial": "SN-HID-2", "device_class": "hid_mouse", "label": "office mouse"}
    det.check_device(device, now=MORNING)
    events = det.check_device(device, now=MORNING)
    assert "usb_hid_spoof_suspected" not in _types(events)


def test_mass_storage_mount_at_night_emits_offhours_medium(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    events = det.check_device({"serial": "SN-NIGHT-1", "device_class": "mass_storage",
                               "label": "flash drive", "mountpoint": "/media/usb"},
                              now=NIGHT)
    off = [e for e in events if e["event_type"] == "usb_offhours_mount"]
    assert len(off) == 1
    assert off[0]["severity"] == "medium"
    assert "09:00-18:00" in off[0]["reason"]


def test_mass_storage_mount_in_hours_emits_no_offhours(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    events = det.check_device({"serial": "SN-DAY-1", "device_class": "mass_storage",
                               "label": "flash drive", "mountpoint": "/media/usb"},
                              now=MORNING)
    assert "usb_offhours_mount" not in _types(events)


def test_device_without_serial_skips_serial_rules(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    events = det.check_device({"serial": None, "device_class": "mass_storage",
                               "label": "unlabeled", "mountpoint": "/media/usb"},
                              now=MORNING)
    types = _types(events)
    assert "usb_first_seen" not in types
    assert "usb_unauthorized" not in types
    assert "usb_hid_spoof_suspected" not in types


def test_events_carry_agent_event_shape(tmp_path, monkeypatch):
    det = _detector(tmp_path, monkeypatch)
    events = det.check_device({"serial": "SN-SHAPE-1", "device_class": "hid_keyboard",
                               "label": "kbd"},
                              now=NIGHT)
    assert events, "expected at least one event"
    for e in events:
        for key in ("event_type", "severity", "reason", "user", "serial",
                    "label", "timestamp", "detail"):
            assert key in e
        assert e["severity"] in ("high", "medium")
        assert e["user"] == "test-user"
