"""Consent-gated Windows tray monitor packaged by ``build_exe.ps1``.

Configuration is loaded from ``config.env`` next to the executable (or from
``WAMSS_MONITOR_CONFIG``). The server URL, idle threshold, and sample interval
must be configured; the monitor deliberately does not ship with a workplace
server or timing policy hard-coded into the executable.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

UNKNOWN = "(unknown)"
SECRET_CONFIG_KEYS = {"WAMSS_EMPLOYEE_TOKEN", "WAMSS_API_KEY"}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _config_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config() -> None:
    """Load simple KEY=VALUE config without overriding process environment."""
    config_path = Path(os.environ.get("WAMSS_MONITOR_CONFIG", _config_dir() / "config.env"))
    try:
        lines = config_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in SECRET_CONFIG_KEYS:
            continue
        os.environ.setdefault(key, value.strip().strip("\"'"))


def _ensure_protected_employee_token() -> bool:
    """Load the user's DPAPI token or collect it through a masked GUI prompt."""
    import secret_store

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        _show_error("Windows could not resolve this account's local application-data directory.")
        return False
    secret_dir = Path(local_app_data) / "WAMSS" / "WorkMonitor" / "secrets"
    try:
        secret_store.restrict_store_to_current_user(secret_dir)
        if secret_store.load_into_environment(secret_dir, required=False):
            return True
    except secret_store.SecretStoreError as exc:
        _show_error(str(exc))
        return False

    try:
        import tkinter
        from tkinter import messagebox, simpledialog
    except Exception:
        _show_error("A Windows dialog is required to enter the employee token securely.")
        return False

    employee_id = os.environ.get("WAMSS_EMPLOYEE_ID", "configured employee")
    token = simpledialog.askstring(
        "WAMSS Work Monitor - employee credential",
        f"Enter the personal employee token assigned to {employee_id}.\n"
        "This is not a manager credential; do not share it. Input is hidden.",
        show="*",
    )
    if token is None:
        return False
    confirmation = simpledialog.askstring(
        "Confirm employee credential",
        "Re-enter the employee token. Input is hidden.",
        show="*",
    )
    if confirmation is None:
        return False
    if not token or not token.strip() or token != confirmation:
        messagebox.showerror("WAMSS Work Monitor", "The token is empty or the confirmations do not match.")
        return False

    try:
        secret_store.write_protected_values(
            secret_dir, {"WAMSS_EMPLOYEE_TOKEN": token}
        )
        secret_store.restrict_store_to_current_user(secret_dir)
        secret_store.load_into_environment(secret_dir, required=True)
        return True
    except secret_store.SecretStoreError as exc:
        _show_error(str(exc))
        return False


def get_idle_seconds() -> float:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    last_input = LASTINPUTINFO()
    last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(last_input)):
        return 0.0
    elapsed_ms = (kernel32.GetTickCount() - last_input.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def get_active_window() -> tuple[str, str]:
    """Return the foreground process name and title; never inspect key content."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return UNKNOWN, ""
    title_len = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(title_len + 1)
    user32.GetWindowTextW(hwnd, title_buf, title_len + 1)

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not process_handle:
        return UNKNOWN, title_buf.value or ""
    try:
        process_buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(process_buf))
        if not kernel32.QueryFullProcessImageNameW(process_handle, 0, process_buf, ctypes.byref(size)):
            return UNKNOWN, title_buf.value or ""
        app_name = process_buf.value.rsplit("\\", 1)[-1] or UNKNOWN
        return app_name, title_buf.value or ""
    finally:
        kernel32.CloseHandle(process_handle)


def _is_excluded(app_name: str, title: str) -> bool:
    excluded_apps = {
        item.strip().casefold()
        for item in os.environ.get("WAMSS_EXCLUDED_APPS", "").split(",")
        if item.strip()
    }
    if app_name.casefold() in excluded_apps:
        return True
    pattern = os.environ.get("WAMSS_EXCLUDED_TITLES", "").strip()
    if not pattern:
        return False
    try:
        return re.search(pattern, title, re.IGNORECASE) is not None
    except re.error:
        return False


def _config_value(args_value: Optional[str], env_name: str) -> Optional[str]:
    return args_value if args_value is not None else os.environ.get(env_name)


def _number(value: Optional[str], name: str) -> float:
    try:
        result = float(value) if value is not None else 0
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if result <= 0:
        raise ValueError(f"{name} must be configured as a positive number")
    return result


def _activity_record(
    app_name: str,
    title: str,
    idle_seconds: float,
    idle_threshold: float,
    machine_id: str,
    employee_id: str,
    office_id: str,
    segment_start: datetime,
    segment_end: datetime,
) -> Dict:
    return {
        "record_id": str(uuid.uuid4()),
        "machine_id": machine_id,
        "employee_id": employee_id,
        "office_id": office_id,
        "event_type": "activity",
        "app_name": app_name[:256],
        "window_title": title[:512],
        "process_name": app_name[:256],
        "start_time": segment_start.isoformat(),
        "end_time": segment_end.isoformat(),
        "keystrokes": 0,
        "mouse_clicks": 0,
        "is_idle": idle_seconds >= idle_threshold,
    }


def _post_record(server: str, record: Dict) -> None:
    parsed = urllib.parse.urlsplit(server)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("WAMSS_AGENT_SERVER_URL must use HTTPS (HTTP is allowed only for loopback)")
    payload = json.dumps({"records": [record]}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    employee_token = os.environ.get("WAMSS_EMPLOYEE_TOKEN", "")
    if not employee_token:
        raise RuntimeError("WAMSS_EMPLOYEE_TOKEN is required for employee activity uploads")
    headers["X-Employee-Token"] = employee_token
    api_key = os.environ.get("WAMSS_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        f"{server.rstrip('/')}/activity",
        data=payload,
        headers=headers,
        method="POST",
    )
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=5) as response:
        response.read()


def _set_status(icon, status: Dict[str, str], text: str) -> None:
    """Keep the tray title and its live menu status in sync."""
    status["text"] = text
    icon.title = f"WAMSS Work Monitor - {text}"
    icon.update_menu()


def _show_error(message: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("WAMSS Work Monitor", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _run_monitor(
    stop_event: threading.Event,
    icon,
    status: Dict[str, str],
    server: str,
    idle_threshold: float,
    interval: float,
) -> None:
    from consent import configured_employee_id, is_consented

    machine_id = os.environ.get("WAMSS_MACHINE_ID") or socket.gethostname()
    employee_id = configured_employee_id()
    office_id = os.environ.get("WAMSS_OFFICE_ID", "hq")
    segment_start = datetime.now(timezone.utc)

    while not stop_event.is_set():
        if not is_consented(employee_id=employee_id):
            _set_status(icon, status, "Consent withdrawn - monitoring stopped")
            stop_event.set()
            break
        try:
            idle_seconds = get_idle_seconds()
            app_name, title = get_active_window()
            if _is_excluded(app_name, title):
                _set_status(icon, status, "Privacy exclusion active - not recording")
                segment_start = datetime.now(timezone.utc)
            else:
                record = _activity_record(
                    app_name, title, idle_seconds, idle_threshold,
                    machine_id, employee_id, office_id,
                    segment_start, datetime.now(timezone.utc),
                )
                # Report the observed input-idle state before networking so the
                # tray remains accurate when the backend is slow or unreachable.
                observed = "IDLE" if record["is_idle"] else "WORKING"
                _set_status(icon, status, observed)
                if not is_consented(employee_id=employee_id):
                    stop_event.set()
                    break
                _post_record(server, record)
                segment_start = datetime.now(timezone.utc)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            observed = status.get("text", "")
            if observed in {"WORKING", "IDLE"}:
                _set_status(icon, status, f"{observed} - backend unavailable")
            else:
                _set_status(icon, status, "Backend unavailable")
            print(f"activity post failed: {type(exc).__name__}", file=sys.stderr)
        stop_event.wait(interval)


def main() -> int:
    if platform.system() != "Windows":
        _show_error("This packaged monitor runs on Windows only.")
        return 2

    load_config()
    parser = argparse.ArgumentParser(description="Consent-gated WAMSS Windows activity tray monitor")
    parser.add_argument("--server", help="Backend base URL; overrides WAMSS_AGENT_SERVER_URL")
    parser.add_argument("--idle-after", help="Idle threshold in seconds; overrides WAMSS_IDLE_AFTER_SEC")
    parser.add_argument("--interval", help="Sampling interval in seconds; overrides WAMSS_LOG_INTERVAL")
    args = parser.parse_args()

    server = _config_value(args.server, "WAMSS_AGENT_SERVER_URL")
    idle_value = _config_value(args.idle_after, "WAMSS_IDLE_AFTER_SEC")
    interval_value = _config_value(args.interval, "WAMSS_LOG_INTERVAL")
    try:
        if not server or not server.strip():
            raise ValueError("Set WAMSS_AGENT_SERVER_URL in config.env or pass --server.")
        idle_threshold = _number(idle_value, "WAMSS_IDLE_AFTER_SEC / --idle-after")
        interval = _number(interval_value, "WAMSS_LOG_INTERVAL / --interval")
    except ValueError as exc:
        _show_error(str(exc))
        return 2

    # Consent runs before any credential is collected: the monitor must not
    # ask for or load an employee token until the employee has consented.
    from consent import configured_employee_id, ensure_consent

    if not ensure_consent(employee_id=configured_employee_id()):
        return 1

    if not _ensure_protected_employee_token():
        return 2

    from consent import prompt_withdraw_consent

    try:
        from PIL import Image, ImageDraw
        import pystray
    except ImportError as exc:
        _show_error(f"Tray dependencies are missing: {exc}. Rebuild with agent/build_exe.ps1.")
        return 2

    stop_event = threading.Event()
    status = {"text": "Starting"}
    image = Image.new("RGB", (64, 64), (22, 88, 125))
    draw = ImageDraw.Draw(image)
    draw.ellipse((12, 12, 52, 52), fill=(228, 245, 250))
    draw.ellipse((23, 23, 41, 41), fill=(22, 88, 125))

    def exit_monitor(icon, _item) -> None:
        stop_event.set()
        icon.stop()

    def withdraw_monitor(icon, _item) -> None:
        if prompt_withdraw_consent():
            stop_event.set()
            _set_status(icon, status, "Consent withdrawn - monitoring stopped")
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: f"Status: {status['text']}", None, enabled=False),
        pystray.MenuItem("Withdraw consent and stop", withdraw_monitor),
        pystray.MenuItem("Exit", exit_monitor),
    )
    icon = pystray.Icon("WAMSS Work Monitor", image, "WAMSS Work Monitor - starting", menu)
    worker = threading.Thread(
        target=_run_monitor,
        args=(stop_event, icon, status, server, idle_threshold, interval),
        name="wamss-monitor",
        daemon=True,
    )
    worker.start()
    try:
        icon.run()
    finally:
        stop_event.set()
        worker.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
