"""Unit tests for behavioral + heart-rate fusion (agent/fusion.py).

All input is synthetic. No real user data, no I/O.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fusion import fuse, hr_is_fresh, hr_score_from_bpm  # noqa: E402

NOW = 1_700_000_000.0


def _hr(bpm, ts=NOW, resting=70.0):
    return {"bpm": bpm, "ts": ts, "resting_bpm": resting}


def test_hr_score_curve():
    assert hr_score_from_bpm(70, 70) == 0.0       # at resting
    assert hr_score_from_bpm(85, 70) == 0.0       # below resting+20
    assert hr_score_from_bpm(90, 70) == 0.0       # exactly resting+20
    assert hr_score_from_bpm(110, 70) == 50.0     # halfway to cap
    assert hr_score_from_bpm(130, 70) == 100.0    # resting+60 saturates
    assert hr_score_from_bpm(250, 70) == 100.0    # clamped


def test_missing_hr_returns_behavioral_unchanged():
    score, meta = fuse(55.0, {"k_mean": 0.25}, None)
    assert score == 55.0
    assert meta == {"hr_missing": True, "mode": "behavioral_only"}


def test_stale_hr_treated_as_missing():
    stale = _hr(130, ts=NOW - 200.0)
    score, meta = fuse(55.0, {}, stale, now=NOW)
    assert score == 55.0
    assert meta["hr_missing"] is True
    assert meta["mode"] == "behavioral_only"


def test_freshness_boundary():
    assert hr_is_fresh(_hr(90, ts=NOW - 119.0), now=NOW) is True
    assert hr_is_fresh(_hr(90, ts=NOW - 121.0), now=NOW) is False
    assert hr_is_fresh({"bpm": 90}, now=NOW) is True   # missing ts: fresh
    assert hr_is_fresh(None, now=NOW) is False
    assert hr_is_fresh({"bpm": "fast"}, now=NOW) is False
    assert hr_is_fresh({"bpm": 999}, now=NOW) is False  # outside sanity range


def test_early_fusion_both_modalities():
    # behavioral=50, bpm=120, resting=70 -> hr_score=75
    # early = 100*(0.7*0.5 + 0.3*0.75) + 10*0.5*0.75 = 61.25
    score, meta = fuse(50.0, {"k_mean": 0.25}, _hr(120), now=NOW)
    assert score == pytest.approx(61.25)
    assert meta["mode"] == "early_fusion"
    assert meta["hr_missing"] is False
    assert meta["hr_bpm"] == 120.0
    assert meta["hr_score"] == pytest.approx(75.0)
    assert len(meta["feature_vector"]) == 3
    assert len(meta["feature_layout"]) == 3
    assert meta["interaction_bonus"] == pytest.approx(3.75)


def test_early_fusion_interaction_rewards_joint_elevation():
    # Same HR, higher behavioral load -> interaction term grows.
    low, _ = fuse(20.0, {}, _hr(120), now=NOW)
    high, _ = fuse(90.0, {}, _hr(120), now=NOW)
    assert high > low
    # Late fusion has no interaction term: pure weighted average.
    late, late_meta = fuse(60.0, {}, _hr(110), mode="late", now=NOW)
    assert late_meta["mode"] == "late_fusion"
    # hr_score for bpm=110, resting=70 is 50 -> 0.7*60 + 0.3*50 = 57
    assert late == pytest.approx(57.0)


def test_late_mode_explicit():
    score, meta = fuse(60.0, {}, _hr(110), mode="late", weights=(0.5, 0.5), now=NOW)
    assert score == pytest.approx(0.5 * 60.0 + 0.5 * 50.0)
    assert meta["mode"] == "late_fusion"


def test_weights_are_normalized():
    a, _ = fuse(60.0, {}, _hr(110), mode="late", weights=(0.7, 0.3), now=NOW)
    b, _ = fuse(60.0, {}, _hr(110), mode="late", weights=(7.0, 3.0), now=NOW)
    assert a == pytest.approx(b)


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        fuse(50.0, {}, _hr(100), mode="quantum", now=NOW)


def test_scores_clamped():
    score, _ = fuse(100.0, {}, _hr(250), now=NOW)
    assert score <= 100.0
    score2, _ = fuse(0.0, {}, _hr(72), now=NOW)
    assert score2 >= 0.0


def test_sample_resting_bpm_overrides_param():
    # Sample says resting=80: bpm=100 is exactly resting+20 -> hr_score 0.
    score, meta = fuse(50.0, {}, _hr(100, resting=80.0), resting_bpm=70.0, now=NOW)
    assert meta["resting_bpm"] == 80.0
    assert meta["hr_score"] == 0.0


def test_missing_ts_is_fresh_documented_choice():
    score, meta = fuse(50.0, {}, {"bpm": 120, "resting_bpm": 70.0}, now=NOW)
    assert meta["mode"] == "early_fusion"


def test_behavioral_feature_keys_carried_into_meta():
    _, meta = fuse(50.0, {"k_mean": 0.25, "m_rate": 3.0}, _hr(100), now=NOW)
    assert meta["behavioral_feature_keys"] == ["k_mean", "m_rate"]


def test_realistic_now_defaults():
    score, meta = fuse(50.0, {}, {"bpm": 100, "ts": time.time(), "resting_bpm": 70})
    assert meta["mode"] == "early_fusion"
    assert 0.0 <= score <= 100.0
