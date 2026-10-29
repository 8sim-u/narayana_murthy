"""Integration tests for the round-4 strain pipeline wiring (agent/strain.py).

Covers: pipeline with agent.ml_stress ABSENT (heuristic fallback, no crash)
and PRESENT (fake module injected into sys.modules), fatigue wiring at
session end, HR fusion wiring, the anonymized timing buffer, and proof that
pre-existing strain behavior is untouched.

All input is synthetic. No real user data, no I/O.
"""

import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from fatigue import FatigueTracker  # noqa: E402
from strain import (  # noqa: E402
    AnonymizedTimingBuffer,
    StrainConfig,
    _reset_round4_state,
    compute_strain_score,
    enrich_strain_score,
    extract_ml_features,
    ml_behavioral_score,
    ml_stress_available,
    score_level,
)
from wearable_ble import HeartRateStream  # noqa: E402


# ------------------------------------------------- fake agent.ml_stress

class _FakeBaseline:
    def __init__(self):
        self.seen = []

    def update(self, features):
        self.seen.append(dict(features))


def _install_fake_ml(feature_sequence=None, score_value=82.0, extract_error=False):
    """Inject a fake ml_stress module into sys.modules (Agent A builds the
    real one in parallel; the pipeline must work against the contract)."""
    mod = types.ModuleType("ml_stress")
    seq = list(feature_sequence) if feature_sequence else [{"k_mean": 0.25, "m_rate": 10.0}]
    idx = {"i": 0}

    def _next():
        f = seq[idx["i"] % len(seq)]
        idx["i"] += 1
        return dict(f)

    def extract_keystroke_features(intervals):
        if extract_error:
            raise RuntimeError("synthetic extractor failure")
        return _next()

    def extract_mouse_features(intervals):
        if extract_error:
            raise RuntimeError("synthetic extractor failure")
        return {"m_clicks": 5.0}

    def unsupervised_strain_score(features, baseline=None):
        return score_value

    mod.extract_keystroke_features = extract_keystroke_features
    mod.extract_mouse_features = extract_mouse_features
    mod.RollingBaseline = _FakeBaseline
    mod.unsupervised_strain_score = unsupervised_strain_score
    sys.modules["ml_stress"] = mod
    sys.modules.pop("agent.ml_stress", None)
    return mod


def _remove_fake_ml():
    sys.modules.pop("ml_stress", None)
    sys.modules.pop("agent.ml_stress", None)


@pytest.fixture(autouse=True)
def _clean_state():
    _remove_fake_ml()
    _reset_round4_state()
    yield
    _remove_fake_ml()
    _reset_round4_state()


def _heuristic():
    return compute_strain_score(60, 150, 0, False, StrainConfig())  # score 45, amber


# ------------------------------------------------- ml_stress absent

def _block_ml_import(monkeypatch):
    """Simulate agent.ml_stress being unavailable: importlib raises
    ImportError when the name maps to None in sys.modules."""
    monkeypatch.setitem(sys.modules, "ml_stress", None)
    monkeypatch.setitem(sys.modules, "agent.ml_stress", None)


def test_ml_absent_falls_back_to_heuristic_no_crash(monkeypatch):
    _block_ml_import(monkeypatch)
    assert ml_stress_available() is False
    assert extract_ml_features([0.2] * 30) is None
    assert ml_behavioral_score({"k_mean": 0.25}) is None

    h = _heuristic()
    enriched = enrich_strain_score(h, key_intervals=[0.2] * 30, mouse_intervals=[0.5] * 10)
    assert enriched.ml_score is None
    assert enriched.ml_features is None
    assert enriched.fatigue_event is None
    assert enriched.score == h.score
    assert enriched.level == h.level
    assert enriched.heuristic is h
    assert enriched.fusion_meta == {"hr_missing": True, "mode": "behavioral_only"}


def test_ml_absent_session_end_uses_heuristic_components_for_fatigue(monkeypatch):
    _block_ml_import(monkeypatch)
    h = _heuristic()
    tracker = FatigueTracker(employee_id="w1")
    for _ in range(4):
        e = enrich_strain_score(h, session_ended=True, fatigue_tracker=tracker)
        assert e.fatigue_event is None
        assert e.ml_features is None  # proves the heuristic-component path
    assert tracker.session_count == 4


# ------------------------------------------------- real ml_stress, cold vs warm baseline

def test_real_ml_cold_baseline_falls_back_to_heuristic():
    # The real agent.ml_stress is present in this tree. With a fresh
    # RollingBaseline (< min_windows windows) the z-scores are neutral, so
    # the pipeline must fall back to the heuristic, not emit a 0 score.
    assert ml_stress_available() is True
    h = _heuristic()
    enriched = enrich_strain_score(
        h, key_intervals=[0.2] * 40, mouse_intervals=[0.5] * 10,
        employee_id="cold-emp",
    )
    assert enriched.ml_features is not None  # 27 real features extracted
    assert len(enriched.ml_features) == 27
    assert enriched.ml_score is None  # cold baseline: no ML verdict yet
    assert enriched.score == h.score


def test_real_ml_warm_baseline_uses_ml_score():
    assert ml_stress_available() is True
    # Warm the rolling baseline with varied synthetic typing.
    for i in range(12):
        gaps = [0.15 + 0.05 * ((i + j) % 3) for j in range(40)]
        feats = extract_ml_features(gaps, [0.4 + 0.1 * ((i + j) % 2) for j in range(10)])
        ml_behavioral_score(feats, employee_id="warm-emp")
    h = _heuristic()
    enriched = enrich_strain_score(
        h, key_intervals=[0.2] * 40, mouse_intervals=[0.5] * 10,
        employee_id="warm-emp",
    )
    assert enriched.ml_score is not None
    assert 0.0 <= enriched.ml_score <= 100.0
    assert enriched.score == int(round(enriched.ml_score))
    # The heuristic result is still carried for break/backend semantics.
    assert enriched.heuristic.score == h.score


# ------------------------------------------------- ml_stress present

def test_ml_present_uses_ml_score():
    _install_fake_ml()
    assert ml_stress_available() is True

    h = _heuristic()
    enriched = enrich_strain_score(h, key_intervals=[0.2] * 30, mouse_intervals=[0.5] * 10)
    assert enriched.ml_features == {"k_mean": 0.25, "m_rate": 10.0, "m_clicks": 5.0}
    assert enriched.ml_score == pytest.approx(82.0)
    # No HR stream: fused score equals the behavioral (ml) score.
    assert enriched.score == 82
    assert enriched.level == "red"
    assert enriched.fusion_meta["mode"] == "behavioral_only"


def test_ml_extraction_error_falls_back_no_crash():
    _install_fake_ml(extract_error=True)
    h = _heuristic()
    enriched = enrich_strain_score(h, key_intervals=[0.2] * 30)
    assert enriched.ml_score is None
    assert enriched.ml_features is None
    assert enriched.score == h.score


def test_fatigue_wiring_end_to_end_session_shifts():
    stable = {"k_mean": 0.25, "m_rate": 10.0}
    shifted = {"k_mean": 0.60, "m_rate": 45.0}
    _install_fake_ml(feature_sequence=[stable] * 4 + [shifted])
    h = _heuristic()
    tracker = FatigueTracker(employee_id="w2")
    events = []
    for _ in range(5):
        e = enrich_strain_score(
            h, key_intervals=[0.2] * 20,
            session_ended=True, fatigue_tracker=tracker, employee_id="w2",
        )
        events.append(e.fatigue_event)
    assert events[:4] == [None, None, None, None]
    assert events[4] is not None
    assert events[4]["type"] == "fatigue_suspected"
    assert events[4]["employee_id"] == "w2"


# ------------------------------------------------- HR fusion wiring

class _FakeStream:
    def __init__(self, sample):
        self._sample = sample

    def latest_sample(self):
        return self._sample


def test_hr_fusion_wiring_with_stream():
    h = _heuristic()  # score 45
    stream = _FakeStream({"bpm": 130, "ts": time.time(), "resting_bpm": 70})
    enriched = enrich_strain_score(h, hr_stream=stream, resting_bpm=70.0)
    assert enriched.fusion_meta["mode"] == "early_fusion"
    assert enriched.fusion_meta["hr_missing"] is False
    # Elevated HR lifts the fused score above the heuristic 45.
    assert enriched.score > h.score


def test_stale_hr_masked():
    h = _heuristic()
    stream = _FakeStream({"bpm": 130, "ts": time.time() - 300.0, "resting_bpm": 70})
    enriched = enrich_strain_score(h, hr_stream=stream, resting_bpm=70.0)
    assert enriched.fusion_meta["mode"] == "behavioral_only"
    assert enriched.score == h.score


def test_real_stream_latest_sample_accessor():
    stream = HeartRateStream()
    assert stream.latest_sample() is None
    stream._handle_notification(0, bytes([0x00, 96]))
    sample = stream.latest_sample()
    assert sample["bpm"] == 96
    assert abs(sample["ts"] - time.time()) < 5.0
    # Duck-typed wiring accepts the real stream object.
    h = _heuristic()
    enriched = enrich_strain_score(h, hr_stream=stream, resting_bpm=70.0)
    assert enriched.fusion_meta["mode"] == "early_fusion"


def test_hr_stream_without_accessor_falls_back_to_latest_bpm():
    class _Legacy:
        latest_bpm = 110
    h = _heuristic()
    enriched = enrich_strain_score(h, hr_stream=_Legacy(), resting_bpm=70.0)
    assert enriched.fusion_meta["mode"] == "early_fusion"


# ------------------------------------------------- timing buffer

def test_timing_buffer_records_gaps_only():
    buf = AnonymizedTimingBuffer(capacity=3)
    for ts in (1000.0, 1001.5, 1003.0, 1003.5, 1006.0):
        buf.record_event(ts=ts)
    assert buf.snapshot() == pytest.approx([1.5, 0.5, 2.5])  # capacity bound
    drained = buf.drain()
    assert drained == pytest.approx([1.5, 0.5, 2.5])
    assert len(buf) == 0
    assert buf.snapshot() == []


def test_timing_buffer_record_interval():
    buf = AnonymizedTimingBuffer()
    buf.record_interval(0.2)
    buf.record_interval(-1.0)  # negative gaps are dropped
    assert buf.snapshot() == [0.2]


# ------------------------------------------------- existing behavior intact

def test_heuristic_scores_unchanged():
    assert compute_strain_score(0, 0, 0, False, StrainConfig()).score == 0
    assert compute_strain_score(200, 500, 50, True, StrainConfig()).score == 100
    assert compute_strain_score(60, 150, 0, False, StrainConfig()).score == 45
    assert score_level(39) == "green"
    assert score_level(40) == "amber"
    assert score_level(70) == "red"


def test_pipeline_off_by_default_is_identity():
    # enrich_strain_score with no optional inputs reproduces the heuristic.
    for score_args in [(0, 0, 0, False), (120, 0, 0, False), (60, 150, 8, True)]:
        h = compute_strain_score(*score_args, StrainConfig())
        e = enrich_strain_score(h)
        assert e.score == h.score
        assert e.level == h.level
        assert set(e.components) >= {"continuous_active", "input_intensity",
                                     "window_switching", "outside_work_hours"}
