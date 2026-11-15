"""Regression tests for the Windows tray monitor's shared consent wiring."""

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import work_monitor  # noqa: E402


def test_tray_gates_with_configured_identity_without_setting_marker_path(monkeypatch):
    calls = {}
    consent = types.ModuleType("consent")

    def configured_employee_id():
        return "configured-employee"

    def ensure_consent(*, employee_id=None):
        calls["employee_id"] = employee_id
        return False

    consent.configured_employee_id = configured_employee_id
    consent.ensure_consent = ensure_consent
    monkeypatch.setitem(sys.modules, "consent", consent)
    monkeypatch.setattr(work_monitor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(work_monitor, "load_config", lambda: None)
    monkeypatch.setattr(
        work_monitor.argparse.ArgumentParser,
        "parse_args",
        lambda _self: types.SimpleNamespace(server="https://example.invalid", idle_after="60", interval="5"),
    )
    monkeypatch.setattr(work_monitor, "_show_error", lambda _message: None)
    monkeypatch.delenv("WAMSS_CONSENT_FILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/example/AppData/Local")

    assert work_monitor.main() == 1
    assert calls["employee_id"] == "configured-employee"
    assert "WAMSS_CONSENT_FILE" not in os.environ
