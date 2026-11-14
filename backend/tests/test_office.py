"""Round 3 (Agent 5) multi-office tests.

Verifies the office_id model and the optional office_id query filters on
GET /activity, GET /presence, GET /focus/daily and GET /security/alerts.

Seeds two offices ("hq-test" and "branch-test"), filters, and asserts only
the matching office's rows come back.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, text

from app.database import engine


@pytest.fixture(scope="module", autouse=True)
def _ensure_office_columns():
    """Round 3 adds office_id to activity_logs and security_events.

    The shared test DB file may predate that column (create_all never
    ALTERs), so add it idempotently when it is missing. Existing rows get
    the "hq" default, matching the model default.
    """
    with engine.begin() as conn:
        inspector = inspect(conn)
        existing = set(inspector.get_table_names())
        for table in ("activity_logs", "security_events"):
            if table not in existing:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "office_id" not in cols:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN office_id VARCHAR(64) NOT NULL DEFAULT 'hq'"
                    )
                )


def _now():
    return datetime.now(timezone.utc)


def _activity_record(machine, employee, office, **kw):
    start = _now() - timedelta(minutes=5)
    rec = {
        "machine_id": machine,
        "employee_id": employee,
        "event_type": "activity",
        "app_name": "code",
        "window_title": "test window",
        "process_name": "code",
        "start_time": start.isoformat(),
        "end_time": _now().isoformat(),
        "keystrokes": 10,
        "mouse_clicks": 5,
        "is_idle": False,
        "strain_score": 20,
    }
    if office is not None:
        rec["office_id"] = office
    rec.update(kw)
    return rec


def _seed_activity(client, records, monkeypatch):
    by_employee = {}
    for record in records:
        by_employee.setdefault(record["employee_id"], []).append(record)
    responses = []
    for employee_id, employee_records in by_employee.items():
        token = f"office-test-token:{employee_id}"
        monkeypatch.setenv(
            "WAMSS_EMPLOYEE_TOKEN_HASHES",
            json.dumps({employee_id: hashlib.sha256(token.encode()).hexdigest()}),
        )
        response = client.post(
            "/activity", json={"records": employee_records},
            headers={"X-Employee-Token": token},
        )
        assert response.status_code == 201, response.text
        responses.append(response.json())
    return responses


def _get(client, path, employee_id, monkeypatch):
    token = f"office-test-token:{employee_id}"
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({employee_id: hashlib.sha256(token.encode()).hexdigest()}),
    )
    res = client.get(path, headers={"X-Employee-Token": token})
    assert res.status_code == 200, res.text
    return res.json()


def test_activity_office_filter(client, monkeypatch):
    """Two offices seeded; office_id filter returns only matching rows."""
    _seed_activity(
        client,
        [
            _activity_record("OFFICE_HQ_M1", "office-emp-hq", "hq-test"),
            _activity_record("OFFICE_HQ_M1", "office-emp-hq", "hq-test"),
            _activity_record("OFFICE_BR_M1", "office-emp-br", "branch-test"),
            _activity_record("OFFICE_BR_M1", "office-emp-br", "branch-test"),
        ],
        monkeypatch,
    )

    hq = _get(client, "/activity?office_id=hq-test&machine_id=OFFICE_HQ_M1", "office-emp-hq", monkeypatch)
    assert len(hq) >= 2  # >= : the shared test DB persists rows across runs
    assert all(r["office_id"] == "hq-test" for r in hq)
    assert all("office_id" in r for r in hq)

    br = _get(client, "/activity?office_id=branch-test&machine_id=OFFICE_BR_M1", "office-emp-br", monkeypatch)
    assert len(br) >= 2
    assert all(r["office_id"] == "branch-test" for r in br)

    # Cross-check: the hq machine has no branch-test rows.
    none_ = _get(client, "/activity?office_id=branch-test&machine_id=OFFICE_HQ_M1", "office-emp-hq", monkeypatch)
    assert none_ == []


def test_activity_office_defaults_to_hq(client, monkeypatch):
    """Records posted without office_id land in the default office."""
    _seed_activity(
        client, [_activity_record("OFFICE_DEF_M1", "office-emp-def", None)], monkeypatch
    )
    rows = _get(client, "/activity?machine_id=OFFICE_DEF_M1", "office-emp-def", monkeypatch)
    assert rows, "expected the seeded row back"
    assert rows[0]["office_id"] == "hq"


def test_presence_office_filter(client, monkeypatch):
    """Presence correlates only the selected office's workstation activity."""
    monkeypatch.setenv("WAMSS_ZONE_MAP", '{"OFFICEZONE": "OFFICE_PRES_M1"}')
    _seed_activity(
        client,
        [_activity_record("OFFICE_PRES_M1", "office-emp-pres", "branch-test")],
        monkeypatch,
    )

    states = _get(client, "/presence?office_id=branch-test", "office-emp-pres", monkeypatch)
    row = next(r for r in states if r["machine_id"] == "OFFICE_PRES_M1")
    assert row["active"] is True
    assert row["state"] == "ACTIVE_BUT_ABSENT"  # no vision events in OFFICEZONE
    assert row["anomaly"] is True

    states_hq = _get(client, "/presence?office_id=hq-test", "office-emp-pres", monkeypatch)
    assert states_hq == []  # A filter outside this employee's own office returns no personal state.


def test_focus_daily_office_filter(client, monkeypatch):
    """Focus score is computed from the selected office's rows only."""
    start = _now() - timedelta(minutes=40)
    rec = _activity_record("OFFICE_FOCUS_M1", "office-emp-focus", "hq-test")
    rec["start_time"] = start.isoformat()
    rec["end_time"] = _now().isoformat()
    _seed_activity(client, [rec], monkeypatch)

    hq = _get(
        client,
        "/focus/daily?employee_id=office-emp-focus&office_id=hq-test",
        "office-emp-focus",
        monkeypatch,
    )
    assert hq["employee_id"] == "office-emp-focus"
    assert hq["breakdown"]["active_minutes"] > 0

    other = _get(
        client,
        "/focus/daily?employee_id=office-emp-focus&office_id=branch-test",
        "office-emp-focus",
        monkeypatch,
    )
    assert other["focus_score"] == 0
    assert other["breakdown"]["active_minutes"] == 0


def test_security_alerts_office_filter(client, monkeypatch):
    """USB alerts carry office_id and filter by it."""
    token = "office-test-token:office-emp-sec"
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({"office-emp-sec": hashlib.sha256(token.encode()).hexdigest()}),
    )
    res = client.post(
        "/security/events",
        json={
            "events": [
                {
                    "machine_id": "OFFICE_SEC_M1",
                    "employee_id": "office-emp-sec",
                    "office_id": "branch-test",
                    "type": "usb_insert",
                    "detail": {"device": "USB-8GB"},
                    "timestamp": _now().isoformat(),
                }
            ]
        },
        headers={"X-Employee-Token": token},
    )
    assert res.status_code == 201, res.text

    alerts = _get(client, "/security/alerts?office_id=branch-test", "office-emp-sec", monkeypatch)
    match = [a for a in alerts if a["machine_id"] == "OFFICE_SEC_M1"]
    assert match, "expected the seeded USB alert in branch-test"
    assert match[0]["office_id"] == "branch-test"
    assert match[0]["severity"] == "high"

    alerts_hq = _get(client, "/security/alerts?office_id=hq-test", "office-emp-sec", monkeypatch)
    assert all(a["machine_id"] != "OFFICE_SEC_M1" for a in alerts_hq)
