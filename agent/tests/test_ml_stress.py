"""
Unit and end-to-end tests for agent/ml_stress.py and agent/train.py.

All data is synthetic. No real keystroke content, key identities, or human
subjects are involved: only generated timestamps and coordinates.
"""

import csv
import math
import os
import random
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml_stress import (  # noqa: E402
    RollingBaseline,
    SupervisedStressModel,
    extract_keystroke_features,
    extract_mouse_features,
    slice_events,
    slice_pairs,
    unsupervised_strain_score,
)

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# ---------------------------------------------------------------------------
# Synthetic stream generators ("calm" vs "stressed" typing/mouse behavior).
# ---------------------------------------------------------------------------

def synth_calm_keys(rng):
    """Steady rhythm: ~4 keys/s, low dwell variance, two long pauses."""
    pairs = []
    t = 0.0
    for i in range(240):
        dwell = max(0.03, 0.08 + rng.gauss(0.0, 0.01))
        pairs.append((t, t + dwell))
        t += max(0.10, 0.25 + rng.gauss(0.0, 0.02))
        if i in (80, 160):
            t += 1.6
    return pairs


def synth_stressed_keys(rng):
    """Bursty rhythm: high dwell variance, many short pauses, fast bursts."""
    pairs = []
    t = 0.0
    for i in range(300):
        dwell = max(0.02, 0.09 + rng.gauss(0.0, 0.05))
        pairs.append((t, t + dwell))
        if i % 25 == 24:
            t += 1.2 + rng.random() * 0.8  # 12 short pauses
        else:
            t += max(0.05, 0.16 + rng.gauss(0.0, 0.08))
    return pairs


def synth_calm_mouse(rng):
    """Smooth straight-line strokes with a click at each stroke end."""
    events = []
    t = 0.0
    for _ in range(5):
        x0, y0 = rng.random() * 800.0, rng.random() * 600.0
        x1, y1 = rng.random() * 800.0, rng.random() * 600.0
        for i in range(40):
            f = i / 39.0
            events.append({
                "ts": t,
                "x": x0 + (x1 - x0) * f + rng.gauss(0.0, 1.5),
                "y": y0 + (y1 - y0) * f + rng.gauss(0.0, 1.5),
                "kind": "move",
            })
            t += 0.05
        events.append({"ts": t, "x": x1, "y": y1, "kind": "click_down"})
        events.append({"ts": t + 0.09, "x": x1, "y": y1, "kind": "click_up"})
        t += 0.3
    return events


def synth_stressed_mouse(rng):
    """Jittery random-walk movement: high path deviation, many turns."""
    events = []
    x, y, t = 400.0, 300.0, 0.0
    for i in range(200):
        ang = rng.random() * 2.0 * math.pi
        step = 8.0 + rng.random() * 20.0
        x += math.cos(ang) * step
        y += math.sin(ang) * step
        events.append({"ts": t, "x": x, "y": y, "kind": "move"})
        t += 0.05
        if i % 40 == 39:
            events.append({"ts": t, "x": x, "y": y, "kind": "click_down"})
            events.append({"ts": t + 0.12 + rng.random() * 0.10,
                           "x": x, "y": y, "kind": "click_up"})
            t += 0.2
    return events


def combined_features(pairs, events):
    feats = extract_keystroke_features(pairs)
    feats.update(extract_mouse_features(events))
    return feats


# ---------------------------------------------------------------------------
# Keystroke extractor tests.
# ---------------------------------------------------------------------------

def test_empty_keystroke_window_zero_fills():
    feats = extract_keystroke_features([])
    assert feats
    assert all(v == 0.0 for v in feats.values())


def test_keystroke_feature_sanity():
    # 10 keys, 0.1 s dwell, 0.3 s press-to-press spacing, no pauses.
    pairs = [(i * 0.3, i * 0.3 + 0.1) for i in range(10)]
    f = extract_keystroke_features(pairs)
    assert f["kb_n_keys"] == 10.0
    assert f["kb_dwell_mean"] == pytest.approx(0.1)
    assert f["kb_dwell_std"] == pytest.approx(0.0)
    assert f["kb_dwell_min"] == pytest.approx(0.1)
    assert f["kb_flight_min"] == pytest.approx(0.3)
    assert f["kb_digraph_mean"] == pytest.approx(0.3)
    assert f["kb_velocity"] == pytest.approx(10.0 / 60.0)
    assert f["kb_pause_count"] == 0.0
    assert f["kb_duty_cycle"] == pytest.approx(1.0 / 60.0)


def test_keystroke_pause_structure():
    pairs = [(0.0, 0.1), (2.5, 2.6), (5.0, 5.1)]
    f = extract_keystroke_features(pairs)
    assert f["kb_pause_count"] == 2.0
    assert f["kb_pause_mean"] == pytest.approx(2.4)


def test_keystroke_correction_counts_are_aggregates_only():
    pairs = [(0.0, 0.1), (0.3, 0.4)]
    f = extract_keystroke_features(
        pairs, correction_counts={"backspace": 3, "delete": 1})
    assert f["kb_backspace_count"] == 3.0
    assert f["kb_delete_count"] == 1.0
    # Privacy: feature dict holds only aggregate timing/count features,
    # never per-key identities. The documented 15-column schema is fixed.
    assert len(f) == 15
    assert all(isinstance(v, float) for v in f.values())


def test_slice_helpers():
    pairs = [(1.0, 1.1), (61.0, 61.1), (119.0, 119.1)]
    assert len(slice_pairs(pairs, 0.0, 60.0)) == 1
    assert len(slice_pairs(pairs, 60.0, 120.0)) == 2
    events = [{"ts": 1.0, "x": 0, "y": 0, "kind": "move"},
              {"ts": 61.0, "x": 0, "y": 0, "kind": "move"}]
    assert len(slice_events(events, 0.0, 60.0)) == 1


# ---------------------------------------------------------------------------
# Mouse extractor tests.
# ---------------------------------------------------------------------------

def test_empty_mouse_window_zero_fills():
    feats = extract_mouse_features([])
    assert feats
    assert all(v == 0.0 for v in feats.values())


def test_mouse_straight_line_has_no_jaggedness():
    events = [{"ts": float(i), "x": float(i * 10), "y": 0.0, "kind": "move"}
              for i in range(10)]
    f = extract_mouse_features(events)
    assert f["ms_move_count"] == 10.0
    assert f["ms_deviation_mean"] == pytest.approx(0.0)
    assert f["ms_direction_changes"] == 0.0
    assert f["ms_velocity_mean"] == pytest.approx(10.0)


def test_mouse_jitter_has_high_jaggedness():
    rng = random.Random(7)
    events = [{"ts": float(i) * 0.05,
               "x": float(i * 10) + rng.gauss(0.0, 30.0),
               "y": rng.gauss(0.0, 30.0),
               "kind": "move"} for i in range(60)]
    f = extract_mouse_features(events)
    assert f["ms_deviation_mean"] > 1.0
    assert f["ms_direction_changes"] > 5.0


def test_mouse_click_features():
    events = [
        {"ts": 1.0, "x": 100.0, "y": 100.0, "kind": "click_down"},
        {"ts": 1.1, "x": 100.0, "y": 100.0, "kind": "click_up"},
        {"ts": 2.0, "x": 200.0, "y": 100.0, "kind": "click_down"},
        {"ts": 2.2, "x": 200.0, "y": 100.0, "kind": "click_up"},
        {"ts": 3.0, "x": 0.0, "y": 0.0, "kind": "scroll"},
    ]
    f = extract_mouse_features(events)
    assert f["ms_click_count"] == 2.0
    assert f["ms_click_dwell_mean"] == pytest.approx(0.15)
    assert f["ms_interclick_distance_mean"] == pytest.approx(100.0)
    assert f["ms_interclick_time_mean"] == pytest.approx(1.0)
    assert f["ms_scroll_count"] == 1.0


# ---------------------------------------------------------------------------
# Baseline tests.
# ---------------------------------------------------------------------------

def test_baseline_cold_start_yields_zero_zscores():
    b = RollingBaseline(min_windows=10)
    b.update({"a": 1.0})
    z = b.zscore({"a": 100.0, "b": 5.0})
    assert z["a"] == 0.0  # below min_windows
    assert z["b"] == 0.0  # unseen feature


def test_baseline_typical_value_scores_near_zero():
    b = RollingBaseline(min_windows=5)
    rng = random.Random(3)
    for _ in range(20):
        b.update({"v": 10.0 + rng.gauss(0.0, 1.0)})
    z = b.zscore({"v": 10.2})
    assert abs(z["v"]) < 1.0


def test_baseline_outlier_scores_high():
    b = RollingBaseline(min_windows=5)
    rng = random.Random(3)
    for _ in range(20):
        b.update({"v": 10.0 + rng.gauss(0.0, 1.0)})
    z = b.zscore({"v": 20.0})
    assert z["v"] > 5.0


# ---------------------------------------------------------------------------
# Unsupervised score tests.
# ---------------------------------------------------------------------------

def test_unsupervised_score_zero_for_neutral_z():
    z = {k: 0.0 for k in ("kb_dwell_std", "kb_flight_std", "kb_velocity",
                          "ms_deviation_mean", "ms_direction_changes",
                          "kb_pause_count", "kb_digraph_std",
                          "ms_speed_accuracy_interaction")}
    assert unsupervised_strain_score(z) == 0.0
    assert unsupervised_strain_score({}) == 0.0


def test_unsupervised_score_bounded_and_monotonic():
    mild = {"kb_dwell_std": 1.0, "kb_flight_std": 1.0}
    wild = {"kb_dwell_std": 10.0, "kb_flight_std": 10.0,
            "kb_velocity": -10.0, "ms_deviation_mean": 10.0,
            "ms_direction_changes": 10.0, "kb_pause_count": 10.0,
            "kb_digraph_std": 10.0, "ms_speed_accuracy_interaction": 10.0}
    s_mild = unsupervised_strain_score(mild)
    s_wild = unsupervised_strain_score(wild)
    assert 0.0 < s_mild < s_wild <= 100.0
    assert s_wild == 100.0  # clipped


def test_unsupervised_score_pause_shift_is_directional():
    up = unsupervised_strain_score({"kb_pause_count": 2.0})
    down = unsupervised_strain_score({"kb_pause_count": -2.0})
    assert up > 0.0
    assert down == 0.0  # fewer pauses than baseline is not strain


# ---------------------------------------------------------------------------
# End-to-end synthetic calm vs stressed test.
# ---------------------------------------------------------------------------

def test_end_to_end_stressed_scores_higher_than_calm():
    baseline = RollingBaseline(min_windows=5)
    for w in range(12):
        rng = random.Random(1000 + w)
        baseline.update(combined_features(
            synth_calm_keys(rng), synth_calm_mouse(rng)))

    calm = combined_features(
        synth_calm_keys(random.Random(999)),
        synth_calm_mouse(random.Random(999)))
    stressed = combined_features(
        synth_stressed_keys(random.Random(4242)),
        synth_stressed_mouse(random.Random(4242)))

    s_calm = unsupervised_strain_score(baseline.zscore(calm))
    s_stressed = unsupervised_strain_score(baseline.zscore(stressed))

    assert 0.0 <= s_calm <= 100.0
    assert 0.0 <= s_stressed <= 100.0
    assert s_stressed > s_calm, (s_calm, s_stressed)


# ---------------------------------------------------------------------------
# Supervised path: train CLI end to end on synthetic labeled data.
# ---------------------------------------------------------------------------

def _labeled_rows(n_per_class, seed):
    rng = random.Random(seed)
    rows = []
    for _ in range(n_per_class):
        rows.append((combined_features(
            synth_calm_keys(rng), synth_calm_mouse(rng)), 0))
    for _ in range(n_per_class):
        rows.append((combined_features(
            synth_stressed_keys(rng), synth_stressed_mouse(rng)), 1))
    return rows


def test_train_cli_end_to_end(tmp_path):
    pytest.importorskip("sklearn")
    rows = _labeled_rows(30, seed=11)
    feature_names = sorted(rows[0][0].keys())
    csv_path = tmp_path / "labeled.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(feature_names + ["stress"])
        for feats, label in rows:
            writer.writerow([feats[c] for c in feature_names] + [label])

    out_path = tmp_path / "models" / "stress_rf.joblib"
    proc = subprocess.run(
        [sys.executable, "-m", "agent.train",
         "--csv", str(csv_path), "--out", str(out_path),
         "--n-estimators", "50", "--seed", "42"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "cv accuracy" in proc.stdout
    assert out_path.exists()

    model = SupervisedStressModel(str(out_path))
    calm_row = combined_features(
        synth_calm_keys(random.Random(2024)),
        synth_calm_mouse(random.Random(2024)))
    stressed_row = combined_features(
        synth_stressed_keys(random.Random(31337)),
        synth_stressed_mouse(random.Random(31337)))
    p_calm = model.predict(calm_row)
    p_stressed = model.predict(stressed_row)
    assert 0.0 <= p_calm <= 100.0
    assert 0.0 <= p_stressed <= 100.0
    assert p_stressed > p_calm, (p_calm, p_stressed)
