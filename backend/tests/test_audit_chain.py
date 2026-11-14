from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.activity_models import ActivityAuditHead, ActivityLog
from app.audit import GENESIS_HASH, activity_event_hash, verify_activity_chain
from app.models import Base


@pytest.fixture
def audit_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _append_sample_records(db, count=2):
    head = ActivityAuditHead(id=1, last_event_id=None, last_hash=GENESIS_HASH)
    db.add(head)
    db.flush()
    start = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
    records = []
    for index in range(count):
        record = ActivityLog(
            machine_id="machine-test",
            employee_id="employee-test",
            office_id="hq",
            event_type="activity",
            app_name="editor",
            window_title=f"Work window {index}",
            process_name="editor.exe",
            start_time=start + timedelta(minutes=index),
            end_time=start + timedelta(minutes=index + 1),
            keystrokes=3,
            mouse_clicks=1,
            is_idle=False,
        )
        db.add(record)
        db.flush()
        record.previous_hash = head.last_hash
        record.event_hash = activity_event_hash(record, record.previous_hash)
        head.last_event_id = record.id
        head.last_hash = record.event_hash
        records.append(record)
    db.commit()
    return records


def test_verifier_accepts_a_valid_chain(audit_db):
    _append_sample_records(audit_db)

    result = verify_activity_chain(audit_db)

    assert result["valid"] is True
    assert result["checked_records"] == 2
    assert result["first_broken_event_id"] is None


def test_verifier_reports_the_first_tampered_event(audit_db):
    first, _ = _append_sample_records(audit_db)
    first_id = first.id
    first.window_title = "Silently rewritten"
    audit_db.commit()

    result = verify_activity_chain(audit_db)

    assert result["valid"] is False
    assert result["first_broken_event_id"] == first_id
    assert result["error"] == "event data hash does not match"


def test_verifier_reports_a_broken_previous_link(audit_db):
    _, second = _append_sample_records(audit_db)
    second.previous_hash = "f" * 64
    audit_db.commit()

    result = verify_activity_chain(audit_db)

    assert result["valid"] is False
    assert result["first_broken_event_id"] == second.id
    assert result["error"] == "previous hash does not match the preceding retained event"
