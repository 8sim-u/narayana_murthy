"""
Optional BLE heart-rate wearable integration for the WAMSS workstation agent.

Reads heart-rate measurements from a BLE smartwatch / chest strap over the
standard Bluetooth SIG Heart Rate service (0x180D) and Heart Rate
Measurement characteristic (0x2A37), using the `bleak` library.

The module is strictly optional:
  - Master switch is WAMSS_BLE_ENABLED (0/1, default 0).
  - If bleak is not installed, no adapter is present, or the device is not
    found, the stream logs a warning and exposes available=False so the
    caller falls back to heuristic-only strain scoring.

Config (env vars, see agent/.env.example):
  WAMSS_BLE_ENABLED   "1" to enable, default "0" (disabled).
  WAMSS_BLE_DEVICE    device name substring or MAC address to connect to.
                      Default "": connect to the first device found that
                      advertises the Heart Rate service.
  WAMSS_BLE_INTERVAL  keep-alive / reconnect check cadence in seconds,
                      default 60.

Heart Rate Measurement payload layout (Bluetooth SIG, characteristic
0x2A37):
  byte 0: flags. bit 0 = 0 -> heart-rate value is an 8-bit uint in byte 1.
                  bit 0 = 1 -> heart-rate value is a 16-bit uint,
                  little-endian, in bytes 1-2.
  Remaining bytes (energy expended, RR intervals) are ignored here.

This is a heuristic workload signal fed into the workstation strain score.
It is NOT a medical measure of stress, fatigue, or heart health.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("wamss.wearable")

try:
    from bleak import BleakClient, BleakScanner
    _BLEAK_AVAILABLE = True
except ImportError:  # bleak not installed: module degrades to unavailable
    BleakClient = None  # type: ignore
    BleakScanner = None  # type: ignore
    _BLEAK_AVAILABLE = False

# Standard Bluetooth SIG UUIDs (16-bit forms expanded to 128-bit).
HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _ble_enabled() -> bool:
    return os.environ.get("WAMSS_BLE_ENABLED", "0") == "1"


def parse_heart_rate(data: bytes | bytearray) -> Optional[int]:
    """
    Parse beats-per-minute from a raw Heart Rate Measurement notification.

    Handles both 8-bit (flags bit 0 = 0) and 16-bit little-endian
    (flags bit 0 = 1) formats per the flags byte. Returns None for
    malformed payloads instead of raising.
    """
    try:
        buf = bytes(data)
    except (TypeError, ValueError):
        return None
    if len(buf) < 2:
        return None
    flags = buf[0]
    is_16bit = bool(flags & 0x01)
    if is_16bit:
        if len(buf) < 3:
            return None
        bpm = int.from_bytes(buf[1:3], byteorder="little")
    else:
        bpm = buf[1]
    if not 0 < bpm < 300:  # sanity range for a wrist-worn sensor
        return None
    return bpm


def scan(timeout: float = 5.0) -> List[Dict[str, str]]:
    """
    Scan for BLE devices advertising the Heart Rate service (0x180D).

    Returns a list of {"address": ..., "name": ...} dicts. Empty list when
    bleak is missing or no adapter is present. Raises nothing; errors are
    logged and an empty list is returned.
    """
    if not _BLEAK_AVAILABLE:
        logger.warning("BLE scan requested but the 'bleak' package is not installed.")
        return []

    async def _do_scan() -> List[Dict[str, str]]:
        found: List[Dict[str, str]] = []
        try:
            devices = await BleakScanner.discover(timeout=timeout)
        except Exception as exc:  # no adapter, permissions, etc.
            logger.warning("BLE scan failed: %s", exc)
            return []
        for dev in devices:
            uuids = set()
            try:
                meta = getattr(dev, "metadata", {}) or {}
                uuids = {str(u).lower() for u in meta.get("uuids", [])}
            except Exception:
                pass
            advertises_hr = HR_SERVICE_UUID in uuids or "180d" in uuids
            name = dev.name or ""
            if advertises_hr or (name and "hr" in name.lower()) or not name:
                found.append({"address": dev.address, "name": name or dev.address})
        # Prefer devices that explicitly advertise the Heart Rate service.
        return found

    try:
        return asyncio.run(_do_scan())
    except RuntimeError as exc:  # e.g. an event loop is already running here
        logger.warning("BLE scan aborted: %s", exc)
        return []


class HeartRateStream:
    """
    Background heart-rate stream from a BLE wearable.

    Usage:
        stream = HeartRateStream()
        stream.start()                      # reads WAMSS_BLE_DEVICE / callback from env
        ...
        bpm = stream.latest_bpm             # None until a reading arrives
        if stream.available: ...            # False -> use heuristic-only scoring
        stream.stop()

    The BLE work runs in a dedicated daemon thread with its own asyncio
    event loop; start() returns immediately. When the connection drops the
    thread retries every WAMSS_BLE_INTERVAL seconds until stop() is called.
    """

    def __init__(
        self,
        on_bpm: Optional[Callable[[int], None]] = None,
        device: Optional[str] = None,
        interval: Optional[float] = None,
        client_factory: Optional[Callable[[str], object]] = None,
    ) -> None:
        self._on_bpm = on_bpm
        self._device = device if device is not None else os.environ.get("WAMSS_BLE_DEVICE", "")
        self._interval = interval if interval is not None else _getenv_int("WAMSS_BLE_INTERVAL", 60)
        # client_factory(address) -> async context manager BleakClient-like.
        # Injectable so unit tests can feed fake notifications.
        self._factory_is_default = client_factory is None
        self._client_factory = client_factory or (lambda addr: BleakClient(addr))
        self.latest_bpm: Optional[int] = None
        self.available: bool = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_reading_at: float = 0.0

    # ------------------------------------------------------------- control

    def start(
        self,
        device_name_or_mac: Optional[str] = None,
        on_bpm: Optional[Callable[[int], None]] = None,
    ) -> "HeartRateStream":
        """Begin scanning/connecting in a background thread. Returns self."""
        if device_name_or_mac:
            self._device = device_name_or_mac
        if on_bpm is not None:
            self._on_bpm = on_bpm
        if not _ble_enabled():
            logger.info("WAMSS_BLE_ENABLED is not 1: BLE heart-rate stream stays disabled.")
            return self
        if not _BLEAK_AVAILABLE and self._factory_is_default:
            # The real bleak factory needs the real library; an injected
            # factory (tests) does not.
            logger.warning("WAMSS_BLE_ENABLED=1 but 'bleak' is not installed; "
                           "wearable heart-rate is unavailable.")
            return self
        if self._thread and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._thread_main, name="wamss-hr", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Signal the background thread to disconnect and exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        self.available = False

    # ------------------------------------------------------------- internals

    def _handle_notification(self, _sender: int, data: bytes | bytearray) -> None:
        bpm = parse_heart_rate(data)
        if bpm is None:
            logger.debug("Ignoring malformed heart-rate payload of %d bytes.", len(bytes(data)) if data is not None else 0)
            return
        self.latest_bpm = bpm
        self.available = True
        self._last_reading_at = time.time()
        if self._on_bpm:
            try:
                self._on_bpm(bpm)
            except Exception:
                logger.exception("on_bpm callback raised; ignoring.")

    async def _resolve_address(self) -> Optional[str]:
        """Turn WAMSS_BLE_DEVICE (name substring or MAC) into a BLE address."""
        want = (self._device or "").strip().lower()
        if not want:
            # Auto-pick: first device advertising the Heart Rate service.
            candidates = scan(timeout=8.0)
            if not candidates:
                logger.warning("No BLE heart-rate device found during scan.")
                return None
            logger.info("Auto-selected BLE device %s (%s).",
                        candidates[0]["name"], candidates[0]["address"])
            return candidates[0]["address"]
        if ":" in want or want.replace("-", "").replace(":", "").isalnum() and len(want) >= 12:
            return self._device  # looks like a MAC address; use as-is
        candidates = scan(timeout=8.0)
        for cand in candidates:
            if want in (cand["name"] or "").lower():
                logger.info("Matched BLE device '%s' -> %s.", cand["name"], cand["address"])
                return cand["address"]
        logger.warning("BLE device '%s' not found during scan.", self._device)
        return None

    async def _run_once(self) -> None:
        address = await self._resolve_address()
        if not address:
            return
        try:
            async with self._client_factory(address) as client:  # type: ignore
                logger.info("Connected to BLE heart-rate device %s.", address)
                await client.start_notify(HR_MEASUREMENT_UUID, self._handle_notification)
                while not self._stop_event.is_set():
                    await asyncio.sleep(1.0)
                try:
                    await client.stop_notify(HR_MEASUREMENT_UUID)
                except Exception:
                    pass
        except Exception as exc:
            self.available = False
            logger.warning("BLE heart-rate connection failed (%s); retrying in %ss.",
                           exc, self._interval)

    def _thread_main(self) -> None:
        asyncio.run(self._connect_loop())

    async def _connect_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_once()
            except Exception:
                logger.exception("Unexpected BLE loop error.")
            if self._stop_event.wait(self._interval):
                break
        self.available = False


def make_stream_from_env(
    on_bpm: Optional[Callable[[int], None]] = None,
) -> HeartRateStream:
    """Convenience constructor that reads WAMSS_BLE_* env vars."""
    return HeartRateStream(on_bpm=on_bpm)
