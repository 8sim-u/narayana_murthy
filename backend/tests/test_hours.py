"""Tests for the per-user hours baselining in app.hours (router mounted on a
dedicated test FastAPI app, mirroring the test_analytics.py pattern).

All activity rows are synthetic. Timezone is pinned to UTC via
WAMSS_HOURS_TZ so bin math is deterministic.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ["WAMSS_HOURS_TZ"] = "UTC"

from app.activity_models import ActivityLog  # noqa: E402
from app.database import SessionLocal, get_db  # noqa: E402
from app.hours import (  # noqa: E402
    after_hours_flag,
    burstiness,
    compute_baseline,
    daily_active_spans,
    normal_window,
    router as hours_router,
    time_anomaly_score,
)

A5 = "a5h"  # unique prefix so these tests do not collide with other suites


def _override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(hours_router)
    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _cleanup(db):
    db.query(ActivityLog).filter(ActivityLog.employee_id.like(f"{A5}%")).delete(
        synchronize_session=False
    )
    db.commit()


def _add_row(db, employee_id, start, duration_min, idle=False):
    db.add(
        ActivityLog(
            machine_id=f"{A5}-m1",
            employee_id=employee_id,
            office_id="hq",
            event_type="activity",
            app_name="code",
            window_title="w",
            process_name="p",
            start_time=start,
            end_time=start + timedelta(minutes=duration_min),
            keystrokes=10,
            mouse_clicks=2,
            is_idle=idle,
        )
    )


def _workday_rows(db, employee_id, day_start, hours=(9, 18)):
    """Seed one full synthetic workday: hourly 50-minute segments."""
    for h in range(hours[0], hours[1]):
        _add_row(db, employee_id, day_start + timedelta(hours=h), 50)


def _seed_baseline(db, employee_id, end=None, vary=False):
    """Seed 28 full 09:00-18:00 workdays at offsets 2..29 days ago.

    Offsets 2..29 keep every seeded row strictly inside both the
    /hours/baseline 30-day window and the /hours/anomaly 30-day-minus-24h
    baseline window regardless of the time of day the test runs, so the
    day counts are deterministic.

    With vary=True, odd offsets use 40-minute segments (360 min/day) instead
    of 50-minute segments (450 min/day): 14 days each, mean 405, std 45, so
    z-score math is exercisable through the endpoints.
    """
    _cleanup(db)
    end = end or datetime.now(timezone.utc)
    for d in range(2, 30):
        day_start = (end - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        seg_min = 40 if (vary and d % 2) else 50
        for h in range(9, 18):
            _add_row(db, employee_id, day_start + timedelta(hours=h), seg_min)
    db.commit()


# ---------------------------------------------------------------- pure math


def test_normal_window_of_identical_workdays():
    emp = f"{A5}e_pure"
    rows = []
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for d in range(10):
        day = base + timedelta(days=d)
        rows.append(_row_like(day + timedelta(hours=9), 60))
        rows.append(_row_like(day + timedelta(hours=17), 60))
    spans = daily_active_spans(rows, "UTC")
    start, end = normal_window(spans)
    assert start == "09:00"
    assert end == "18:00"


class _RowLike:
    def __init__(self, start, end):
        self.start_time = start
        self.end_time = end
        self.event_type = "activity"
        self.is_idle = False


def _row_like(start, duration_min):
    return _RowLike(start, start + timedelta(minutes=duration_min))


def test_idle_rows_excluded_from_spans():
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    idle = _RowLike(base + timedelta(hours=9), base + timedelta(hours=10))
    idle.is_idle = True
    assert daily_active_spans([idle], "UTC") == []


def test_time_anomaly_score_zero_std_is_zero():
    assert time_anomaly_score(999.0, 540.0, 0.0) == 0.0
    assert time_anomaly_score(600.0, 540.0, 30.0) == pytest.approx(2.0)


def test_after_hours_flag_inside_and_outside_window():
    bins = [0.0] * 96
    bins[40] = 15.0  # 10:00, inside 09:00-18:00
    assert after_hours_flag(bins, "09:00", "18:00") is False
    bins[88] = 15.0  # 22:00, outside
    assert after_hours_flag(bins, "09:00", "18:00") is True


def test_after_hours_flag_falls_back_to_work_hours():
    bins = [0.0] * 96
    bins[88] = 15.0
    assert after_hours_flag(bins, None, None, "09:00-18:00") is True
    bins2 = [0.0] * 96
    bins2[40] = 15.0
    assert after_hours_flag(bins2, None, None, "09:00-18:00") is False


def test_burstiness_uniform_vs_concentrated():
    uniform = [10.0] * 96
    concentrated = [0.0] * 96
    concentrated[40] = 960.0
    assert burstiness(uniform) == pytest.approx(0.0)
    assert burstiness(concentrated) > 5.0
    assert burstiness([0.0] * 96) == 0.0


# ---------------------------------------------------------------- endpoints


def test_baseline_endpoint_returns_normal_window():
    emp = f"{A5}e_base"
    db = SessionLocal()
    try:
        _seed_baseline(db, emp)
        client = _make_client()
        resp = client.get("/hours/baseline", params={"employee_id": emp})
        assert resp.status_code == 200
        body = resp.json()
        assert body["employee_id"] == emp
        assert body["normal_start"] == "09:00"
        assert body["normal_end"] == "18:00"
        assert body["days_used"] == 28
        assert len(body["bins"]) == 96
        assert body["bins"][36] == 28  # 09:00 bin active every day
        assert body["bins"][0] == 0
        assert body["mean_daily_active_minutes"] == pytest.approx(450.0)
    finally:
        _cleanup(db)
        db.close()


def test_baseline_endpoint_no_data_returns_nulls():
    emp = f"{A5}e_empty"
    db = SessionLocal()
    try:
        _cleanup(db)
        client = _make_client()
        resp = client.get("/hours/baseline", params={"employee_id": emp})
        assert resp.status_code == 200
        body = resp.json()
        assert body["normal_start"] is None
        assert body["normal_end"] is None
        assert body["days_used"] == 0
        assert body["bins"] == [0] * 96
    finally:
        db.close()


def test_anomaly_endpoint_flags_after_hours_and_high_z():
    emp = f"{A5}e_anom"
    db = SessionLocal()
    try:
        _seed_baseline(db, emp, vary=True)
        # Recent 24h: four 15-min rows at 22:00 (after-hours vs the 09-18
        # baseline). The [now-24h, now] window always contains exactly one
        # 22:00; rows starting at/after now are excluded by the endpoint
        # query, so the expected minutes are computed, not hardcoded.
        now = datetime.now(timezone.utc)
        t22 = (now - timedelta(hours=24)).replace(hour=22, minute=0, second=0, microsecond=0)
        if t22 < now - timedelta(hours=24):
            t22 += timedelta(days=1)
        starts = [t22 + timedelta(minutes=15 * i) for i in range(4)]
        for s in starts:
            _add_row(db, emp, s, 15)
        db.commit()
        counted = [s for s in starts if now - timedelta(hours=24) <= s < now]
        expected_minutes = 15.0 * len(counted)
        assert counted, "at least the 22:00 row must fall inside the 24h window"
        client = _make_client()
        resp = client.get("/hours/anomaly", params={"employee_id": emp})
        assert resp.status_code == 200
        body = resp.json()
        assert body["employee_id"] == emp
        assert body["after_hours_activity"] is True
        assert body["recent_active_minutes"] == pytest.approx(expected_minutes)
        # Baseline with vary=True: mean 405, std 45.
        assert body["time_anomaly_score"] == pytest.approx((expected_minutes - 405.0) / 45.0)
        assert body["burstiness"] >= 0.0
        assert body["normal_start"] == "09:00"
        assert body["normal_end"] == "18:00"
    finally:
        _cleanup(db)
        db.close()


def test_anomaly_endpoint_quiet_day_scores_low():
    emp = f"{A5}e_quiet"
    db = SessionLocal()
    try:
        _seed_baseline(db, emp, vary=True)
        # Recent 24h: one normal midday hour, inside the window.
        now = datetime.now(timezone.utc)
        midday = now.replace(hour=10, minute=0, second=0, microsecond=0)
        _add_row(db, emp, midday, 60)
        db.commit()
        client = _make_client()
        resp = client.get("/hours/anomaly", params={"employee_id": emp})
        assert resp.status_code == 200
        body = resp.json()
        assert body["after_hours_activity"] is False
        assert abs(body["time_anomaly_score"]) > 0
    finally:
        _cleanup(db)
        db.close()


def test_compute_baseline_days_used_counts_only_active_days():
    emp = f"{A5}e_sparse"
    rows = []
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for d in (0, 2, 5):  # gaps: only 3 active days
        day = base + timedelta(days=d)
        rows.append(_row_like(day + timedelta(hours=9), 120))
    summary = compute_baseline(rows, "UTC")
    assert summary["days_used"] == 3
    assert summary["bins"][36] == 3
    assert summary["mean_daily_active_minutes"] == pytest.approx(120.0)
