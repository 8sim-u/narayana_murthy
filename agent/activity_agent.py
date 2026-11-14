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
import hashlib
import logging
import os
import platform
import re
import socket
import threading
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Optional, Tuple

import requests

from strain import StrainConfig, compute_strain_score
try:
    # Round 4: guarded so the agent runs fine without the ML pipeline.
    from strain import AnonymizedTimingBuffer, enrich_strain_score
    _STRAIN_PIPELINE_IMPORT_OK = True
except ImportError:  # pragma: no cover - defensive
    AnonymizedTimingBuffer = None  # type: ignore
    enrich_strain_score = None  # type: ignore
    _STRAIN_PIPELINE_IMPORT_OK = False

try:
    from notifier import notify_break
except ImportError:  # pragma: no cover - defensive
    def notify_break(title, message, notifier=None):  # type: ignore
        print(f"[WAMSS] {title}: {message}")
        return "log"

try:
    from consent import (configured_employee_id, current_os_user, ensure_consent, is_consented,
                         ensure_wellness_consent, is_wellness_consented)
except ImportError:  # pragma: no cover - defensive
    def configured_employee_id(os_user: Optional[str] = None) -> str:  # type: ignore
        return os.environ.get("WAMSS_EMPLOYEE_ID", os.environ.get("USER", "unknown"))

    def current_os_user() -> Optional[str]:  # type: ignore
        return None

    def ensure_consent(*, employee_id=None, os_user=None) -> bool:  # type: ignore
        logger.error("consent module unavailable; refusing to collect")
        return False

    def is_consented(path=None, *, employee_id=None, os_user=None) -> bool:  # type: ignore
        logger.error("consent module unavailable; refusing to sync")
        return False

    def ensure_wellness_consent() -> bool:  # type: ignore
        logger.error("wellness consent module unavailable; wearable stays off")
        return False

    def is_wellness_consented() -> bool:  # type: ignore
        return False

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

# Optional deployment key for separately trusted clients, plus this employee's
# personal token. Windows provisioning clears the global key; this token binds
# activity records to EMPLOYEE_ID and is never a manager credential.
API_KEY = os.environ.get("WAMSS_API_KEY", "")
EMPLOYEE_TOKEN = os.environ.get("WAMSS_EMPLOYEE_TOKEN", "")


def _auth_headers(employee_token: Optional[str] = EMPLOYEE_TOKEN):
    """Return configured deployment and employee headers for agent POSTs."""
    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    if employee_token:
        headers["X-Employee-Token"] = employee_token
    return headers or None
MACHINE_ID = os.environ.get("WAMSS_MACHINE_ID", socket.gethostname())
EMPLOYEE_ID = configured_employee_id()

# ------------------------------------------------------------------
# Agent 5 (multi-office, Round 3): office identity.
# WAMSS_OFFICE_ID names the office/site this workstation belongs to
# (e.g. "hq", "branch-north"). It is sent as office_id on every
# activity record so the backend can filter dashboards by office.
# A parallel Round 3 agent adds API-key headers in this file; this
# block is self-contained and does not touch that work.
# ------------------------------------------------------------------
OFFICE_ID = os.environ.get("WAMSS_OFFICE_ID", "hq")
LOG_INTERVAL = _getenv_float("WAMSS_LOG_INTERVAL", 60.0)          # seconds per activity segment
SYNC_INTERVAL = _getenv_float("WAMSS_SYNC_INTERVAL", 300.0)       # seconds between upload attempts
SYNC_ENABLED = os.environ.get("WAMSS_SYNC_ENABLED", "1") != "0"
IDLE_AFTER_SEC = _getenv_float("WAMSS_IDLE_AFTER_SEC", 180.0)     # no input -> idle segment


def spool_dir_for_identity(
    os_user: Optional[str] = None,
    employee_id: Optional[str] = None,
) -> Optional[str]:
    """Return a stable, identity-isolated spool path, or None if identity is unknown."""
    user = current_os_user() if os_user is None else os_user
    employee = configured_employee_id(user) if employee_id is None else employee_id
    if not user or not employee:
        return None
    configured_root = os.environ.get(
        "WAMSS_SPOOL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "spool")
    )
    root = os.path.expanduser(configured_root)
    if not os.path.isabs(root):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), root)
    scope = hashlib.sha256(f"{employee}\0{user}".encode("utf-8")).hexdigest()[:24]
    return os.path.join(root, scope)


SPOOL_DIR = spool_dir_for_identity(employee_id=EMPLOYEE_ID) or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "spool", "unbound"
)
MAX_BATCH = _getenv_int("WAMSS_MAX_BATCH", 500)                   # records per POST
MAX_BACKOFF_SEC = _getenv_float("WAMSS_MAX_BACKOFF_SEC", 3600.0)
SCREENSHOTS_ENABLED = os.environ.get("WAMSS_SCREENSHOTS", "0") == "1"  # reserved; must stay 0 unless explicitly enabled
WELLNESS_BREAK_PROMPTS = os.environ.get("WAMSS_WELLNESS_BREAK_PROMPTS", "0") == "1"

# Privacy exclusion list: personal apps/windows that are never logged,
# not even to the local spool. Case-insensitive process/app names.
EXCLUDED_APPS = {a.strip().lower() for a in os.environ.get("WAMSS_EXCLUDED_APPS", "").split(",") if a.strip()}
EXCLUDED_TITLES_RE = os.environ.get("WAMSS_EXCLUDED_TITLES", "").strip()


# Round 4: opt-in ML/fatigue/fusion strain pipeline. Default 0 keeps the
# heuristic-only behavior exactly as before.
STRAIN_PIPELINE_ON = (
    os.environ.get("WAMSS_STRAIN_PIPELINE", "0") == "1"
    and _STRAIN_PIPELINE_IMPORT_OK
)

# In-memory anonymized timing buffers for the ML feature extractors.
# Gaps between input events only: no key identities, no key content, no
# coordinates. Never persisted, never spooled. Drained each cycle.
KEY_TIMINGS = AnonymizedTimingBuffer() if AnonymizedTimingBuffer else None
MOUSE_TIMINGS = AnonymizedTimingBuffer() if AnonymizedTimingBuffer else None

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
        # Round 4: anonymized press-gap timing for ML features.
        if STRAIN_PIPELINE_ON and KEY_TIMINGS is not None:
            KEY_TIMINGS.record_event()

    def _on_click(self, _x, _y, _button, _pressed) -> None:
        if not _pressed:
            return
        with self._lock:
            self.mouse_clicks += 1
            self.last_input_ts = time.time()
        # Round 4: anonymized click-gap timing for ML features.
        # _x/_y are deliberately ignored: no coordinates are captured.
        if STRAIN_PIPELINE_ON and MOUSE_TIMINGS is not None:
            MOUSE_TIMINGS.record_event()

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


@contextmanager
def _cross_process_lock(lock_path: str):
    """Serialize spool operations across the collector and Windows sync service."""
    lock_parent = os.path.dirname(lock_path) or "."
    if not os.path.isdir(lock_parent):
        os.makedirs(lock_parent, exist_ok=True)
    lock_file = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


class Spool:
    """Append-only JSONL spool; records stay local until a sync succeeds."""

    def __init__(self, spool_dir: str = SPOOL_DIR) -> None:
        self.dir = spool_dir
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir, exist_ok=True)
        self.pending_path = os.path.join(self.dir, "pending.jsonl")
        self.sent_path = os.path.join(self.dir, "sent.jsonl")
        self.lock_path = os.path.join(self.dir, ".spool.lock")
        self._lock = threading.Lock()

    def append(self, record: Dict) -> None:
        line = json.dumps(record, default=str)
        with self._lock, _cross_process_lock(self.lock_path), open(self.pending_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read_batch(self, max_records: int = MAX_BATCH) -> list:
        with self._lock, _cross_process_lock(self.lock_path):
            if not os.path.exists(self.pending_path):
                return []
            with open(self.pending_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        return [json.loads(l) for l in lines[:max_records] if l.strip()]

    def mark_sent(self, records_or_count) -> None:
        """Archive exactly the uploaded records, preserving unrelated pending rows.

        Integer input remains supported for older callers. Syncer passes the
        posted records so concurrent processes cannot acknowledge a different
        set of lines after another process has already removed its batch.
        """
        with self._lock, _cross_process_lock(self.lock_path):
            if not os.path.exists(self.pending_path):
                return
            with open(self.pending_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if isinstance(records_or_count, int):
                sent, remaining = lines[:records_or_count], lines[records_or_count:]
            else:
                pending_ids = Counter(
                    str(record["record_id"])
                    for record in records_or_count
                    if isinstance(record, dict) and record.get("record_id")
                )
                pending_payloads = Counter(
                    json.dumps(record, sort_keys=True, default=str)
                    for record in records_or_count
                    if isinstance(record, dict) and not record.get("record_id")
                )
                sent, remaining = [], []
                for line in lines:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        remaining.append(line)
                        continue
                    record_id = str(record.get("record_id")) if isinstance(record, dict) and record.get("record_id") else None
                    payload_key = json.dumps(record, sort_keys=True, default=str)
                    if record_id and pending_ids[record_id] > 0:
                        pending_ids[record_id] -= 1
                        sent.append(line)
                    elif not record_id and pending_payloads[payload_key] > 0:
                        pending_payloads[payload_key] -= 1
                        sent.append(line)
                    else:
                        remaining.append(line)
            with open(self.sent_path, "a", encoding="utf-8") as f:
                f.writelines(sent)
                f.flush()
                os.fsync(f.fileno())
            tmp = self.pending_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(remaining)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.pending_path)
            if os.name != "nt":
                directory_fd = os.open(self.dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)

    def pending_count(self) -> int:
        try:
            with self._lock, _cross_process_lock(self.lock_path), open(self.pending_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except FileNotFoundError:
            return 0


# ---------------------------------------------------------------- sync with exponential backoff

class WellnessUploader:
    """Direct, best-effort live-sample upload; never writes health data to the spool."""

    def __init__(self, server_url: str = SERVER_URL, employee_token: str = EMPLOYEE_TOKEN) -> None:
        self.server_url = server_url.rstrip("/")
        self.employee_token = employee_token
        self.session = requests.Session()
        self.last_sent_ts = 0.0
        self._warned_rejected = False
        self._disabled = False

    @property
    def disabled(self) -> bool:
        return self._disabled

    def upload_live(self, sample: Dict) -> bool:
        if self._disabled or not is_wellness_consented() or not self.employee_token:
            return False
        try:
            ts = float(sample.get("ts", 0))
            if ts <= self.last_sent_ts or time.time() - ts > 90:
                return False
            headers = dict(_auth_headers() or {})
            headers["X-Employee-Token"] = self.employee_token
            payload = {
                "heart_rate_bpm": int(sample["bpm"]),
                "rmssd_ms": sample.get("rmssd_ms"),
                "observed_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            }
            response = self.session.post(
                f"{self.server_url}/wellness/live", json=payload, headers=headers, timeout=4,
            )
            if response.status_code in (401, 403, 503):
                if not self._warned_rejected:
                    logger.warning("wearable sample was not accepted; check personal token and separate dashboard consent")
                    self._warned_rejected = True
                self._disabled = True
                return False
            response.raise_for_status()
            self.last_sent_ts = ts
            return True
        except Exception as exc:
            logger.debug("live wellness upload skipped: %s", type(exc).__name__)
            return False


class WellnessBreakPromptPoller:
    """Poll the employee-only combined prompt and log a suggestion, never completion."""

    def __init__(self, server_url: str = SERVER_URL,
                 employee_token: str = EMPLOYEE_TOKEN,
                 notifier: Optional[Callable[[str, str], None]] = None) -> None:
        self.server_url = server_url.rstrip("/")
        self.employee_token = employee_token
        self.notifier = notifier
        self.session = requests.Session()
        self.suggested_today = False

    def poll(self) -> bool:
        if (not WELLNESS_BREAK_PROMPTS or not self.employee_token
                or not is_wellness_consented()):
            return False
        headers = dict(_auth_headers() or {})
        headers["X-Employee-Token"] = self.employee_token
        try:
            response = self.session.get(f"{self.server_url}/wellness/me", headers=headers, timeout=5)
            response.raise_for_status()
            wellness = (response.json().get("wellness") or {})
            breaks = wellness.get("breaks_today") or {}
            if breaks.get("suggested"):
                self.suggested_today = True
            prompt = wellness.get("break_prompt") or {}
            if (self.suggested_today or not wellness.get("consented")
                    or not prompt.get("show_prompt")):
                return False
            self.suggested_today = True
            notify_break(
                "Time for a break",
                "Consider stepping away for a few minutes. This is a private wellness nudge, not a medical assessment.",
                notifier=self.notifier,
            )
            # This minimal employee-scoped record means the prompt is logged
            # even if no control is clicked. It is not a completed break.
            logged = self.session.post(
                f"{self.server_url}/wellness/breaks",
                json={"event_type": "suggested"}, headers=headers, timeout=5,
            )
            logged.raise_for_status()
            return True
        except Exception as exc:
            logger.debug("private wellness break prompt skipped: %s", type(exc).__name__)
            return False


class Syncer:
    def __init__(
        self,
        spool: Spool,
        server_url: str = SERVER_URL,
        employee_token: Optional[str] = EMPLOYEE_TOKEN,
        os_user: Optional[str] = None,
    ) -> None:
        self.spool = spool
        self.server_url = server_url
        self.employee_token = employee_token
        self.os_user = os_user
        self.backoff = 30.0
        self.session = requests.Session()

    def sync_once(self) -> bool:
        if not is_consented(employee_id=EMPLOYEE_ID, os_user=self.os_user):
            logger.info("activity sync paused: workstation consent is absent")
            return False
        parsed = urllib.parse.urlsplit(self.server_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            logger.error("activity sync blocked: server URL must use HTTPS (HTTP is allowed only for loopback)")
            return False
        batch = self.spool.read_batch()
        if not batch:
            self.backoff = 30.0
            return True
        if not self.employee_token:
            logger.error(
                "activity sync blocked: WAMSS_EMPLOYEE_TOKEN is missing; %d records remain spooled",
                self.spool.pending_count(),
            )
            return False
        try:
            resp = self.session.post(
                f"{self.server_url}/activity",
                json={"records": batch},
                timeout=15,
                headers=_auth_headers(self.employee_token),
                allow_redirects=False,
            )
            resp.raise_for_status()
            self.spool.mark_sent(batch)
            self.backoff = 30.0
            logger.info("synced %d activity records", len(batch))
            return True
        except Exception as exc:
            logger.warning("sync failed (%s); backing off %.0fs, %d records spooled",
                           type(exc).__name__, self.backoff, self.spool.pending_count())
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
            "office_id": OFFICE_ID,  # Agent 5 (multi-office, Round 3)
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

def collect_once(counter: InputCounter, tracker: StrainTracker, hr_stream=None,
                 spool: Optional["Spool"] = None) -> Optional[Dict]:
    """spool is optional and only used by the round-4 pipeline to spool
    fatigue_suspected records. Existing callers are unaffected."""
    app_name, window_title, process_name = get_active_window()
    if _is_excluded(app_name, window_title, process_name):
        return None  # privacy exclusion: never logged, not even spooled
    keystrokes, mouse_clicks = counter.drain()
    idle_sec = counter.idle_seconds()
    is_idle = idle_sec >= IDLE_AFTER_SEC

    end = datetime.now(timezone.utc)
    start = end  # segment granularity is LOG_INTERVAL; start=end replaced by caller cadence

    # Round 4: a session ends when the continuous-active clock resets after
    # a long idle stretch; the fatigue tracker consumes it.
    was_active = tracker.active_start_ts is not None
    tracker.reset_active_clock_if_rested(idle_sec)
    session_ended = was_active and tracker.active_start_ts is None
    window_key = f"{process_name}\x00{window_title}"
    tracker.observe(keystrokes, mouse_clicks, window_key, is_idle)
    strain = tracker.signals()

    ml_score = None
    if STRAIN_PIPELINE_ON:
        # Round 4 pipeline: ml features -> session fatigue -> HR fusion.
        # The heuristic result rides along inside `enriched` so break
        # thresholds and the backend HIGH_STRAIN_DETECTED rule keep their
        # meaning. enrich_strain_score never raises: any ML/HR failure
        # falls back to the heuristic score.
        enriched = enrich_strain_score(
            strain,
            key_intervals=KEY_TIMINGS.drain() if KEY_TIMINGS else (),
            mouse_intervals=MOUSE_TIMINGS.drain() if MOUSE_TIMINGS else (),
            employee_id=EMPLOYEE_ID,
            session_ended=session_ended,
            # Health-derived values are intentionally excluded from the
            # activity strain score and all manager-visible alerts.
            hr_stream=None,
        )
        score = enriched.score
        ml_score = enriched.ml_score
        fatigue_event = enriched.fatigue_event
        if fatigue_event is not None and spool is not None:
            spool_fatigue_event(spool, fatigue_event, score)
    else:
        # Activity strain remains work-pattern-only; wearable values are
        # available exclusively in the employee's separate private view.
        score = strain.score
        fatigue_event = None

    return {
        "record_id": str(uuid.uuid4()),
        "machine_id": MACHINE_ID,
        "employee_id": EMPLOYEE_ID,
        "office_id": OFFICE_ID,  # Agent 5 (multi-office, Round 3)
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
        "ml_stress_score": ml_score,
    }


def spool_fatigue_event(spool: Spool, fatigue_event: dict,
                        strain_score: int) -> None:
    """Spool a fatigue_suspected record so it flows through the normal
    /activity ingest path, like break_suggested does. Round 4."""
    record = {
        "record_id": str(uuid.uuid4()),
        "machine_id": MACHINE_ID,
        "employee_id": fatigue_event.get("employee_id", EMPLOYEE_ID),
        "office_id": OFFICE_ID,
        "event_type": "fatigue_suspected",
        "app_name": "wamss-agent",
        "window_title": ("fatigue_suspected severity="
                         f"{fatigue_event.get('severity')} "
                         f"robust_z={fatigue_event.get('detail', {}).get('robust_z')}"),
        "process_name": "wamss-agent",
        "start_time": _utcnow_iso(),
        "end_time": _utcnow_iso(),
        "keystrokes": 0,
        "mouse_clicks": 0,
        "is_idle": False,
        "strain_score": strain_score,
    }
    spool.append(record)
    logger.info("fatigue_suspected spooled (severity=%s)",
                fatigue_event.get("severity"))


def run(stop_after: Optional[float] = None,
        notifier: Optional[Callable[[str, str], None]] = None) -> None:
    """
    Main loop. stop_after (seconds) is for dry runs; None means run forever.
    """
    if not ensure_consent(employee_id=EMPLOYEE_ID):
        raise SystemExit("WAMSS agent stopped: consent not given.")

    wellness_local_consent = False
    wellness_features_enabled = (os.environ.get("WAMSS_BLE_ENABLED", "0") == "1"
                                 or WELLNESS_BREAK_PROMPTS)
    if wellness_features_enabled and EMPLOYEE_TOKEN:
        wellness_local_consent = ensure_wellness_consent()

    active_user = current_os_user()
    spool_dir = spool_dir_for_identity(os_user=active_user, employee_id=EMPLOYEE_ID)
    if not spool_dir:
        raise SystemExit("WAMSS agent stopped: active employee/OS identity unavailable.")
    os.makedirs(spool_dir, exist_ok=True)
    spool = Spool(spool_dir)
    syncer = Syncer(spool) if SYNC_ENABLED else None
    counter = InputCounter()
    counter.start()
    tracker = StrainTracker(notifier=notifier)
    wellness_prompt_poller = (
        WellnessBreakPromptPoller(notifier=notifier)
        if WELLNESS_BREAK_PROMPTS and EMPLOYEE_TOKEN and wellness_local_consent else None
    )

    hr_stream = None
    wellness_uploader = None
    if (HeartRateStream is not None and os.environ.get("WAMSS_BLE_ENABLED", "0") == "1"
            and EMPLOYEE_TOKEN and wellness_local_consent):
        try:
            wellness_uploader = WellnessUploader()
            hr_stream = HeartRateStream().start()
        except Exception as exc:
            logger.warning("BLE wellness stream failed to start: %s", type(exc).__name__)
            hr_stream = None
            wellness_uploader = None
    elif os.environ.get("WAMSS_BLE_ENABLED", "0") == "1":
        logger.info("BLE stays off: employee token or separate local wellness consent is missing")

    logger.info("WAMSS activity agent started: machine=%s employee=%s interval=%ss sync=%ss spool=%s",
                MACHINE_ID, EMPLOYEE_ID, LOG_INTERVAL, SYNC_INTERVAL, spool_dir)
    if SCREENSHOTS_ENABLED:
        logger.warning("WAMSS_SCREENSHOTS=1 is set; screenshot capture is not implemented and stays off.")

    try:
        started = time.time()
        last_sync = 0.0
        segment_start = datetime.now(timezone.utc)
        while True:
            time.sleep(LOG_INTERVAL)
            # Consent can be withdrawn while this per-user process is running.
            # Stop before another sample is collected or spooled; the Windows
            # sync service independently checks the same marker before upload.
            if not is_consented(employee_id=EMPLOYEE_ID):
                logger.info("workstation consent withdrawn; stopping collection and pausing uploads")
                break
            if wellness_uploader is not None and hr_stream is not None:
                if not is_wellness_consented():
                    hr_stream.stop()
                    logger.info("local wellness consent marker removed; BLE stopped and sample memory cleared")
                    hr_stream = None
                    wellness_uploader = None
                else:
                    sample = hr_stream.latest_sample()
                    if sample is not None:
                        wellness_uploader.upload_live(sample)
                        if wellness_uploader.disabled:
                            hr_stream.stop()
                            logger.info("server declined wellness upload; BLE stopped and sample memory cleared")
                            hr_stream = None
                            wellness_uploader = None
            record = collect_once(counter, tracker, hr_stream=hr_stream, spool=spool)
            if record is None:
                segment_start = datetime.now(timezone.utc)
                continue  # excluded window: nothing logged or spooled
            record["start_time"] = segment_start.isoformat()
            segment_start = datetime.now(timezone.utc)
            spool.append(record)

            tracker.maybe_trigger_break(spool)
            if wellness_prompt_poller is not None:
                wellness_prompt_poller.poll()

            now = time.time()
            if syncer is not None and now - last_sync >= SYNC_INTERVAL:
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
