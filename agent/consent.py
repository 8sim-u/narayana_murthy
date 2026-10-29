"""
WAMSS first-run consent gate.

Logging must NOT start until the employee has seen the consent notice and
accepted. Acceptance is recorded in a local consent.json marker so the prompt
only appears on the very first run (or when the marker is deleted).

Platform behavior:
  - Windows: graphical dialog (tkinter messagebox, with a ctypes MessageBox
    fallback). Declining closes the agent without logging.
  - Linux / console: the notice is printed and the employee must type YES.
  - Headless / non-interactive: if no prompt can be shown, consent is treated
    as NOT given and the agent must not log (returns False).

Integration (for the coordinator; do NOT start the agent's collection loop
until ensure_consent() returns True):

    from consent import ensure_consent
    if not ensure_consent():
        # stop: do not collect, do not spool, do not sync
        raise SystemExit("WAMSS agent stopped: consent not given.")
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("wamss.agent.consent")

CONSENT_ENV_VAR = "WAMSS_CONSENT_FILE"
DEFAULT_MARKER_NAME = "consent.json"

# Version of the notice text. Bump this when the notice changes so existing
# markers from an older wording are re-prompted.
CONSENT_VERSION = "1"

CONSENT_TITLE = "WAMSS activity monitoring: your consent"

CONSENT_TEXT = """\
WAMSS (Workplace Activity Monitoring and Surveillance System) runs on this
workstation and records your work activity. Please read carefully.

WHAT IS LOGGED (counts and metadata only):
- The name of the application you are using (e.g. "notepad.exe").
- The title of the active window (e.g. the document name in the title bar).
- Input COUNTS only: how many keystrokes and mouse clicks occurred in each
  time interval. The CONTENT of what you type is never captured.
- Idle time: periods with no keyboard or mouse input.
- Machine and employee identifiers, so the logs can be attributed.

WHAT IS NEVER LOGGED:
- The text you type (passwords, messages, documents).
- Screenshots or camera images (disabled by default).
- Anything typed or clicked inside excluded applications or windows
  configured by your administrator (see the privacy policy).

WHAT HAPPENS TO THE DATA:
- Logs are sent to the WAMSS backend server and are visible to your
  administrators and managers on the dashboard.
- They are used for work-pattern analysis, break reminders, and
  policy compliance (working hours, lunch break, rest limits).
- Logs older than the configured retention period (default 90 days) are
  automatically deleted by the server.

Click Yes (or type YES) to consent and start monitoring.
Click No to decline: the agent will stop and nothing will be logged.
You can withdraw consent at any time by asking your administrator to stop
the agent on your workstation.
"""


def marker_path(override: Optional[str] = None) -> Path:
    """Location of the consent marker file."""
    env_path = override or os.environ.get(CONSENT_ENV_VAR)
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent / DEFAULT_MARKER_NAME


def is_consented(path: Optional[Path] = None) -> bool:
    """True if a valid consent marker exists (and matches the current text version)."""
    marker = path or marker_path()
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("consented") is True
        and str(data.get("version")) == CONSENT_VERSION
    )


def record_consent(path: Optional[Path] = None) -> Path:
    """Write the consent marker. Returns the marker path."""
    marker = path or marker_path()
    payload = {
        "consented": True,
        "version": CONSENT_VERSION,
        "consented_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
    }
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("consent recorded at %s", marker)
    return marker


def _ask_windows_gui() -> Optional[bool]:
    """Graphical Yes/No dialog on Windows. Returns None if no GUI available."""
    # Prefer tkinter for a proper Yes/No/Cancel style prompt.
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        answer = messagebox.askyesno(CONSENT_TITLE, CONSENT_TEXT, icon="info")
        root.destroy()
        return bool(answer)
    except Exception as exc:  # pragma: no cover - no display in CI
        logger.debug("tkinter consent dialog unavailable: %s", exc)

    # Fallback: native MessageBox via ctypes (IDYES=6).
    try:
        import ctypes

        result = ctypes.windll.user32.MessageBoxW(
            0, CONSENT_TEXT, CONSENT_TITLE, 0x00000004 | 0x00000040
        )
        return result == 6
    except Exception as exc:  # pragma: no cover
        logger.debug("ctypes MessageBox consent dialog unavailable: %s", exc)
    return None


def _ask_console() -> Optional[bool]:
    """Console prompt. Returns None when stdin is not interactive."""
    if not sys.stdin.isatty():
        return None
    print("=" * 72)
    print(CONSENT_TITLE)
    print("=" * 72)
    print(CONSENT_TEXT)
    try:
        answer = input('Type YES to consent and start monitoring, anything else to decline: ')
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().upper() == "YES"


def prompt_for_consent() -> Optional[bool]:
    """
    Show the consent notice on the appropriate surface.
    Returns True (accepted), False (declined), or None (no prompt possible).
    """
    if platform.system() == "Windows":
        result = _ask_windows_gui()
        if result is not None:
            return result
        # Fall back to console on Windows too if the GUI failed.
    return _ask_console()


def ensure_consent() -> bool:
    """
    Gate the agent: True only if consent is recorded. Shows the notice on
    first run and records the marker on acceptance. Never raises.
    """
    try:
        if is_consented():
            return True
        answer = prompt_for_consent()
        if answer is True:
            record_consent()
            return True
        logger.warning("consent not given (answer=%r); agent must not start logging", answer)
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("consent check failed: %s; refusing to log", exc)
        return False
