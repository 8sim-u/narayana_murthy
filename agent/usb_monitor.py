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
