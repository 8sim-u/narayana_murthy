"""
WAMSS workstation DLP signal collector: USB device events and file-write bursts.

Privacy note: this module NEVER reads file content, keystroke content, or
screen content. It only:
  * watches removable-device insert/remove events (device label, time), and
  * counts bytes written (psutil disk IO counters), never which files.

Platform backends:
  * Windows: WMI Win32_VolumeChangeEvent watch via pywin32 (preferred), with
    a psutil.disk_partitions polling fallback when pywin32 is unavailable.
  * Linux: pyudev monitor when installed; otherwise removable-mount polling.
    Without pyudev, remove events for still-mounted devices are detected but
    "unplug while unmounted" cases may be missed.
  * Other: polling only, degrades gracefully (yields nothing on error).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
import time
from typing import Dict, Iterator, List, Optional

try:
    import psutil
except ImportError:  # pragma: no cover - import guard for documentation
    psutil = None  # type: ignore

logger = logging.getLogger("wamss.usb_monitor")

WRITE_THRESHOLD_MB = float(os.getenv("WAMSS_DLP_WRITE_THRESHOLD_MB", "500"))
WRITE_INTERVAL_SEC = float(os.getenv("WAMSS_DLP_WRITE_INTERVAL_SEC", "300"))

_REMOVABLE_MOUNT_ROOTS = ("/media/", "/run/media/", "/mnt/")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _is_removable_partition(part) -> bool:
    """Best-effort removable detection per platform."""
    opts = (getattr(part, "opts", "") or "").lower()
    if sys.platform == "win32":
        # psutil on Windows flags removable drives and CD-ROMs in opts.
        return "removable" in opts or "cdrom" in opts
    mountpoint = getattr(part, "mountpoint", "") or ""
    if mountpoint in ("", "/", "/boot", "/boot/efi"):
        return False
    return mountpoint.startswith(_REMOVABLE_MOUNT_ROOTS)


def snapshot_removable() -> List[Dict[str, str]]:
    """
    Return the current set of removable partitions.

    Each entry: {"mountpoint", "device", "fstype", "label"}. Labels are best
    effort: Windows volume labels when pywin32 is available, otherwise the
    mountpoint basename or device path.
    """
    if psutil is None:
        logger.warning("psutil not installed; cannot snapshot removable devices")
        return []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("disk_partitions failed: %s", type(exc).__name__)
        return []
    result = []
    for part in partitions:
        if not _is_removable_partition(part):
            continue
        label = _volume_label(part.mountpoint) or os.path.basename(
            part.mountpoint.rstrip(os.sep)
        ) or part.device
        result.append(
            {
                "mountpoint": part.mountpoint,
                "device": part.device,
                "fstype": part.fstype,
                "label": label,
            }
        )
    return result


def _volume_label(mountpoint: str) -> Optional[str]:
    """Try to read a Windows volume label via pywin32; None on failure."""
    if sys.platform != "win32":
        return None
    try:
        import win32api

        return win32api.GetVolumeInformation(mountpoint)[0] or None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# USB insert/remove event watchers
# ---------------------------------------------------------------------------


def _iter_wmi_volume_events() -> Iterator[Dict[str, str]]:
    """
    Windows WMI event watch (requires pywin32).

    Listens for Win32_VolumeChangeEvent: EventType 2 = device arrival,
    EventType 3 = device removal. DriveName identifies the volume.
    """
    import win32com.client

    wmi = win32com.client.GetObject("winmgmts:")
    query = "SELECT * FROM Win32_VolumeChangeEvent"
    watcher = wmi.ExecNotificationQuery(query)
    logger.info("WMI volume-change watch started (pywin32)")
    while True:
        try:
            event = watcher.NextEvent()
        except Exception as exc:  # noqa: BLE001
            logger.warning("WMI NextEvent failed: %s", type(exc).__name__)
            break
        event_type = int(getattr(event, "EventType", 0))
        drive = str(getattr(event, "DriveName", ""))
        if event_type == 2:
            yield {"event": "insert", "device": drive, "timestamp": _now_iso()}
        elif event_type == 3:
            yield {"event": "remove", "device": drive, "timestamp": _now_iso()}
        # Other event types (e.g. media change) are ignored.


def _iter_pyudev_events() -> Iterator[Dict[str, str]]:
    """
    Linux pyudev monitor (optional dependency).

    Watches the 'block' subsystem for USB partition add/remove events.
    """
    import pyudev

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="block", device_type="partition")
    monitor.start()
    logger.info("pyudev USB block-device monitor started")
    for action, device in monitor:
        devnode = device.get("DEVNAME") or device.device_path or ""
        label = device.get("ID_FS_LABEL") or devnode
        if action == "add":
            yield {"event": "insert", "device": label, "timestamp": _now_iso()}
        elif action == "remove":
            yield {"event": "remove", "device": label, "timestamp": _now_iso()}


def _iter_polling_events(poll_interval: float = 2.0) -> Iterator[Dict[str, str]]:
    """
    Portable polling fallback: diff the removable-device set every
    poll_interval seconds and emit insert/remove events.
    """
    previous = {d["mountpoint"]: d for d in snapshot_removable()}
    while True:
        time.sleep(poll_interval)
        current = {d["mountpoint"]: d for d in snapshot_removable()}
        for mount, dev in current.items():
            if mount not in previous:
                yield {"event": "insert", "device": dev["label"], "timestamp": _now_iso()}
        for mount, dev in previous.items():
            if mount not in current:
                yield {"event": "remove", "device": dev["label"], "timestamp": _now_iso()}
        previous = current


def iter_usb_events(poll_interval: float = 2.0) -> Iterator[Dict[str, str]]:
    """
    Yield USB insert/remove events as dicts:
    {"event": "insert" | "remove", "device": <label>, "timestamp": <ISO UTC>}.

    Backend selection order:
      * Windows: WMI via pywin32, else polling.
      * Linux: pyudev if installed, else polling.
    Any backend failure degrades to polling; total failure logs and stops.
    """
    if sys.platform == "win32":
        try:
            yield from _iter_wmi_volume_events()
            return
        except ImportError:
            logger.warning("pywin32 not available; using polling fallback")
        except Exception as exc:  # noqa: BLE001
            logger.warning("WMI watch failed (%s); using polling fallback", type(exc).__name__)
    else:
        try:
            yield from _iter_pyudev_events()
            return
        except ImportError:
            logger.info("pyudev not installed; using polling fallback")
        except Exception as exc:  # noqa: BLE001
            logger.warning("pyudev monitor failed (%s); using polling fallback", type(exc).__name__)
    try:
        yield from _iter_polling_events(poll_interval=poll_interval)
    except Exception as exc:  # noqa: BLE001
        logger.error("USB event watch stopped: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# File-write burst signal (byte counts only, never file content)
# ---------------------------------------------------------------------------


class WriteBurstTracker:
    """
    Tracks bytes written per interval to flag bulk exfiltration signals.

    Only aggregate byte counts are used (psutil system-wide disk IO counters
    plus per-process IO counters where permissions allow). File names, paths,
    and contents are never read or recorded.

    Interval and threshold come from env:
      WAMSS_DLP_WRITE_THRESHOLD_MB (default 500)
      WAMSS_DLP_WRITE_INTERVAL_SEC (default 300, i.e. 5 minutes)
    """

    def __init__(
        self,
        interval_sec: Optional[float] = None,
        threshold_mb: Optional[float] = None,
    ) -> None:
        self.interval_sec = interval_sec if interval_sec is not None else WRITE_INTERVAL_SEC
        self.threshold_mb = threshold_mb if threshold_mb is not None else WRITE_THRESHOLD_MB
        self._last_system_bytes: Optional[int] = None
        self._last_process_bytes: Optional[int] = None

    def _system_write_bytes(self) -> int:
        if psutil is None:
            return 0
        counters = psutil.disk_io_counters()
        return int(counters.write_bytes) if counters else 0

    def _process_write_bytes(self) -> int:
        """Sum of write_bytes across accessible processes; 0 when unavailable."""
        if psutil is None:
            return 0
        total = 0
        try:
            for proc in psutil.process_iter(["pid"]):
                try:
                    io = proc.io_counters()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                if io is not None:
                    total += int(getattr(io, "write_bytes", 0))
        except Exception:  # noqa: BLE001
            return 0
        return total

    def bytes_written_per_interval(self) -> Dict[str, float]:
        """
        Sample cumulative counters and return bytes written since the last
        call: {"system_bytes": n, "process_bytes": n, "interval_sec": t}.
        The first call returns zeros (baseline).
        """
        system_now = self._system_write_bytes()
        process_now = self._process_write_bytes()
        if self._last_system_bytes is None:
            delta_system, delta_process = 0, 0
        else:
            delta_system = max(0, system_now - self._last_system_bytes)
            delta_process = max(0, process_now - self._last_process_bytes)
        self._last_system_bytes = system_now
        self._last_process_bytes = process_now
        return {
            "system_bytes": float(delta_system),
            "process_bytes": float(delta_process),
            "interval_sec": float(self.interval_sec),
        }

    def check_burst(self) -> Dict[str, object]:
        """
        Sample and decide whether the write volume exceeds the threshold.
        Returns {"bytes_written_mb", "threshold_mb", "burst": bool}.
        """
        sample = self.bytes_written_per_interval()
        bytes_written = max(sample["system_bytes"], sample["process_bytes"])
        written_mb = bytes_written / (1024 * 1024)
        return {
            "bytes_written_mb": written_mb,
            "threshold_mb": self.threshold_mb,
            "burst": written_mb >= self.threshold_mb,
        }


# ---------------------------------------------------------------------------
# USB anomaly detection: first-seen serials, allowlist, HID-spoof heuristic,
# off-hours mass-storage mounts.
#
# This section classifies enriched device descriptions and emits structured
# anomaly events. A device description is a dict with:
#   {"serial": <usb serial string or None>,
#    "device_class": <"mass_storage"|"hid_keyboard"|"hid_mouse"|"hid"|...>,
#    "label": <human label>, "mountpoint": <path or None>}
#
# Emitted events share a common shape:
#   {"event_type", "severity", "reason", "user", "serial", "label",
#    "timestamp", "detail"}
# with severities "high" / "medium" matching the backend alert vocabulary in
# backend/app/security.py. To ingest these over POST /security/events the
# backend SecurityEventType enum needs the new type values added
# (usb_first_seen, usb_unauthorized, usb_hid_spoof_suspected, usb_offhours_mount).
#
# Serial sources per platform (best effort; no content is ever read):
#   * Linux: pyudev device properties ID_SERIAL_SHORT / ID_SERIAL.
#   * Windows: WMI Win32_DiskDrive.SerialNumber, or the Security log
#     EventID 6416 "new external device" record. Both paths need a real
#     machine and are NOT exercised by the unit tests; they stay documented
#     here until verified on hardware.
#
# Privacy note: only device metadata (serial, class, label, time) is used.
# ---------------------------------------------------------------------------

import getpass
import json as _json

# Env knobs for this section.
WAMSS_USB_ALLOWLIST = os.getenv("WAMSS_USB_ALLOWLIST", "")
WAMSS_USB_STATE = os.getenv("WAMSS_USB_STATE", "")
WAMSS_USB_WORK_HOURS = os.getenv("WAMSS_WORK_HOURS", "09:00-18:00")
WAMSS_USB_TZ = os.getenv("WAMSS_SECURITY_TZ", "UTC")

HID_DEVICE_CLASSES = frozenset({"hid", "hid_keyboard", "hid_mouse"})
MASS_STORAGE_CLASSES = frozenset({"mass_storage", "storage", "disk", "usb_disk"})


def _wamss_config_dir() -> str:
    """Agent config dir; overridable via WAMSS_CONFIG_DIR for tests."""
    return os.getenv(
        "WAMSS_CONFIG_DIR", os.path.join(os.path.expanduser("~"), ".wamss")
    )


def _default_state_path() -> str:
    if WAMSS_USB_STATE:
        return WAMSS_USB_STATE
    return os.path.join(_wamss_config_dir(), "usb_seen.json")


def _default_allowlist_path() -> str:
    if WAMSS_USB_ALLOWLIST:
        return WAMSS_USB_ALLOWLIST
    return os.path.join(_wamss_config_dir(), "usb_allowlist.txt")


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.getenv("USERNAME", os.getenv("USER", "unknown"))


def load_usb_allowlist(path: Optional[str] = None) -> set:
    """
    Load the USB serial allowlist: one serial per line; blank lines and
    lines starting with '#' are ignored. Missing file -> empty set.
    """
    target = path or _default_allowlist_path()
    serials = set()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    serials.add(line)
    except FileNotFoundError:
        logger.info("USB allowlist file not found at %s; allowlist is empty", target)
    except OSError as exc:
        logger.warning("Could not read USB allowlist %s: %s", target, exc)
    return serials


def _parse_hhmm_range(work_hours: str):
    """Parse "HH:MM-HH:MM" into (start_time, end_time); raises ValueError."""
    start_s, end_s = work_hours.split("-")
    start = _dt.datetime.strptime(start_s.strip(), "%H:%M").time()
    end = _dt.datetime.strptime(end_s.strip(), "%H:%M").time()
    return start, end


def _in_work_hours(moment: _dt.datetime, work_hours: str) -> bool:
    """True when moment's time-of-day is inside the work-hours window."""
    start_h, end_h = _parse_hhmm_range(work_hours)
    t = moment.time()
    if start_h <= end_h:
        return start_h <= t < end_h
    return t >= start_h or t < end_h  # overnight window, e.g. 22:00-06:00


def _as_local(ts: _dt.datetime, tz_name: str) -> _dt.datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = _dt.timezone.utc
    return ts.astimezone(tz)


def lookup_device_serial(device_node: Optional[str]) -> Optional[str]:
    """
    Best-effort USB serial lookup for a Linux device node (e.g. /dev/sdb1)
    via pyudev ID_SERIAL_SHORT. Returns None when unavailable. Windows
    callers should use WMI Win32_DiskDrive.SerialNumber instead; that path
    is not implemented here and needs a real Windows machine to verify.
    """
    if not device_node:
        return None
    try:
        import pyudev

        context = pyudev.Context()
        device = pyudev.Devices.from_device_file(context, device_node)
        for key in ("ID_SERIAL_SHORT", "ID_SERIAL"):
            value = device.get(key)
            if value:
                return str(value)
    except Exception:  # noqa: BLE001
        pass
    return None


class UsbAnomalyDetector:
    """
    Stateful USB anomaly classifier for one workstation user.

    * First-seen serials are persisted per user in a small JSON state file
      (default ~/.wamss/usb_seen.json, overridable via WAMSS_USB_STATE).
    * The allowlist (default ~/.wamss/usb_allowlist.txt, overridable via
      WAMSS_USB_ALLOWLIST) is loaded once at startup.
    * Off-hours checks use WAMSS_WORK_HOURS ("HH:MM-HH:MM") as the baseline
      until the backend hours-baselining API (GET /hours/baseline) exists;
      the upgrade path is to fetch the per-user normal window from the
      backend and pass it in place of the env value.
    """

    def __init__(
        self,
        user: Optional[str] = None,
        state_path: Optional[str] = None,
        allowlist_path: Optional[str] = None,
        work_hours: Optional[str] = None,
        tz_name: Optional[str] = None,
    ) -> None:
        self.user = user or _current_user()
        self.state_path = state_path or _default_state_path()
        self.allowlist = load_usb_allowlist(allowlist_path)
        self.work_hours = work_hours or WAMSS_USB_WORK_HOURS
        self.tz_name = tz_name or WAMSS_USB_TZ
        self._seen: Dict[str, str] = {}
        self._load_state()

    # -- persistent first-seen state -------------------------------------
    def _load_state(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
            user_state = data.get(self.user, {}) if isinstance(data, dict) else {}
            self._seen = {str(k): str(v) for k, v in user_state.items()}
        except (FileNotFoundError, ValueError, OSError):
            self._seen = {}

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            try:
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    data = _json.load(fh)
                if not isinstance(data, dict):
                    data = {}
            except (FileNotFoundError, ValueError, OSError):
                data = {}
            data[self.user] = self._seen
            with open(self.state_path, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2)
        except OSError as exc:
            logger.warning("Could not persist USB seen-state: %s", exc)

    def is_first_seen(self, serial: Optional[str]) -> bool:
        """True when serial is non-empty and never recorded for this user."""
        return bool(serial) and serial not in self._seen

    # -- classification --------------------------------------------------
    def _event(
        self,
        event_type: str,
        severity: str,
        reason: str,
        device: Dict[str, object],
        timestamp: str,
    ) -> Dict[str, object]:
        return {
            "event_type": event_type,
            "severity": severity,
            "reason": reason,
            "user": self.user,
            "serial": device.get("serial"),
            "label": device.get("label"),
            "timestamp": timestamp,
            "detail": {
                "device_class": device.get("device_class"),
                "mountpoint": device.get("mountpoint"),
            },
        }

    def check_device(
        self,
        device: Dict[str, object],
        now: Optional[_dt.datetime] = None,
    ) -> List[Dict[str, object]]:
        """
        Classify one device description and return anomaly events (possibly
        empty). Rules, in order:

        * serial never seen for this user -> usb_first_seen (medium); the
          serial is recorded immediately (write-through).
        * serial not on the allowlist -> usb_unauthorized (high).
        * device presents as HID keyboard/mouse AND is first-seen ->
          usb_hid_spoof_suspected (high). HEURISTIC: a legitimate new
          keyboard also fires; this is a triage signal for BadUSB-style
          spoofing, not proof of an attack. Human verification required.
        * mass-storage mount outside the baseline work-hours window ->
          usb_offhours_mount (medium).

        `now` is injectable for tests; defaults to current UTC time.
        """
        events: List[Dict[str, object]] = []
        timestamp = str(device.get("timestamp") or _now_iso())
        serial = device.get("serial")
        serial_str = str(serial) if serial else None
        device_class = str(device.get("device_class") or "").lower()
        label = device.get("label") or "unknown device"

        first_seen = self.is_first_seen(serial_str)

        if first_seen:
            assert serial_str is not None
            events.append(
                self._event(
                    "usb_first_seen",
                    "medium",
                    f"USB device seen for the first time for user {self.user}: {label}",
                    device,
                    timestamp,
                )
            )
            self._seen[serial_str] = timestamp
            self._save_state()

        if serial_str and serial_str not in self.allowlist:
            events.append(
                self._event(
                    "usb_unauthorized",
                    "high",
                    f"USB device serial not on the allowlist: {label} (serial {serial_str})",
                    device,
                    timestamp,
                )
            )

        if device_class in HID_DEVICE_CLASSES and first_seen:
            events.append(
                self._event(
                    "usb_hid_spoof_suspected",
                    "high",
                    "First-seen device presents as HID keyboard/mouse; possible "
                    "BadUSB spoofing. Heuristic only: verify the physical device.",
                    device,
                    timestamp,
                )
            )

        is_storage = device_class in MASS_STORAGE_CLASSES or bool(device.get("mountpoint"))
        if is_storage:
            moment = now or _dt.datetime.now(_dt.timezone.utc)
            local = _as_local(moment, self.tz_name)
            try:
                inside = _in_work_hours(local, self.work_hours)
            except ValueError:
                logger.warning("Bad WAMSS_WORK_HOURS %r; skipping off-hours check", self.work_hours)
                inside = True
            if not inside:
                events.append(
                    self._event(
                        "usb_offhours_mount",
                        "medium",
                        f"Mass-storage device mounted outside work hours "
                        f"({self.work_hours} {self.tz_name}): {label}",
                        device,
                        timestamp,
                    )
                )

        return events


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def self_test() -> int:
    print("== WAMSS usb_monitor self-test ==")
    print(f"platform: {sys.platform}")
    print(f"psutil: {'available' if psutil is not None else 'MISSING'}")
    try:
        import pyudev  # noqa: F401

        print("pyudev: available")
    except ImportError:
        print("pyudev: not installed (Linux monitor degrades to polling)")
    if sys.platform == "win32":
        try:
            import win32com.client  # noqa: F401

            print("pywin32: available")
        except ImportError:
            print("pywin32: not installed (Windows monitor uses polling fallback)")

    print("\n-- removable-device snapshot --")
    devices = snapshot_removable()
    if not devices:
        print("(no removable devices currently mounted)")
    for d in devices:
        print(f"  mount={d['mountpoint']} device={d['device']} fstype={d['fstype']} label={d['label']}")

    print("\n-- simulated insert/remove pair (no real hardware touched) --")
    now = _now_iso()
    simulated = [
        {"event": "insert", "device": "TEST-USB-8GB", "timestamp": now},
        {"event": "remove", "device": "TEST-USB-8GB", "timestamp": now},
    ]
    for ev in simulated:
        print(f"  {ev}")

    print("\n-- write-burst tracker (env-driven thresholds) --")
    tracker = WriteBurstTracker(
        interval_sec=float(os.getenv("WAMSS_DLP_WRITE_INTERVAL_SEC", "0.5")),
        threshold_mb=float(os.getenv("WAMSS_DLP_WRITE_THRESHOLD_MB", "0.001")),
    )
    tracker.bytes_written_per_interval()  # baseline
    time.sleep(0.5)
    result = tracker.check_burst()
    print(
        f"  bytes_written_mb={result['bytes_written_mb']:.4f} "
        f"threshold_mb={result['threshold_mb']} burst={result['burst']}"
    )
    print("\nself-test OK: no file content or keystrokes were read.")

    print("\n-- USB anomaly detector (synthetic devices, temp state dir) --")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        detector = UsbAnomalyDetector(
            user="selftest-user",
            state_path=os.path.join(tmp, "usb_seen.json"),
            allowlist_path=os.path.join(tmp, "allowlist.txt"),
            work_hours="09:00-18:00",
            tz_name="UTC",
        )
        demo_devices = [
            {"serial": "DEMO-NEW-1", "device_class": "mass_storage",
             "label": "DEMO flash drive", "mountpoint": "/media/demo"},
            {"serial": "DEMO-HID-1", "device_class": "hid_keyboard",
             "label": "DEMO keyboard"},
        ]
        for dev in demo_devices:
            for ev in detector.check_device(dev):
                print(f"  {ev['event_type']} [{ev['severity']}] {ev['reason']}")
    print("anomaly demo OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WAMSS USB/DLP signal monitor")
    parser.add_argument("--self-test", action="store_true", help="print snapshot and simulated events, then exit")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="polling interval seconds for the fallback watcher",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    logger.info("watching USB events (Ctrl+C to stop)")
    try:
        for event in iter_usb_events(poll_interval=args.poll_interval):
            print(event, flush=True)
    except KeyboardInterrupt:
        logger.info("stopped by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
