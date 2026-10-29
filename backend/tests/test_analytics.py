"""Tests for the weekly trend analytics in app.analytics (router mounted on a
dedicated test FastAPI app, per the agent-4 contract)."""

from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.activity import strain_level
from app.activity_models import ActivityLog
from app.database import SessionLocal, get_db
from app.analytics import router as analytics_router
from app.focus import Segment, compute_focus_score

A4 = "a4t"  # unique prefix so these tests do not collide with other suites


def _override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(analytics_router)
    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _cleanup(db):
    db.query(ActivityLog).filter(ActivityLog.employee_id.like(f"{A4}%")).delete(
        synchronize_session=False
    )
    db.commit()


def _add_row(db, employee_id: str, start: datetime, duration_min: float,
             app_name: str = "code", idle: bool = False, strain=None):
    db.add(
        ActivityLog(
            machine_id=f"{A4}-m1",
            employee_id=employee_id,
            office_id="hq",
            event_type="activity",
            app_name=app_name,
            window_title="w",
            process_name="p",
            start_time=start,
            end_time=start + timedelta(minutes=duration_min),
            keystrokes=10,
            mouse_clicks=2,
            is_idle=idle,
            strain_score=strain,
        )
    )


def _seed(db):
    """Seed two employees across week 0 (recent) and week 1 (older)."""
    _cleanup(db)
    today = date.today()
    emp_focus = f"{A4}e_focus"
    emp_strain = f"{A4}e_strain"

    # Week 0: focused day for emp_focus -> high score.
    day0 = datetime(today.year, today.month, today.day, 9, 0, tzinfo=timezone.utc)
    for i in range(4):
        start = day0 + timedelta(minutes=i * 90)
        _add_row(db, emp_focus, start, 60, app_name="code", strain=30)

    # Week 1: fragmented day for emp_focus -> low score (30 one-minute segments).
    old_day = day0 - timedelta(days=10)
    apps = ["code", "browser", "slack", "terminal", "email"]
    for i in range(30):
        _add_row(db, emp_focus, old_day + timedelta(minutes=i * 5), 1,
                 app_name=apps[i % len(apps)], strain=30)

    # Strain rows this week for emp_strain: 2 red, 2 amber, 1 green, 1 unknown.
    s0 = day0 + timedelta(minutes=5)
    for i, score in enumerate([80, 85, 50, 60, 20, None]):
        _add_row(db, emp_strain, s0 + timedelta(minutes=i * 40), 20,
                 app_name="code", strain=score)
    db.commit()
    return emp_focus, emp_strain


def _week_start(d: date) -> date:
    return d - timedelta(days=6)


def test_focus_trends_math():
    db = SessionLocal()
    emp_focus, _ = _seed(db)
    db.close()

    client = _make_client()
    resp = client.get("/analytics/trends", params={"metric": "focus", "weeks": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["metric"] == "focus"
    assert data["weeks"] == 2
    assert len(data["team"]) == 2

    today = date.today()
    week0_start = today - timedelta(days=6)
    week1_start = today - timedelta(days=13)

    mine = [r for r in data["per_employee"] if r["employee_id"] == emp_focus]
    assert len(mine) == 2
    by_week = {r["week_start"]: r for r in mine}

    # Expected scores computed with the same pure scorer the endpoint uses.
    day0 = datetime(today.year, today.month, today.day, 9, 0, tzinfo=timezone.utc)
    focused_segs = [
        Segment(day0 + timedelta(minutes=i * 90),
                day0 + timedelta(minutes=i * 90 + 60), "code", False)
        for i in range(4)
    ]
    expected_high = compute_focus_score(focused_segs)["score"]
    assert by_week[week0_start.isoformat()]["average_focus"] == expected_high
    assert by_week[week0_start.isoformat()]["days_with_data"] == 1

    old_day = day0 - timedelta(days=10)
    apps = ["code", "browser", "slack", "terminal", "email"]
    frag_segs = [
        Segment(old_day + timedelta(minutes=i * 5),
                old_day + timedelta(minutes=i * 5 + 1), apps[i % len(apps)], False)
        for i in range(30)
    ]
    expected_low = compute_focus_score(frag_segs)["score"]
    assert expected_low < expected_high
    assert by_week[week1_start.isoformat()]["average_focus"] == expected_low

    # Internal consistency of the team rollup: mean of per-employee weekly means.
    for team_row in data["team"]:
        week = team_row["week_start"]
        emp_avgs = [
            r["average_focus"]
            for r in data["per_employee"]
            if r["week_start"] == week and r["average_focus"] is not None
        ]
        expected_team = round(sum(emp_avgs) / len(emp_avgs), 1)
        assert team_row["average_focus"] == expected_team
        assert team_row["employee_count"] == len(emp_avgs)


def test_strain_trends_math():
    db = SessionLocal()
    _, emp_strain = _seed(db)
    db.close()

    client = _make_client()
    resp = client.get("/analytics/trends", params={"metric": "strain", "weeks": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["metric"] == "strain"

    today = date.today()
    week0_start = (today - timedelta(days=6)).isoformat()

    mine = [r for r in data["per_employee"]
            if r["employee_id"] == emp_strain and r["week_start"] == week0_start]
    assert len(mine) == 1
    row = mine[0]

    scores = [80, 85, 50, 60, 20]
    assert row["average_strain"] == round(sum(scores) / len(scores), 1)
    expected_levels = {"green": 0, "amber": 0, "red": 0, "unknown": 0}
    for s in scores + [None]:
        expected_levels[strain_level(s)] += 1
    assert row["green"] == expected_levels["green"] == 1
    assert row["amber"] == expected_levels["amber"] == 2
    assert row["red"] == expected_levels["red"] == 2
    assert row["unknown"] == expected_levels["unknown"] == 1
    assert row["records"] == 6

    # Team rollup: summed level counts match the sum over employees in the week.
    team = [t for t in data["team"] if t["week_start"] == week0_start][0]
    for key in ("green", "amber", "red", "unknown"):
        assert team[key] == sum(
            r[key] for r in data["per_employee"] if r["week_start"] == week0_start
        )


def test_trends_rejects_bad_metric():
    client = _make_client()
    resp = client.get("/analytics/trends", params={"metric": "bogus", "weeks": 2})
    assert resp.status_code == 422
