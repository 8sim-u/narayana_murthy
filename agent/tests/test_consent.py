"""Regression tests for identity-bound workstation consent."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import consent


def test_valid_same_identity_consent(tmp_path):
    path = tmp_path / "consent.json"
    consent.record_consent(path, employee_id="E-1", os_user="alice")
    assert consent.is_consented(path, employee_id="E-1", os_user="alice")


def test_employee_mismatch_fails_closed(tmp_path):
    path = tmp_path / "consent.json"
    consent.record_consent(path, employee_id="E-1", os_user="alice")
    assert not consent.is_consented(path, employee_id="E-2", os_user="alice")


def test_os_user_mismatch_fails_closed(tmp_path):
    path = tmp_path / "consent.json"
    consent.record_consent(path, employee_id="E-1", os_user="alice")
    assert not consent.is_consented(path, employee_id="E-1", os_user="bob")


def test_explicit_windows_os_user_is_normalized_consistently(tmp_path, monkeypatch):
    monkeypatch.setattr(consent.platform, "system", lambda: "Windows")
    path = tmp_path / "consent.json"
    consent.record_consent(path, employee_id="E-1", os_user="DOMAIN\\Alice")

    assert consent.is_consented(path, employee_id="E-1", os_user="domain\\ALICE")
    assert consent.withdraw_consent(path, employee_id="E-1", os_user="DOMAIN\\alice")
    assert not path.exists()


def test_old_unbound_marker_fails_closed(tmp_path):
    path = tmp_path / "consent.json"
    path.write_text(json.dumps({"consented": True, "version": "3"}))
    assert not consent.is_consented(path, employee_id="E-1", os_user="alice")


def test_default_path_uses_durable_per_user_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(consent.platform, "system", lambda: "Linux")
    monkeypatch.setattr(consent, "current_os_user", lambda: "alice")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("WAMSS_CONSENT_FILE", raising=False)

    assert consent.marker_path() == tmp_path / "wamss" / "consent.json"


def test_shared_override_is_used_exactly_for_any_identity(tmp_path, monkeypatch):
    shared = tmp_path / "shared-consent.json"
    monkeypatch.setenv("WAMSS_CONSENT_FILE", str(shared))

    assert consent.marker_path(os_user="alice") == shared
    assert consent.marker_path(os_user="bob") == shared


def test_windows_unknown_session_identity_fails_closed(monkeypatch):
    monkeypatch.setattr(consent.platform, "system", lambda: "Windows")
    monkeypatch.setattr(consent, "_windows_process_session_id", lambda: None)
    monkeypatch.setattr(consent, "_windows_session_identity", lambda: (None, None))
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setenv("USERDOMAIN", "DOMAIN")
    monkeypatch.delenv("SESSIONNAME", raising=False)

    assert consent.current_os_user() is None


def test_prompt_acceptance_records_only_after_explicit_yes(tmp_path, monkeypatch):
    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent, "marker_path", lambda *args, **kwargs: path)
    monkeypatch.setattr(consent, "current_os_user", lambda: "alice")
    monkeypatch.setattr(consent, "prompt_for_consent", lambda: None)
    assert not consent.ensure_consent(employee_id="E-1")
    assert not path.exists()
    monkeypatch.setattr(consent, "prompt_for_consent", lambda: True)
    assert consent.ensure_consent(employee_id="E-1")
    assert consent.is_consented(path, employee_id="E-1", os_user="alice")


def test_missing_active_identity_cannot_prompt_or_consent(monkeypatch):
    prompted = []
    monkeypatch.setattr(consent, "current_os_user", lambda: None)
    monkeypatch.setattr(consent, "prompt_for_consent", lambda: prompted.append(True))

    assert not consent.ensure_consent(employee_id="E-1")
    assert prompted == []


def test_withdrawal_mismatch_preserves_shared_marker(tmp_path):
    path = tmp_path / "shared.json"
    consent.record_consent(path, employee_id="E-1", os_user="alice")
    original = path.read_text()
    assert not consent.withdraw_consent(path, employee_id="E-2", os_user="alice")
    assert path.read_text() == original


def test_malformed_marker_withdrawal_fails_closed(tmp_path):
    path = tmp_path / "consent.json"
    path.write_text("not-json")

    assert not consent.withdraw_consent(path, employee_id="E-1", os_user="alice")
    assert path.exists()
