"""Unit tests for backend/scripts/weekly_digest.py (Agent K)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from weekly_digest import build_digest  # noqa: E402


def _rows():
    activity_rows = [
        {"employee_id": "E001", "is_idle": False, "strain_score": 55},
        {"employee_id": "E001", "is_idle": True, "strain_score": None},
        {"employee_id": "E002", "is_idle": False, "strain_score": 82},
        {"employee_id": "E003", "is_idle": False, "strain_score": 25},
    ]
    focus_rows = [
        {"employee_id": "E001", "focus_score": 72.5, "break_compliant": True, "streak_days": 4},
        {"employee_id": "E002", "focus_score": 58.0, "break_compliant": False, "streak_days": 0},
        {"employee_id": "E003", "focus_score": 91.0, "break_compliant": True, "streak_days": 12},
    ]
    strain_rows = [
        {"employee_id": "E001", "strain_level": "amber", "strain_score": 55},
        {"employee_id": "E002", "strain_level": "red", "strain_score": 82},
        {"employee_id": "E003", "strain_level": "green", "strain_score": 25},
    ]
    policy_flags = [
        {"employee_id": "E002", "flag": "EXCESSIVE_OVERTIME", "detail": "logged 62h"},
        {"employee_id": "E001", "flag": "UNAUTHORIZED_APP", "detail": "game client"},
    ]
    presence_rows = [
        {"employee_id": "E001", "status": "present", "anomaly": None},
        {"employee_id": "E002", "status": "present", "anomaly": "no badge-out after 22:00"},
        {"employee_id": "E003", "status": "absent", "anomaly": None},
    ]
    return activity_rows, focus_rows, strain_rows, policy_flags, presence_rows


def test_top_strain_employee_appears_in_plain():
    plain, _ = build_digest(*_rows())
    assert "E002" in plain
    assert "RED" in plain


def test_policy_flags_listed_in_plain():
    plain, _ = build_digest(*_rows())
    assert "EXCESSIVE_OVERTIME" in plain
    assert "UNAUTHORIZED_APP" in plain


def test_html_contains_stats_and_flags():
    _, html = build_digest(*_rows())
    assert "<html>" in html
    assert "E002" in html
    assert "EXCESSIVE_OVERTIME" in html
    assert "no badge-out after 22:00" in html


def test_empty_inputs_degrade_gracefully():
    plain, html = build_digest([], [], [], [], [])
    assert "no data" in plain
    assert "none reported" in plain
    assert "none raised" in plain
    assert "none detected" in plain
    assert "<html>" in html
