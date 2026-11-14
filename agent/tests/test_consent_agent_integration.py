"""Regression coverage for employee-bound consent and sync-service gating."""

import importlib
import os
import sys
import types
from pathlib import Path

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)


def test_agent_binds_consent_checks_to_configured_employee(monkeypatch):
    import activity_agent

    calls = []
    monkeypatch.setattr(activity_agent, "EMPLOYEE_ID", "configured-employee")
    monkeypatch.setattr(
        activity_agent, "ensure_consent",
        lambda **kwargs: calls.append(("ensure", kwargs)) or False,
    )
    monkeypatch.setattr(activity_agent, "is_consented", lambda **kwargs: True)

    try:
        activity_agent.run(stop_after=0)
    except SystemExit:
        pass

    assert calls == [("ensure", {"employee_id": "configured-employee"})]


def test_spool_paths_are_isolated_by_employee_and_os_user(tmp_path, monkeypatch):
    import activity_agent

    monkeypatch.setenv("WAMSS_SPOOL_DIR", str(tmp_path))
    alice = activity_agent.spool_dir_for_identity("alice", "E-1")
    bob = activity_agent.spool_dir_for_identity("bob", "E-1")
    another_employee = activity_agent.spool_dir_for_identity("alice", "E-2")

    assert alice != bob
    assert alice != another_employee
    assert Path(alice).parent == tmp_path
    assert Path(bob).parent == tmp_path
    assert "alice" not in alice.casefold()
    assert "bob" not in bob.casefold()


def test_windows_service_calls_shared_consent_gate_before_sync(monkeypatch):
    # The service is Windows-only at runtime; stubs let this test exercise its
    # control flow without importing or invoking any Windows behavior.
    win32event = types.ModuleType("win32event")
    win32event.WAIT_OBJECT_0 = 0
    win32event.CreateEvent = lambda *args: object()
    win32event.SetEvent = lambda *args: None
    wait_results = iter([1, 0])
    win32event.WaitForSingleObject = lambda *args: next(wait_results)

    win32service = types.ModuleType("win32service")
    win32service.SERVICE_STOP_PENDING = 3
    win32serviceutil = types.ModuleType("win32serviceutil")

    class ServiceFramework:
        def __init__(self, args):
            pass

        def ReportServiceStatus(self, status):
            pass

    win32serviceutil.ServiceFramework = ServiceFramework
    win32serviceutil.HandleCommandLine = lambda *args: None
    servicemanager = types.ModuleType("servicemanager")
    servicemanager.LogInfoMsg = lambda *args: None
    servicemanager.LogErrorMsg = lambda *args: None
    monkeypatch.setitem(sys.modules, "win32event", win32event)
    monkeypatch.setitem(sys.modules, "win32service", win32service)
    monkeypatch.setitem(sys.modules, "win32serviceutil", win32serviceutil)
    monkeypatch.setitem(sys.modules, "servicemanager", servicemanager)

    import activity_agent

    gate_calls = []
    consent_stub = types.SimpleNamespace(
        current_os_user=lambda: "alice",
        configured_employee_id=lambda os_user=None: "E-1",
        is_consented=lambda **kwargs: gate_calls.append(kwargs) or False,
    )
    monkeypatch.setitem(sys.modules, "consent", consent_stub)

    spool_paths = []
    syncers = []
    class FakeSpool:
        def __init__(self, path):
            self.path = path
            spool_paths.append(path)

    class FakeSyncer:
        def __init__(self, spool):
            syncers.append(self)
            self.sync_calls = 0

        def sync_once(self):
            self.sync_calls += 1
            return True

    monkeypatch.setattr(activity_agent, "Spool", FakeSpool)
    monkeypatch.setattr(activity_agent, "Syncer", FakeSyncer)
    service_module = importlib.import_module("windows_sync_service")
    monkeypatch.setattr(service_module, "_load_config", lambda: None)

    service = service_module.WorkstationSyncService([])
    service._sync_until_stopped()

    assert gate_calls == [{"employee_id": "E-1", "os_user": "alice"}]
    assert spool_paths == []
    assert syncers == []

    consent_stub.current_os_user = lambda: "bob"
    consent_stub.is_consented = lambda **kwargs: gate_calls.append(kwargs) or True
    monkeypatch.setattr(
        activity_agent, "spool_dir_for_identity",
        lambda os_user=None, employee_id=None: f"/spool/{os_user}/{employee_id}",
    )
    wait_results = iter([1, 0])
    service._sync_until_stopped()

    assert gate_calls[-1] == {"employee_id": "E-1", "os_user": "bob"}
    assert spool_paths == ["/spool/bob/E-1"]
    assert len(syncers) == 1
    assert syncers[0].sync_calls == 1
