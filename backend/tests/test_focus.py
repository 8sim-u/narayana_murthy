"""Unit tests for the pure focus scorer in app.focus (no DB, no HTTP)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.focus import Segment, break_compliance, compute_focus_score


def seg(start_min: float, duration_min: float, app: str = "code", idle: bool = False) -> Segment:
    start = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=start_min)
    return Segment(
        start_time=start,
        end_time=start + timedelta(minutes=duration_min),
        app_name=app,
        is_idle=idle,
    )


def test_deep_work_day_scores_high():
    # 4 x 60-minute uninterrupted blocks of coding = 4 focused blocks, no switches.
    segments = [seg(i * 90, 60, "code") for i in range(4)]
    result = compute_focus_score(segments)
    assert result["focused_blocks"] == 4
    assert result["focus_reward"] == 8
    assert result["switch_penalty"] == 0
    assert result["fragment_penalty"] == 0
    assert result["score"] >= 95


def test_fragmented_switching_day_scores_low():
    # 60 x 1-minute segments across 12 apps: constant switching + fragmentation.
    # (4-minute gaps between segments so they do not merge into one stretch.)
    apps = ["code", "browser", "slack", "terminal", "email", "notes",
            "docs", "chat", "ide", "git", "music", "filemgr"]
    segments = [seg(i * 5, 1, apps[i % len(apps)]) for i in range(60)]
    result = compute_focus_score(segments)
    assert result["app_switches"] == 59
    assert result["switch_penalty"] > 0
    assert result["short_segments"] == 60
    assert result["fragment_penalty"] == (60 - 6) * 2  # 6 free short segments
    assert result["focused_blocks"] == 0
    assert result["score"] < 30


def test_all_idle_day_scores_low():
    # A day of only idle rows: no activity to reward, idle-heavy penalty applies.
    segments = [seg(i * 60, 45, "idle", idle=True) for i in range(8)]
    result = compute_focus_score(segments)
    assert result["idle_fraction"] == 1.0
    assert result["focused_blocks"] == 0
    assert result["score"] == 70  # 100 - 30 idle penalty


def test_empty_day_scores_zero():
    assert compute_focus_score([])["score"] == 0


def test_score_always_clamped():
    # Extreme fragmentation cannot push the score below 0.
    segments = [seg(i, 0.5, f"app{i}") for i in range(200)]
    result = compute_focus_score(segments)
    assert 0 <= result["score"] <= 100


def test_break_compliance_requires_breaks_for_long_days():
    # 8 hours active with two 10-minute breaks: 480/90 -> 6 breaks required.
    segments = [seg(i * 70, 60, "code") for i in range(8)]
    segments += [seg(200, 10, "", idle=True), seg(400, 10, "", idle=True)]
    compliance = break_compliance(segments)
    assert compliance["required_breaks"] == 6
    assert compliance["taken_breaks"] == 2
    assert compliance["compliant"] is False


def test_break_compliance_healthy_day():
    # 3 hours active with two 10-minute breaks: 180/90 -> 2 breaks required.
    segments = [seg(i * 60, 50, "code") for i in range(3)]
    segments += [seg(200, 10, "", idle=True), seg(300, 10, "", idle=True)]
    compliance = break_compliance(segments)
    assert compliance["required_breaks"] == 2
    assert compliance["taken_breaks"] == 2
    assert compliance["compliant"] is True


def test_streak_badge_boundaries():
    from app.focus import badges_for_streaks

    assert "Focused Starter" in badges_for_streaks(3, 0)
    assert "Deep Work Pro" in badges_for_streaks(7, 0)
    assert "Zen Master" in badges_for_streaks(30, 0)
    assert "Well Rested" in badges_for_streaks(0, 5)
    assert "Break Champion" in badges_for_streaks(0, 14)
    assert badges_for_streaks(2, 4) == []
