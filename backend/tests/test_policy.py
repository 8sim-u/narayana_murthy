"""Unit tests for the policy engine (pure function, no database)."""
from datetime import datetime, timedelta, timezone

from app.policy import default_rules, evaluate_policies


def seg(start_h, start_m, end_h, end_m, is_idle=False, day=15):
    base = datetime(2026, 10, 30, tzinfo=timezone.utc)
    return {
        "start": base.replace(hour=start_h, minute=start_m),
        "end": base.replace(hour=end_h, minute=end_m),
        "is_idle": is_idle,
    }


def policies_of(violations):
    return {v["policy"]: v for v in violations}


def test_compliant_day_is_clean():
    rules = default_rules()
    # 90-min work blocks separated by 20-min idle gaps (>= 15 min reset),
    # 30-min idle lunch inside 12:30-13:30, 6 h total. All within limits.
    segments = [
        seg(9, 0, 10, 30),
        seg(10, 30, 10, 50, is_idle=True),
        seg(10, 50, 12, 20),
        seg(12, 30, 13, 0, is_idle=True),  # lunch, fully inside the window
        seg(13, 0, 14, 30),
        seg(14, 30, 14, 50, is_idle=True),
        seg(14, 50, 16, 20),
    ]
    assert evaluate_policies(segments, rules) == []


def test_missed_lunch_break_flagged():
    rules = default_rules()
    # Active straight through the 12:30-13:30 window -> no qualifying break
    segments = [seg(9, 0, 12, 0), seg(12, 0, 17, 0)]
    v = policies_of(evaluate_policies(segments, rules))
    assert "missed_lunch_break" in v
    assert v["missed_lunch_break"]["severity"] == "amber"


def test_work_after_21_flagged():
    rules = default_rules()
    segments = [seg(9, 0, 12, 0), seg(20, 30, 22, 15)]
    v = policies_of(evaluate_policies(segments, rules))
    assert "no_work_after_hours" in v
    assert v["no_work_after_hours"]["severity"] == "red"
    assert "75" in v["no_work_after_hours"]["detail"]  # 21:00-22:15 = 75 min


def test_long_continuous_block_flagged():
    rules = default_rules()
    # 150 min of continuous work with only 5-min gaps (< 15 min reset gap)
    segments = [seg(9, 0, 10, 0), seg(10, 5, 11, 0), seg(11, 5, 11, 35)]
    v = policies_of(evaluate_policies(segments, rules))
    assert "max_continuous_work" in v
    assert v["max_continuous_work"]["severity"] == "red"


def test_qualifying_break_resets_continuous():
    rules = default_rules()
    # 100 min work, 20 min idle (>= 15 min reset), 100 min work -> no violation
    segments = [
        seg(9, 0, 10, 40),
        seg(10, 40, 11, 0, is_idle=True),
        seg(11, 0, 12, 40),
    ]
    # Idle is not counted toward continuous work; add an idle segment as a gap
    assert "max_continuous_work" not in policies_of(evaluate_policies(segments, rules))


def test_excessive_daily_hours_flagged():
    rules = default_rules()
    segments = [seg(6, 0, 12, 0), seg(13, 0, 19, 0)]  # 12 h active
    v = policies_of(evaluate_policies(segments, rules))
    assert "max_daily_active_hours" in v
    assert v["max_daily_active_hours"]["severity"] == "red"


def test_empty_segments_no_violations():
    assert evaluate_policies([], default_rules()) == []


def test_custom_rules_are_honored():
    rules = default_rules()
    rules["continuous_work"]["max_continuous_minutes"] = 300
    segments = [seg(9, 0, 10, 0), seg(10, 5, 11, 0)]
    assert "max_continuous_work" not in policies_of(evaluate_policies(segments, rules))
