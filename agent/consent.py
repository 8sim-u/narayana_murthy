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
import getpass
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("wamss.agent.consent")

CONSENT_ENV_VAR = "WAMSS_CONSENT_FILE"
DEFAULT_MARKER_NAME = "consent.json"

# Version of the notice text. Bump this when the notice changes so existing
# markers from an older wording are re-prompted.
CONSENT_VERSION = "4"

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
- Daily app-use totals may be categorized as work, break, or neutral using an
  administrator-configured app map; apps not listed in that map are neutral.
- Daily attendance reporting is disabled by default. If it is separately
  enabled after notice, recognized enrolled camera sightings and non-idle
  workstation intervals may be combined into daily totals with the source shown.
  Camera identity use requires its own applicable notice and consent; this
  workstation consent does not enroll you for face recognition.

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

Consent is bound to your configured employee identity and this signed-in
workstation account. The workstation account name is used only to protect this
local choice and is not added to activity records.

Click Yes (or type YES) to consent and start monitoring.
Click No to decline: the agent will stop and nothing will be logged.
You can withdraw consent at any time: run `python consent.py --withdraw` in
the agent folder and confirm the prompt, or ask your administrator to stop the
agent. Withdrawal immediately stops local collection and pauses spool uploads.
"""


def _windows_process_session_id() -> Optional[int]:
    """Return this process's Windows session ID, or None if it cannot be read."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        session_id = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(
            kernel.GetCurrentProcessId(), ctypes.byref(session_id)
        ):
            return None
        return int(session_id.value)
    except Exception:
        return None


def _windows_profile_local_app_data(identity: str) -> Optional[str]:
    """Resolve an account's LocalAppData from its Windows profile registration."""
    try:
        import ctypes
        import winreg
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        sid_buf = ctypes.create_unicode_buffer(256)
        domain_buf = ctypes.create_unicode_buffer(256)
        sid_len = wintypes.DWORD(len(sid_buf))
        domain_len = wintypes.DWORD(len(domain_buf))
        use = wintypes.DWORD()
        if not advapi.LookupAccountNameW(
            None, identity, sid_buf, ctypes.byref(sid_len), domain_buf,
            ctypes.byref(domain_len), ctypes.byref(use),
        ):
            return None
        key = "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList\\" + sid_buf.value
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as reg:
            profile = winreg.QueryValueEx(reg, "ProfileImagePath")[0]
        profile = os.path.expandvars(profile)
        try:
            shell_key = sid_buf.value + "\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_USERS, shell_key) as shell:
                local_app_data = winreg.QueryValueEx(shell, "Local AppData")[0]
            local_app_data = re.sub(r"%USERPROFILE%", lambda _match: profile, local_app_data, flags=re.IGNORECASE)
            return os.path.expandvars(local_app_data)
        except OSError:
            return os.path.join(profile, "AppData", "Local")
    except Exception:
        return None


def _windows_session_identity() -> tuple[Optional[str], Optional[str]]:
    """Resolve this session's account/profile, or the active console for a service."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        session = _windows_process_session_id()
        if session is None:
            return None, None
        if session == 0:
            active = int(kernel.WTSGetActiveConsoleSessionId()) & 0xFFFFFFFF
            if active == 0xFFFFFFFF:
                return None, None
            session = active

        wts = ctypes.WinDLL("wtsapi32", use_last_error=True)

        def query(info: int) -> Optional[str]:
            buffer, size = ctypes.c_void_p(), wintypes.DWORD()
            if not wts.WTSQuerySessionInformationW(
                None, session, info, ctypes.byref(buffer), ctypes.byref(size)
            ) or not buffer.value:
                return None
            try:
                return ctypes.wstring_at(buffer.value)
            finally:
                wts.WTSFreeMemory(buffer)

        username, domain = query(5), query(7)
        if not username:
            return None, None
        identity = (domain + "\\" if domain else "") + username
        return identity, _windows_profile_local_app_data(identity)
    except Exception:
        return None, None


def _normalize_os_user(user: Optional[str]) -> Optional[str]:
    if not user or not user.strip():
        return None
    value = user.strip()
    return value.casefold() if platform.system() == "Windows" else value


def current_os_user() -> Optional[str]:
    """Resolve this process session's OS identity, case-folded on Windows."""
    try:
        if platform.system() == "Windows":
            session = _windows_process_session_id()
            identity, _ = _windows_session_identity()
            if identity:
                return _normalize_os_user(identity)
            if (session is None or session == 0
                    or os.environ.get("SESSIONNAME", "").upper() == "SERVICES"):
                return None
            name = os.environ.get("USERNAME") or getpass.getuser()
            domain = os.environ.get("USERDOMAIN", "")
            return _normalize_os_user((domain + "\\" if domain else "") + name)
        try:
            import pwd
            return _normalize_os_user(pwd.getpwuid(os.getuid()).pw_name)
        except (ImportError, KeyError, AttributeError):
            return _normalize_os_user(getpass.getuser())
    except Exception:
        return None


def configured_employee_id(os_user: Optional[str] = None) -> str:
    configured = os.environ.get("WAMSS_EMPLOYEE_ID", "").strip()
    if configured:
        return configured
    user = current_os_user() if os_user is None else _normalize_os_user(os_user)
    return user or ""


def marker_path(override: Optional[str] = None, *, os_user: Optional[str] = None) -> Path:
    """Location of the per-user consent marker (or exact shared override)."""
    env_path = override if override is not None else os.environ.get(CONSENT_ENV_VAR)
    if env_path:
        return Path(env_path)
    user = current_os_user() if os_user is None else _normalize_os_user(os_user)
    if not user:
        raise OSError("active OS identity is unavailable")
    if platform.system() == "Windows":
        session = _windows_process_session_id()
        current, profile_local = _windows_session_identity()
        target = user.strip().casefold()
        is_service = session == 0 or os.environ.get("SESSIONNAME", "").upper() == "SERVICES"
        if not is_service and current and current.strip().casefold() == target:
            root = os.environ.get("LOCALAPPDATA") or profile_local
        elif not is_service and not current:
            process_user = os.environ.get("USERNAME", "")
            process_domain = os.environ.get("USERDOMAIN", "")
            process_identity = (process_domain + "\\" if process_domain else "") + process_user
            if process_identity.strip().casefold() == target:
                root = os.environ.get("LOCALAPPDATA")
            else:
                root = _windows_profile_local_app_data(user)
        else:
            root = _windows_profile_local_app_data(user)
        if not root:
            raise OSError("interactive user's LocalAppData is unavailable")
        return Path(root) / "WAMSS" / DEFAULT_MARKER_NAME
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "wamss" / DEFAULT_MARKER_NAME


def is_consented(path: Optional[Path] = None, *, employee_id: Optional[str] = None, os_user: Optional[str] = None) -> bool:
    """True only for a current marker bound to both active identities."""
    try:
        active_user = current_os_user() if os_user is None else _normalize_os_user(os_user)
        employee = configured_employee_id(active_user) if employee_id is None else employee_id.strip()
        if not active_user or not employee:
            return False
        marker = path if path is not None else marker_path(os_user=active_user)
    except (OSError, ValueError):
        return False
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
        and data.get("employee_id") == employee
        and data.get("os_user") == active_user
    )


def record_consent(path: Optional[Path] = None, *, employee_id: Optional[str] = None, os_user: Optional[str] = None) -> Path:
    """Write identity-bound consent marker. Returns its path."""
    active_user = current_os_user() if os_user is None else _normalize_os_user(os_user)
    employee = configured_employee_id(active_user) if employee_id is None else employee_id.strip()
    if not active_user or not employee:
        raise OSError("employee and active OS identities are required")
    marker = path if path is not None else marker_path(os_user=active_user)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "consented": True,
        "version": CONSENT_VERSION,
        "consented_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "employee_id": employee,
        "os_user": active_user,
    }
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("consent recorded at %s", marker)
    return marker


def withdraw_consent(path: Optional[Path] = None, *, employee_id: Optional[str] = None, os_user: Optional[str] = None) -> bool:
    """Revoke only this identity's workstation marker; fail closed on errors."""
    active_user = current_os_user() if os_user is None else _normalize_os_user(os_user)
    employee = configured_employee_id(active_user) if employee_id is None else employee_id.strip()
    if not active_user or not employee:
        return False
    try:
        marker = path if path is not None else marker_path(os_user=active_user)
        if marker.exists():
            data = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("employee_id") != employee or data.get("os_user") != active_user:
                return False
        marker.unlink(missing_ok=True)
        logger.info("workstation consent withdrawn; collection and uploads are disabled")
        return True
    except (OSError, ValueError) as exc:
        logger.error("could not withdraw workstation consent: %s", exc)
        return False


def prompt_withdraw_consent() -> bool:
    """Ask the signed-in employee to confirm revocation; never runs silently."""
    title = "Withdraw WAMSS workstation activity consent"
    text = ("Withdraw workstation activity consent now? This stops new local "
            "collection and pauses queued activity uploads until fresh consent is given.")
    answer: Optional[bool] = None
    if platform.system() == "Windows":
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            answer = bool(messagebox.askyesno(title, text, icon="warning"))
            root.destroy()
        except Exception as exc:
            logger.debug("withdrawal dialog unavailable: %s", exc)
    if answer is None and sys.stdin.isatty():
        print(text)
        try:
            answer = input("Type WITHDRAW to stop monitoring: ").strip().upper() == "WITHDRAW"
        except (EOFError, KeyboardInterrupt):
            answer = False
    if answer is not True:
        print("Consent unchanged; no action taken.")
        return False
    withdrawn = withdraw_consent()
    if withdrawn:
        print("Consent withdrawn. Collection and queued uploads are paused.")
    return withdrawn


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


def ensure_consent(*, employee_id: Optional[str] = None, os_user: Optional[str] = None) -> bool:
    """
    Gate the agent: True only if consent is recorded. Shows the notice on
    first run and records the marker on acceptance. Never raises.
    """
    try:
        active_user = current_os_user() if os_user is None else _normalize_os_user(os_user)
        employee = configured_employee_id(active_user) if employee_id is None else employee_id.strip()
        if not active_user or not employee:
            return False
        if is_consented(employee_id=employee, os_user=active_user):
            return True
        answer = prompt_for_consent()
        if answer is True:
            record_consent(employee_id=employee, os_user=active_user)
            return True
        logger.warning("consent not given (answer=%r); agent must not start logging", answer)
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("consent check failed: %s; refusing to log", exc)
        return False


# Wellness collection has its own separate opt-in. It never follows implicitly
# from the workstation activity consent above.
WELLNESS_CONSENT_ENV_VAR = "WAMSS_WELLNESS_CONSENT_FILE"
WELLNESS_CONSENT_VERSION = "1"
WELLNESS_TITLE = "Optional wearable wellness data"
WELLNESS_TEXT = """\
Optional wellness collection is separate from workstation activity logging.

If enabled, a paired BLE wearable may send live heart rate and a short-window
HRV estimate to your personal dashboard. Live readings are kept in server
memory only and expire quickly. Daily HRV, resting heart rate, and sleep
summaries are stored only after you separately opt in on your personal
wellness dashboard. Raw readings, values, and health trends are not disclosed
to managers or other employees. Only if you separately opt in to manager
check-ins on that dashboard may a manager authorized for your roster see your
employee ID and one generic supportive prompt; no health values or reason are
shared, and the prompt must never be used as a penalty or employment decision.
These signals are not a diagnosis or a performance rating.

If your workstation administrator also enables the separate, default-off
WAMSS_WELLNESS_BREAK_PROMPTS setting, the agent may show you a private local
break suggestion based on the existing employee-only combined prompt. It logs
that a suggestion occurred, not that you took a break.

Choose Yes only if you want this optional collection. Choose No, or delete the
wellness-consent.json marker and disable BLE, to stop local collection. You
can withdraw dashboard consent there; stored wellness summaries are then
removed. No wearable is required for the activity agent.
"""


def wellness_marker_path(override: Optional[str] = None) -> Path:
    """Separate per-user marker for optional wearable health collection."""
    env_path = override or os.environ.get(WELLNESS_CONSENT_ENV_VAR)
    if env_path:
        return Path(env_path)
    if platform.system() == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            user = current_os_user()
            local_app_data = _windows_profile_local_app_data(user) if user else None
        if not local_app_data:
            raise OSError("interactive user's LocalAppData is unavailable")
        return Path(local_app_data) / "WAMSS" / "wellness-consent.json"
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "wamss" / "wellness-consent.json"


def is_wellness_consented(path: Optional[Path] = None) -> bool:
    try:
        active_user = current_os_user()
        employee = configured_employee_id(active_user) if active_user else ""
        if not active_user or not employee:
            return False
        marker = path or wellness_marker_path()
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("consented") is True
        and str(data.get("version")) == WELLNESS_CONSENT_VERSION
        and data.get("employee_id") == employee
        and data.get("os_user") == active_user
    )


def _record_wellness_consent(path: Optional[Path] = None) -> Path:
    active_user = current_os_user()
    employee = configured_employee_id(active_user) if active_user else ""
    if not active_user or not employee:
        raise OSError("employee and active OS identities are required")
    marker = path or wellness_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "consented": True,
        "version": WELLNESS_CONSENT_VERSION,
        "consented_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "employee_id": employee,
        "os_user": active_user,
    }, indent=2), encoding="utf-8")
    return marker


def ensure_wellness_consent() -> bool:
    """Ask separately before BLE starts; headless or declined means no wearable."""
    try:
        if is_wellness_consented():
            return True
        answer: Optional[bool] = None
        if platform.system() == "Windows":
            try:
                import tkinter
                from tkinter import messagebox
                root = tkinter.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                answer = bool(messagebox.askyesno(WELLNESS_TITLE, WELLNESS_TEXT, icon="info"))
                root.destroy()
            except Exception as exc:
                logger.debug("wellness consent dialog unavailable: %s", exc)
        if answer is None and sys.stdin.isatty():
            print("=" * 72)
            print(WELLNESS_TITLE)
            print("=" * 72)
            print(WELLNESS_TEXT)
            try:
                answer = input("Type YES to enable optional wearable collection, anything else to decline: ").strip().upper() == "YES"
            except (EOFError, KeyboardInterrupt):
                answer = False
        if answer is True:
            _record_wellness_consent()
            return True
        logger.info("optional wellness collection declined or no prompt available; wearable stays off")
        return False
    except Exception as exc:  # fail closed
        logger.error("wellness consent check failed: %s; wearable stays off", type(exc).__name__)
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Manage WAMSS workstation consent")
    parser.add_argument("--withdraw", action="store_true", help="prompt to withdraw workstation activity consent")
    args = parser.parse_args()
    if not args.withdraw:
        parser.error("specify --withdraw to revoke workstation consent")
    raise SystemExit(0 if prompt_withdraw_consent() else 1)
