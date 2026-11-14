"""Windows service for ordered upload of the workstation agent's local spool.

Foreground app/input collection stays in the consent-gated per-user session:
Windows services run in an isolated session and cannot reliably observe that
desktop. This service owns only network sync and shares the collector's spool.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import servicemanager
import win32event
import win32security
import win32service
import win32serviceutil

SERVICE_NAME = "WAMSSActivitySync"
BASE_DIR = Path(__file__).resolve().parent
SECRET_CONFIG_KEYS = {"WAMSS_EMPLOYEE_TOKEN", "WAMSS_API_KEY"}


def _account_sid(identity: str) -> str:
    try:
        sid, _domain, _use = win32security.LookupAccountName(None, identity)
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        return ""


def _load_config() -> None:
    """Load the installed agent's config before importing its env-based settings."""
    config_path = BASE_DIR / "config.env"
    try:
        lines = config_path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        raise RuntimeError("Agent configuration file is missing.") from None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in SECRET_CONFIG_KEYS:
            continue
        os.environ[key] = value.strip().strip("\"'")

    from secret_store import load_into_environment

    secret_dir = os.environ.get("WAMSS_SECRET_DIR")
    if not secret_dir:
        raise RuntimeError("Protected credential directory is not configured.")
    load_into_environment(secret_dir, required=True)


class WorkstationSyncService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "WAMSS Workstation Activity Sync"
    _svc_description_ = (
        "Uploads consented WAMSS workstation activity from the local durable spool."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("WAMSS activity sync service starting")
        try:
            self._sync_until_stopped()
        except Exception as exc:
            logging.exception("WAMSS sync service stopped after an error")
            servicemanager.LogErrorMsg(
                f"WAMSS activity sync service failed: {type(exc).__name__}"
            )
            raise

    def _sync_until_stopped(self) -> None:
        _load_config()
        sys.path.insert(0, str(BASE_DIR))
        from activity_agent import (
            MAX_BACKOFF_SEC, SYNC_INTERVAL, Spool, Syncer, spool_dir_for_identity,
        )
        import consent

        expected_os_user = os.environ.get("WAMSS_INTERACTIVE_USER", "").strip().casefold()
        expected_os_sid = os.environ.get("WAMSS_INTERACTIVE_SID", "").strip().casefold()
        configured_employee = os.environ.get("WAMSS_EMPLOYEE_ID", "").strip()
        if not expected_os_user or not expected_os_sid or not configured_employee:
            raise RuntimeError("The bound interactive account or employee identity is missing.")

        active_spool_dir = None
        syncer = None
        while True:
            if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                return

            # Resolve the active interactive user afresh: service identity and
            # import-time identity are not authoritative for consent. Each
            # employee/OS-user pair has its own spool; never drain another
            # user's queued records under the current user's consent.
            active_os_user = consent.current_os_user()
            employee_id = consent.configured_employee_id(active_os_user) if active_os_user else ""
            active_os_sid = _account_sid(active_os_user) if active_os_user else ""
            if (not active_os_user or active_os_user != expected_os_user
                    or active_os_sid.casefold() != expected_os_sid
                    or employee_id != configured_employee
                    or not consent.is_consented(employee_id=employee_id, os_user=active_os_user)):
                active_spool_dir = None
                syncer = None
                delay = min(max(SYNC_INTERVAL, 30.0), MAX_BACKOFF_SEC)
            else:
                selected_spool_dir = spool_dir_for_identity(active_os_user, employee_id)
                if not selected_spool_dir:
                    delay = min(max(SYNC_INTERVAL, 30.0), MAX_BACKOFF_SEC)
                else:
                    if selected_spool_dir != active_spool_dir:
                        syncer = Syncer(Spool(selected_spool_dir), os_user=active_os_user)
                        active_spool_dir = selected_spool_dir
                    if syncer.sync_once():
                        delay = SYNC_INTERVAL
                    else:
                        delay = min(syncer.next_delay(), MAX_BACKOFF_SEC)

            result = win32event.WaitForSingleObject(self.stop_event, int(max(1.0, delay) * 1000))
            if result == win32event.WAIT_OBJECT_0:
                return


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(WorkstationSyncService)
