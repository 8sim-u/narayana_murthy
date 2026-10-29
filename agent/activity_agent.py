"""
WAMSS workstation activity agent: smartwatch-style always-on logger.

Collects per activity segment: active application name, window title, process
name, start/end timestamps, input activity counts (keystroke and mouse counts
per interval; counts only, NEVER keystroke content), idle periods, machine id,
user id, plus a heuristic strain (workload tension) score.

Pipeline:
  collect -> local spool (JSONL, crash-safe) -> periodic batched sync to the
  backend -> retry with exponential backoff when the server is unreachable.

Runs headless as a background service. Configured entirely through env vars;
see agent/.env.example.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Optional, Tuple

import requests

from strain import StrainConfig, compute_strain_score

try:
    from notifier import notify_break
except ImportError:  # pragma: no cover - defensive
    def notify_break(title, message, notifier=None):  # type: ignore
        print(f"[WAMSS] {title}: {message}")
        return "log"

try:
    from consent import ensure_consent
except ImportError:  # pragma: no cover - defensive
    def ensure_consent() -> bool:  # type: ignore
        logger.warning("consent module unavailable; skipping consent gate")
        return True

try:
    from wearable_ble import HeartRateStream
except ImportError:  # pragma: no cover - optional BLE module
    HeartRateStream = None  # type: ignore

logger = logging.getLogger("wamss.agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# ---------------------------------------------------------------- config

def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _getenv_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


SERVER_URL = os.environ.get("WAMSS_AGENT_SERVER_URL", "http://localhost:8000").rstrip("/")
MACHINE_ID = os.environ.get("WAMSS_MACHINE_ID", socket.gethostname())
EMPLOYEE_ID = os.environ.get("WAMSS_EMPLOYEE_ID", os.environ.get("USER", "unknown"))
LOG_INTERVAL = _getenv_float("WAMSS_LOG_INTERVAL", 60.0)          # seconds per activity segment
SYNC_INTERVAL = _getenv_float("WAMSS_SYNC_INTERVAL", 300.0)       # seconds between upload attempts
IDLE_AFTER_SEC = _getenv_float("WAMSS_IDLE_AFTER_SEC", 180.0)     # no input -> idle segment
SPOOL_DIR = os.environ.get("WAMSS_SPOOL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "spool"))
MAX_BATCH = _getenv_int("WAMSS_MAX_BATCH", 500)                   # records per POST
MAX_BACKOFF_SEC = _getenv_float("WAMSS_MAX_BACKOFF_SEC", 3600.0)
SCREENSHOTS_ENABLED = os.environ.get("WAMSS_SCREENSHOTS", "0") == "1"  # reserved; must stay 0 unless explicitly enabled

# Privacy exclusion list: personal apps/windows that are never logged,
# not even to the local spool. Case-insensitive process/app names.
EXCLUDED_APPS = {a.strip().lower() for a in os.environ.get("WAMSS_EXCLUDED_APPS", "").split(",") if a.strip()}
EXCLUDED_TITLES_RE = os.environ.get("WAMSS_EXCLUDED_TITLES", "").strip()

# Optional smartwatch heart rate blended into the strain score.
HR_RESTING_BPM = _getenv_int("WAMSS_STRAIN_HR_RESTING", 90)

STRAIN_CFG = StrainConfig.from_env()


def _is_excluded(app_name: str, window_title: str, process_name: str) -> bool:
    """True if this window is on the privacy exclusion list (never logged)."""
    if app_name.lower() in EXCLUDED_APPS or process_name.lower() in EXCLUDED_APPS:
        return True
    if EXCLUDED_TITLES_RE:
        try:
            if re.search(EXCLUDED_TITLES_RE, window_title):
                return True
        except re.error:
            pass
    return False


# ---------------------------------------------------------------- active window detection

def _active_window_windows() -> Tuple[str, str, str]:
    """Returns (app_name, window_title, process_name) on Windows."""
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
        import psutil
    except ImportError:
        return _active_window_windows_ctypes()
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        return proc.name(), title, proc.name()
    except Exception:
        return "", "", ""


def _active_window_windows_ctypes() -> Tuple[str, str, str]:
    """ctypes fallback for Windows when pywin32 is unavailable."""
    try:
        import ctypes
        import psutil
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = psutil.Process(pid.value).name() if pid.value else ""
        return name, title, name
    except Exception:
        return "", "", ""


def _active_window_linux() -> Tuple[str, str, str]:
    """
    Best-effort Linux detection via python-xlib. If X11/xlib is unavailable
    (Wayland, headless), degrades gracefully to empty strings.
    """
    try:
        from Xlib import display  # type: ignore
        import psutil
    except ImportError:
        return "", "", ""
    try:
        d = display.Display()
        root = d.screen().root
        NET_ACTIVE = d.intern_atom("_NET_ACTIVE_WINDOW")
        win_id = root.get_full_property(NET_ACTIVE, 0)
        if not win_id:
            return "", "", ""
        win = d.create_resource_object("window", win_id.value[0])
        title = win.get_wm_name() or ""
        pid_prop = win.get_full_property(d.intern_atom("_NET_WM_PID"), 0)
        name = ""
        if pid_prop:
            try:
                name = psutil.Process(int(pid_prop.value[0])).name()
            except Exception:
                name = ""
        return name, title, name
    except Exception:
        return "", "", ""


def get_active_window() -> Tuple[str, str, str]:
    system = platform.system()
    if system == "Windows":
        return _active_window_windows()
    if system == "Linux":
        return _active_window_linux()
    return "", "", ""  # macOS and others: not implemented, degrade gracefully


# ---------------------------------------------------------------- input counting (counts only, no content)

class InputCounter:
    """
    Counts keystrokes and mouse clicks per interval using pynput listeners.

    PRIVACY: only event counts are recorded. No key content, no key names,
    no mouse coordinates, no screenshots are captured by this class.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.keystrokes = 0
        self.mouse_clicks = 0
        self.last_input_ts = time.time()
        self._listeners: list = []

    def _on_key(self, _key) -> None:
        # The key object is deliberately ignored: count only.
        with self._lock:
            self.keystrokes += 1
            self.last_input_ts = time.time()

    def _on_click(self, _x, _y, _button, _pressed) -> None:
        if not _pressed:
            return
        with self._lock:
            self.mouse_clicks += 1
            self.last_input_ts = time.time()

    def start(self) -> None:
        try:
            from pynput import keyboard, mouse  # type: ignore
        except Exception as exc:
            logger.warning("pynput unavailable, input counting disabled: %s", exc)
            return
        kl = keyboard.Listener(on_press=self._on_key)
        ml = mouse.Listener(on_click=self._on_click)
        kl.daemon = True
        ml.daemon = True
        kl.start()
        ml.start()
        self._listeners = [kl, ml]

    def drain(self) -> Tuple[int, int]:
        with self._lock:
            k, m = self.keystrokes, self.mouse_clicks
            self.keystrokes, self.mouse_clicks = 0, 0
        return k, m

    def idle_seconds(self) -> float:
        with self._lock:
            return time.time() - self.last_input_ts


# ---------------------------------------------------------------- spool (local JSONL buffer)

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Spool:
    """Append-only JSONL spool; records stay local until a sync succeeds."""

    def __init__(self, spool_dir: str = SPOOL_DIR) -> None:
        self.dir = spool_dir
        os.makedirs(self.dir, exist_ok=True)
        self.pending_path = os.path.join(self.dir, "pending.jsonl")
        self.sent_path = os.path.join(self.dir, "sent.jsonl")
        self._lock = threading.Lock()

    def append(self, record: Dict) -> None:
        line = json.dumps(record, default=str)
        with self._lock, open(self.pending_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_batch(self, max_records: int = MAX_BATCH) -> list:
        with self._lock:
            if not os.path.exists(self.pending_path):
                return []
            with open(self.pending_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        return [json.loads(l) for l in lines[:max_records] if l.strip()]

    def mark_sent(self, count: int) -> None:
        """Drop the first `count` pending lines; archive them to sent.jsonl."""
        with self._lock:
            if not os.path.exists(self.pending_path):
                return
            with open(self.pending_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            sent, remaining = lines[:count], lines[count:]
            with open(self.sent_path, "a", encoding="utf-8") as f:
                f.writelines(sent)
            tmp = self.pending_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(remaining)
            os.replace(tmp, self.pending_path)

    def pending_count(self) -> int:
        try:
            with open(self.pending_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except FileNotFoundError:
            return 0


# ---------------------------------------------------------------- sync with exponential backoff

class Syncer:
    def __init__(self, spool: Spool, server_url: str = SERVER_URL) -> None:
        self.spool = spool
        self.server_url = server_url
        self.backoff = 30.0
        self.session = requests.Session()

    def sync_once(self) -> bool:
        batch = self.spool.read_batch()
        if not batch:
            self.backoff = 30.0
            return True
        try:
            resp = self.session.post(
                f"{self.server_url}/activity",
                json={"records": batch},
                timeout=15,
            )
            resp.raise_for_status()
            self.spool.mark_sent(len(batch))
            self.backoff = 30.0
            logger.info("synced %d activity records", len(batch))
            return True
        except Exception as exc:
            logger.warning("sync failed (%s); backing off %.0fs, %d records spooled locally",
                           exc, self.backoff, self.spool.pending_count())
            self.backoff = min(self.backoff * 2, MAX_BACKOFF_SEC)
            return False

    def next_delay(self) -> float:
        return self.backoff


# ---------------------------------------------------------------- strain tracking + break reminders

class StrainTracker:
    """
    Maintains the rolling signals the strain formula needs and decides when
    to nudge the employee to take a break.

    Break trigger: strain score >= WAMSS_BREAK_THRESHOLD. On trigger the
    agent shows a desktop toast (pluggable notifier) and writes a
    ``break_suggested`` record into the local spool so it flows through the
    normal /activity ingest path. Repeat reminders are suppressed for
    WAMSS_BREAK_SNOOZE_MIN minutes (snooze) and at least
    WAMSS_BREAK_REMINDER_COOLDOWN_MIN minutes apart.
    """

    def __init__(self, cfg: StrainConfig = STRAIN_CFG,
                 notifier: Optional[Callable[[str, str], None]] = None) -> None:
        self.cfg = cfg
        self.notifier = notifier
        self.input_events: Deque[Tuple[float, int]] = deque()  # (ts, count) over 60 s
        self.switch_events: Deque[float] = deque()             # switch ts over 5 min
        self.active_start_ts: Optional[float] = None           # continuous-active clock
        self.last_switch_key: Optional[str] = None
        self.last_reminder_ts: Optional[float] = None
        self.snoozed_until: Optional[float] = None

    # -- signal maintenance, called once per log interval -----------------

    def observe(self, keystrokes: int, mouse_clicks: int, window_key: str,
                is_idle: bool, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        self.input_events.append((now, keystrokes + mouse_clicks))
        while self.input_events and now - self.input_events[0][0] > 60:
            self.input_events.popleft()
        if window_key != self.last_switch_key:
            if self.last_switch_key is not None:
                self.switch_events.append(now)
            self.last_switch_key = window_key
        while self.switch_events and now - self.switch_events[0] > 300:
            self.switch_events.popleft()
        if is_idle:
            # An idle stretch of break_reset_idle_min resets the continuous clock.
            if self.active_start_ts is not None and (now - self.active_start_ts) / 60 >= 0:
                pass  # keep clock; reset happens in tick() once idle is long enough
        else:
            if self.active_start_ts is None:
                self.active_start_ts = now

    def reset_active_clock_if_rested(self, idle_seconds: float, now: Optional[float] = None) -> None:
        if idle_seconds >= self.cfg.break_reset_idle_min * 60:
            self.active_start_ts = None

    def signals(self, now: Optional[float] = None):
        now = now if now is not None else time.time()
        per_min = float(sum(c for _, c in self.input_events))
        active_minutes = ((now - self.active_start_ts) / 60.0) if self.active_start_ts else 0.0
        return compute_strain_score(
            active_minutes=active_minutes,
            input_events_per_min=per_min,
            switches_per_5min=len(self.switch_events),
            outside_work_hours=self.cfg.outside_work_hours(),
            cfg=self.cfg,
        )

    # -- break trigger ----------------------------------------------------

    def maybe_trigger_break(self, spool: Spool, now: Optional[float] = None) -> Optional[Dict]:
        """
        If the strain score crosses the break threshold and we are not inside
        a snooze/cooldown window, show the reminder and spool a
        ``break_suggested`` record. Returns the record, or None if no trigger.
        """
        now = now if now is not None else time.time()
        result = self.signals(now)
        if result.score < self.cfg.break_threshold:
            return None
        if self.snoozed_until is not None and now < self.snoozed_until:
            return None
        if (self.last_reminder_ts is not None
                and now - self.last_reminder_ts < self.cfg.break_reminder_cooldown_min * 60):
            return None

        title = "Time for a break"
        message = (f"Your workload strain is {result.score}/100 ({result.level}). "
                   f"Consider stepping away for a few minutes. "
                   f"This is a wellness nudge, not a medical assessment.")
        notify_break(title, message, notifier=self.notifier)

        record = {
            "record_id": str(uuid.uuid4()),
            "machine_id": MACHINE_ID,
            "employee_id": EMPLOYEE_ID,
            "event_type": "break_suggested",
            "app_name": "wamss-agent",
            "window_title": f"break_suggested score={result.score} level={result.level}",
            "process_name": "wamss-agent",
            "start_time": _utcnow_iso(),
            "end_time": _utcnow_iso(),
            "keystrokes": 0,
            "mouse_clicks": 0,
            "is_idle": False,
            "strain_score": result.score,
        }
        spool.append(record)
        self.last_reminder_ts = now
        self.snoozed_until = now + self.cfg.break_snooze_min * 60
        logger.info("break reminder triggered (score=%d level=%s)", result.score, result.level)
        return record


# ---------------------------------------------------------------- collector loop

def collect_once(counter: InputCounter, tracker: StrainTracker, hr_stream=None) -> Optional[Dict]:
    app_name, window_title, process_name = get_active_window()
    if _is_excluded(app_name, window_title, process_name):
        return None  # privacy exclusion: never logged, not even spooled
    keystrokes, mouse_clicks = counter.drain()
    idle_sec = counter.idle_seconds()
    is_idle = idle_sec >= IDLE_AFTER_SEC

    end = datetime.now(timezone.utc)
    start = end  # segment granularity is LOG_INTERVAL; start=end replaced by caller cadence

    tracker.reset_active_clock_if_rested(idle_sec)
    window_key = f"{process_name}\x00{window_title}"
    tracker.observe(keystrokes, mouse_clicks, window_key, is_idle)
    strain = tracker.signals()

    # Blend optional smartwatch heart rate into the strain score:
    # elevated BPM adds at most 10 points (+1 per 2 BPM over resting).
    score = strain.score
    bpm = hr_stream.latest_bpm if (hr_stream is not None and hr_stream.available) else None
    if bpm is not None and bpm > HR_RESTING_BPM:
        score = min(100, score + min(10, (bpm - HR_RESTING_BPM) // 2))

    return {
        "record_id": str(uuid.uuid4()),
        "machine_id": MACHINE_ID,
        "employee_id": EMPLOYEE_ID,
        "event_type": "activity",
        "app_name": app_name[:256],
        "window_title": window_title[:512],
        "process_name": process_name[:256],
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "keystrokes": keystrokes,
        "mouse_clicks": mouse_clicks,
        "is_idle": is_idle,
        "strain_score": score,
    }


def run(stop_after: Optional[float] = None,
        notifier: Optional[Callable[[str, str], None]] = None) -> None:
    """
    Main loop. stop_after (seconds) is for dry runs; None means run forever.
    """
    if not ensure_consent():
        raise SystemExit("WAMSS agent stopped: consent not given.")

    os.makedirs(SPOOL_DIR, exist_ok=True)
    spool = Spool(SPOOL_DIR)
    syncer = Syncer(spool)
    counter = InputCounter()
    counter.start()
    tracker = StrainTracker(notifier=notifier)

    hr_stream = None
    if HeartRateStream is not None:
        try:
            hr_stream = HeartRateStream().start()
        except Exception as exc:
            logger.warning("BLE heart-rate stream failed to start: %s", exc)
            hr_stream = None

    logger.info("WAMSS activity agent started: machine=%s employee=%s interval=%ss sync=%ss spool=%s",
                MACHINE_ID, EMPLOYEE_ID, LOG_INTERVAL, SYNC_INTERVAL, SPOOL_DIR)
    if SCREENSHOTS_ENABLED:
        logger.warning("WAMSS_SCREENSHOTS=1 is set; screenshot capture is not implemented and stays off.")

    try:
        started = time.time()
        last_sync = 0.0
        segment_start = datetime.now(timezone.utc)
        while True:
            time.sleep(LOG_INTERVAL)
            record = collect_once(counter, tracker, hr_stream=hr_stream)
            if record is None:
                segment_start = datetime.now(timezone.utc)
                continue  # excluded window: nothing logged or spooled
            record["start_time"] = segment_start.isoformat()
            segment_start = datetime.now(timezone.utc)
            spool.append(record)

            tracker.maybe_trigger_break(spool)

            now = time.time()
            if now - last_sync >= SYNC_INTERVAL:
                if syncer.sync_once():
                    last_sync = now
                else:
                    # On failure the syncer already applied exponential backoff;
                    # wait out the backoff before the next attempt.
                    last_sync = now - SYNC_INTERVAL + syncer.next_delay()

            if stop_after is not None and now - started >= stop_after:
                logger.info("dry run finished; %d records pending in spool", spool.pending_count())
                break
    finally:
        if hr_stream is not None:
            try:
                hr_stream.stop()
            except Exception:
                pass


if __name__ == "__main__":
    dry_run = os.environ.get("WAMSS_DRY_RUN_SECONDS")
    run(stop_after=float(dry_run) if dry_run else None)
