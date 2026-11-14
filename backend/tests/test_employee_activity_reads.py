"""Employee identity scoping regressions for activity-history routes.

These tests are intentionally added without executing the test suite in this task.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

def _record(employee_id: str, app_name: str):
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    start = now - timedelta(minutes=50)
    return {
        "record_id": str(uuid.uuid4()),
        "machine_id": f"read-auth-machine-{employee_id}",
        "employee_id": employee_id,
        "office_id": "read-auth-office",
        "event_type": "activity",
        "app_name": app_name,
        "window_title": "private window",
        "process_name": app_name,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=25)).isoformat(),
        "keystrokes": 0,
        "mouse_clicks": 0,
        "is_idle": False,
        "strain_score": 40,
    }


def _configure_tokens(monkeypatch, token_by_employee):
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({
            employee_id: hashlib.sha256(token.encode("utf-8")).hexdigest()
            for employee_id, token in token_by_employee.items()
        }),
    )


def test_activity_reads_require_employee_token_and_are_bound_to_self(client, monkeypatch):
    employee_a = f"read-auth-a-{uuid.uuid4().hex}"
    employee_b = f"read-auth-b-{uuid.uuid4().hex}"
    token_a = f"employee-token-a-{uuid.uuid4().hex}"
    token_b = f"employee-token-b-{uuid.uuid4().hex}"

    _configure_tokens(monkeypatch, {employee_a: token_a, employee_b: token_b})
    posted_a = client.post(
        "/activity", json={"records": [_record(employee_a, "editor-a")]},
        headers={"X-Employee-Token": token_a},
    )
    posted_b = client.post(
        "/activity", json={"records": [_record(employee_b, "editor-b")]},
        headers={"X-Employee-Token": token_b},
    )
    assert posted_a.status_code == 201, posted_a.text
    assert posted_b.status_code == 201, posted_b.text

    deployment_key_only = client.get(
        "/activity", headers={"X-API-Key": "deployment-key-only"}
    )
    assert deployment_key_only.status_code == 401

    headers_a = {"X-Employee-Token": token_a}
    activity = client.get("/activity", headers=headers_a)
    assert activity.status_code == 200, activity.text
    assert activity.json()
    assert activity.headers["cache-control"] == "no-store, private"
    # CORSMiddleware appends Origin to Vary, so assert membership rather
    # than an exact value.
    assert "X-Employee-Token" in activity.headers["vary"]
    assert {row["employee_id"] for row in activity.json()} == {employee_a}
    assert all("editor-b" not in row["app_name"] for row in activity.json())

    app_usage = client.get("/activity/app-usage", headers=headers_a)
    assert app_usage.status_code == 200, app_usage.text
    assert app_usage.json()
    assert {row["employee_id"] for row in app_usage.json()} == {employee_a}
    assert all(row["app_name"] == "editor-a" for row in app_usage.json())

    strain = client.get("/activity/strain-summary", headers=headers_a)
    assert strain.status_code == 200, strain.text
    assert strain.json()
    assert {row["employee_id"] for row in strain.json()} == {employee_a}

    for path in ("/activity", "/activity/app-usage"):
        forbidden = client.get(path, params={"employee_id": employee_b}, headers=headers_a)
        assert forbidden.status_code == 403


def test_employee_identity_fails_closed_for_missing_bad_or_ambiguous_config(client, monkeypatch):
    monkeypatch.delenv("WAMSS_EMPLOYEE_TOKEN_HASHES", raising=False)
    missing = client.get("/activity")
    assert missing.status_code == 503

    monkeypatch.setenv("WAMSS_EMPLOYEE_TOKEN_HASHES", "not-json")
    malformed = client.get("/activity", headers={"X-Employee-Token": "anything"})
    assert malformed.status_code == 503

    token = "reused-employee-token"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        json.dumps({"employee-one": digest, "employee-two": digest}),
    )
    ambiguous = client.get("/activity", headers={"X-Employee-Token": token})
    assert ambiguous.status_code == 401


def test_duplicate_json_keys_in_employee_mapping_fail_closed(client, monkeypatch):
    token = "duplicate-key-token"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    monkeypatch.setenv(
        "WAMSS_EMPLOYEE_TOKEN_HASHES",
        '{"employee-one":"' + digest + '","employee-one":"' + digest + '"}',
    )
    response = client.get("/activity", headers={"X-Employee-Token": token})
    assert response.status_code == 503


def test_missing_employee_token_is_rejected_even_when_mapping_is_configured(client, monkeypatch):
    token = "configured-but-not-sent"
    _configure_tokens(monkeypatch, {f"read-auth-{uuid.uuid4().hex}": token})
    response = client.get("/activity/app-usage")
    assert response.status_code == 401
