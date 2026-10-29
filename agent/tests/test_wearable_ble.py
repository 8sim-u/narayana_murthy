"""
Unit tests for agent/wearable_ble.py.

Pure-Python tests with a mocked bleak client. No BLE adapter, no real
device, no network. Covers:
  - 8-bit and 16-bit Heart Rate Measurement payload parsing
  - malformed payloads returning None (no exceptions)
  - the notification path via a fake async client factory
  - graceful degradation: disabled by default, device not found -> available=False
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import wearable_ble
from wearable_ble import HeartRateStream, parse_heart_rate


# ---------------------------------------------------------------- parsing

def test_parse_8bit():
    # flags bit 0 = 0 -> BPM is the single byte that follows
    assert parse_heart_rate(bytes([0x00, 72])) == 72
    assert parse_heart_rate(bytes([0x00, 58])) == 58
    # extra trailing bytes (energy expended / RR intervals) are ignored
    assert parse_heart_rate(bytes([0x00, 72, 0x10, 0x20, 0x30])) == 72


def test_parse_16bit_little_endian():
    # flags bit 0 = 1 -> BPM is a 16-bit little-endian uint in bytes 1-2
    assert parse_heart_rate(bytes([0x01, 0x64, 0x00])) == 100  # 0x0064
    assert parse_heart_rate(bytes([0x01, 0x2B, 0x01])) == 299  # 0x012B
    assert parse_heart_rate(bytes([0x01, 0x00, 0x01])) == 256  # 0x0100
    assert parse_heart_rate(bytes([0x01, 0xFF, 0x00])) == 255  # 0x00FF


def test_parse_malformed_returns_none():
    assert parse_heart_rate(b"") is None
    assert parse_heart_rate(bytes([0x00])) is None            # flags only, no value
    assert parse_heart_rate(bytes([0x01, 0x64])) is None      # 16-bit flag, short
    assert parse_heart_rate(None) is None
    assert parse_heart_rate("not-bytes") is None
    assert parse_heart_rate(bytes([0x00, 0])) is None         # out of sanity range
    assert parse_heart_rate(bytes([0x01, 0x2C, 0x01])) is None  # 300, out of range


# ---------------------------------------------------------------- fake client

class FakeBleakClient:
    """Minimal async-CM stand-in for BleakClient used by the tests."""

    def __init__(self, address, payloads):
        self.address = address
        self._payloads = payloads
        self.notified = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def start_notify(self, uuid, callback):
        self.notified = True
        assert uuid == wearable_ble.HR_MEASUREMENT_UUID
        for payload in self._payloads:
            callback(1, payload)  # sender handle is irrelevant here

    async def stop_notify(self, uuid):
        pass


def _run_stream(payloads, device="AA:BB:CC:DD:EE:FF", enabled=True):
    received = []
    factory = lambda addr: FakeBleakClient(addr, payloads)  # noqa: E731
    env = {"WAMSS_BLE_ENABLED": "1" if enabled else "0"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        stream = HeartRateStream(on_bpm=received.append, device=device,
                                 interval=0.05, client_factory=factory)
        stream.start()
        # Wait for the background thread to process the fake notifications.
        deadline = time.time() + 5.0
        while time.time() < deadline and not stream.available:
            time.sleep(0.02)
        time.sleep(0.1)
        # Snapshot before stop(): stop() deliberately clears `available`.
        snapshot = (stream.available, stream.latest_bpm)
        stream.stop()
        return snapshot, received
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_notification_path_sets_bpm_and_available():
    payloads = [bytes([0x00, 72]), bytes([0x01, 0x64, 0x00]), bytes([0x00, 81])]
    (available, latest_bpm), received = _run_stream(payloads)
    assert available is True
    assert latest_bpm == 81
    assert received == [72, 100, 81]


def test_malformed_notification_does_not_set_available():
    (available, latest_bpm), received = _run_stream([b"", bytes([0x01])])
    assert available is False
    assert latest_bpm is None
    assert received == []


def test_disabled_by_default_leaves_stream_idle():
    (available, latest_bpm), _received = _run_stream([bytes([0x00, 72])], enabled=False)
    assert available is False
    assert latest_bpm is None


def test_disabled_start_spawns_no_thread():
    old = os.environ.get("WAMSS_BLE_ENABLED")
    os.environ["WAMSS_BLE_ENABLED"] = "0"
    try:
        stream = HeartRateStream(client_factory=lambda addr: FakeBleakClient(addr, []))
        stream.start()
        assert stream._thread is None
        stream.stop()
    finally:
        if old is None:
            os.environ.pop("WAMSS_BLE_ENABLED", None)
        else:
            os.environ["WAMSS_BLE_ENABLED"] = old


def test_device_not_found_is_graceful(monkeypatch):
    monkeypatch.setattr(wearable_ble, "scan", lambda timeout=5.0: [])
    received = []
    monkeypatch.setenv("WAMSS_BLE_ENABLED", "1")
    stream = HeartRateStream(on_bpm=received.append, device="",
                             interval=0.05,
                             client_factory=lambda addr: FakeBleakClient(addr, []))
    stream.start()
    time.sleep(0.3)
    stream.stop()
    assert stream.available is False
    assert stream.latest_bpm is None
    assert received == []


def test_scan_without_bleak_returns_empty(monkeypatch):
    monkeypatch.setattr(wearable_ble, "_BLEAK_AVAILABLE", False)
    assert wearable_ble.scan(timeout=0.1) == []
