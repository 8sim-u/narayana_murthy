"""
WAMSS Computer Work Monitor - zero-dependency Windows version.
Shows whether work is being completed on the computer side.
Uses only Python stdlib (ctypes) - no pip install needed.

Usage: python work_monitor.py [--server http://localhost:8000]
"""
import ctypes
import json
import sys
import time
import urllib.request
from ctypes import wintypes
from datetime import datetime

# Windows API setup
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

def get_idle_seconds():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    user32.GetLastInputInfo(ctypes.byref(lii))
    millis = kernel32.GetTickCount() - lii.dwTime
    return millis / 1000.0

def get_active_window():
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or "(unknown)"

def get_active_process():
    """Get the process name of the foreground window."""
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(h)
        path = buf.value
        return path.split("\\")[-1] if path else "(unknown)"
    except Exception:
        return "(unknown)"

def main():
    server = "http://localhost:8000"
    for i, a in enumerate(sys.argv):
        if a == "--server" and i + 1 < len(sys.argv):
            server = sys.argv[i + 1].rstrip("/")

    print("=" * 60, flush=True)
    print("WAMSS Computer Work Monitor", flush=True)
    print("Watching: keyboard/mouse activity + active application", flush=True)
    print("Server:", server, flush=True)
    print("=" * 60, flush=True)

    idle_threshold = 60  # seconds
    interval = 10  # report every 10s

    while True:
        idle = get_idle_seconds()
        app = get_active_process()
        title = get_active_window()
        is_idle = idle > idle_threshold

        if is_idle:
            status, detail = "IDLE", f"no input for {int(idle)}s"
        else:
            status, detail = "WORKING", f"active in {app}"

        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {status}: {detail}", flush=True)
        if title and title != "(unknown)":
            print(f"         Window: {title[:60]}", flush=True)

        # Try to POST to backend (graceful if endpoint missing)
        try:
            payload = json.dumps({
                "machine_id": "laptop-01",
                "active_app": app,
                "window_title": title[:200],
                "is_idle": is_idle,
                "idle_seconds": round(idle, 1),
            }).encode()
            req = urllib.request.Request(
                f"{server}/activity", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass  # backend may not have /activity yet; local display still works

        time.sleep(interval)

if __name__ == "__main__":
    main()
