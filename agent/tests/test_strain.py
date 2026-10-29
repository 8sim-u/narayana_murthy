"""Unit tests for the strain-score heuristic (pure function, no OS deps)."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strain import StrainConfig, compute_strain_score, score_level  # noqa: E402


def test_zero_activity_scores_zero():
    r = compute_strain_score(0, 0, 0, False, StrainConfig())
    assert r.score == 0
    assert r.level == "green"


def test_saturated_signals_score_100_red():
    r = compute_strain_score(200, 500, 50, True, StrainConfig())
    assert r.score == 100
    assert r.level == "red"


def test_continuous_work_alone_stays_below_break_threshold():
    # 90+ active minutes saturate only the 0.30-weighted component -> 30.
    r = compute_strain_score(120, 0, 0, False, StrainConfig())
    assert r.score == 30
    assert r.level == "green"


def test_high_intensity_burst_scores_amber():
    # 120/min input saturates the 0.25 component -> 25; plus 60 active min (20) -> 45.
    r = compute_strain_score(60, 150, 0, False, StrainConfig())
    assert r.score == 45
    assert r.level == "amber"


def test_outside_hours_adds_twenty_points():
    base = compute_strain_score(60, 0, 0, False, StrainConfig())
    late = compute_strain_score(60, 0, 0, True, StrainConfig())
    assert late.score - base.score == 20


def test_switching_signal_saturates_at_threshold():
    a = compute_strain_score(0, 0, 10, False, StrainConfig())
    b = compute_strain_score(0, 0, 100, False, StrainConfig())
    assert a.score == 25
    assert b.score == 25  # capped at 1.0


def test_break_threshold_boundary():
    cfg = StrainConfig(break_threshold=70)
    below = compute_strain_score(200, 200, 20, False, cfg)
    assert below.score == 80
    assert below.score >= cfg.break_threshold
    assert below.level == "red"


def test_level_boundaries():
    assert score_level(0) == "green"
    assert score_level(39) == "green"
    assert score_level(40) == "amber"
    assert score_level(69) == "amber"
    assert score_level(70) == "red"
    assert score_level(100) == "red"


def test_custom_config_rescales():
    cfg = StrainConfig(active_min_threshold=45, intensity_per_min=60,
                       switches_per_5min=5, break_threshold=50)
    r = compute_strain_score(45, 0, 0, False, cfg)
    assert r.score == 30  # 0.30 weight still applies, threshold just lower


def test_overnight_work_hours_window():
    cfg = StrainConfig(work_hours="22:00-06:00")
    assert cfg.outside_work_hours(datetime(2026, 1, 1, 12, 0)) is True
    assert cfg.outside_work_hours(datetime(2026, 1, 1, 23, 30)) is False
    assert cfg.outside_work_hours(datetime(2026, 1, 1, 5, 59)) is False


def test_daytime_work_hours_window():
    cfg = StrainConfig(work_hours="09:00-18:00")
    assert cfg.outside_work_hours(datetime(2026, 1, 1, 10, 0)) is False
    assert cfg.outside_work_hours(datetime(2026, 1, 1, 20, 0)) is True


def test_env_override():
    os.environ["WAMSS_STRAIN_ACTIVE_MIN_THRESHOLD"] = "45"
    try:
        cfg = StrainConfig.from_env()
        assert cfg.active_min_threshold == 45
    finally:
        del os.environ["WAMSS_STRAIN_ACTIVE_MIN_THRESHOLD"]
