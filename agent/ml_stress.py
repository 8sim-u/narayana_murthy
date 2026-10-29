"""
ML stress dynamics engine for the WAMSS workstation agent (round 4).

Timing-only keystroke and mouse dynamics features for workload strain
estimation. Privacy rule is absolute: this module only ever sees anonymized
timestamps (press/release pairs and move/click/scroll events with
coordinates). Key identities, key content, and screen content are NEVER
accepted, stored, or derivable here. Backspace/delete correction activity is
accepted only as aggregate counts supplied by the logger, never as
per-event key identities.

Literature basis for feature selection and weight emphasis:
  Pepa et al. 2021, Nagelin et al. 2023, Hibbeln et al. 2017,
  Banholzer et al. 2021, Koldijk et al. 2018.

Pipeline:
  1. Slice raw events into 60-second non-overlapping windows
     (see slice_pairs / slice_events).
  2. extract_keystroke_features / extract_mouse_features per window.
     Windows with no events are zero-filled.
  3. RollingBaseline: per-user z-score normalization against a rolling
     personal baseline (one instance per user, kept by the integrator).
  4. unsupervised_strain_score: weighted 0-100 heuristic score, or
     SupervisedStressModel: joblib RandomForest trained by agent/train.py.

Integrator note: the current activity_agent InputCounter only emits
keystroke/click counts. To use this module, the logger must be extended to
emit anonymized (press_ts, release_ts) pairs and mouse events shaped as
{"ts": float, "x": float, "y": float, "kind": str} with kind in
{"move", "click_down", "click_up", "scroll"}. No key identities or content
may be logged, here or upstream.

This is a workload-tension heuristic, NOT a medical measure of stress,
fatigue, or any health condition.
"""

from __future__ import annotations

import math

# 60-second non-overlapping analysis windows, per the literature baseline.
WINDOW_SECONDS = 60.0

# A gap between key release and next key press longer than this counts as a
# pause. Many short pauses are a stress signal (Banholzer 2021, Koldijk 2018).
PAUSE_THRESHOLD_S = 1.0

# Z magnitudes are clipped here before mapping to 0-100.
_Z_CLIP = 4.0

# Minimum valid dwell/flight durations; guards against clock artifacts.
_MIN_POSITIVE = 1e-6


# ---------------------------------------------------------------------------
# Small statistics helpers (stdlib only; keeps the extractor dependency-free).
# ---------------------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    """Population standard deviation; 0.0 for empty or single-element input."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / n
    return math.sqrt(var) if var > 0.0 else 0.0


def _finite(x: float) -> float:
    return x if math.isfinite(x) else 0.0


# ---------------------------------------------------------------------------
# Windowing helpers.
# ---------------------------------------------------------------------------

def slice_pairs(
    pairs: list[tuple[float, float]], start: float, end: float
) -> list[tuple[float, float]]:
    """Return (press_ts, release_ts) pairs whose press falls in [start, end)."""
    return [(p, r) for p, r in pairs if start <= p < end]


def slice_events(events: list[dict], start: float, end: float) -> list[dict]:
    """Return mouse events whose ts falls in [start, end)."""
    return [e for e in events if start <= float(e.get("ts", 0.0)) < end]


# ---------------------------------------------------------------------------
# Keystroke features.
# ---------------------------------------------------------------------------

def extract_keystroke_features(
    pairs: list[tuple[float, float]],
    correction_counts: dict | None = None,
) -> dict:
    """
    Timing features from anonymized press/release pairs.

    pairs: list of (press_ts, release_ts) in seconds. NO key identities and
    NO key content may be present; timing only.
    correction_counts: optional {"backspace": int, "delete": int} aggregate
    counts supplied by the keyboard logger. Counts only, never identities.

    Returns a dict of 15 float features (CSV-ready column order is stable).
    An empty window yields all zeros.
    """
    feats = {
        "kb_n_keys": 0.0,
        "kb_dwell_min": 0.0,
        "kb_dwell_mean": 0.0,
        "kb_dwell_std": 0.0,
        "kb_flight_min": 0.0,
        "kb_flight_std": 0.0,
        "kb_digraph_mean": 0.0,
        "kb_digraph_std": 0.0,
        "kb_release_press_latency_mean": 0.0,
        "kb_velocity": 0.0,
        "kb_backspace_count": 0.0,
        "kb_delete_count": 0.0,
        "kb_pause_count": 0.0,
        "kb_pause_mean": 0.0,
        "kb_duty_cycle": 0.0,
    }
    if not pairs:
        return feats

    ordered = sorted(pairs, key=lambda pr: pr[0])
    n = len(ordered)
    feats["kb_n_keys"] = float(n)

    # Dwell: how long each key is held (press -> release).
    dwells = [max(0.0, r - p) for p, r in ordered]
    feats["kb_dwell_min"] = _finite(min(dwells)) if dwells else 0.0
    feats["kb_dwell_mean"] = _finite(_mean(dwells))
    feats["kb_dwell_std"] = _finite(_std(dwells))

    presses = [p for p, _ in ordered]
    releases = [r for _, r in ordered]

    # Flight: down-to-down time between consecutive presses.
    flights = [b - a for a, b in zip(presses, presses[1:]) if b - a >= 0.0]
    feats["kb_flight_min"] = _finite(min(flights)) if flights else 0.0
    feats["kb_flight_std"] = _finite(_std(flights))

    # Digraph inter-key time: press-to-press gaps inside active typing
    # (pauses excluded), i.e. the rhythm of continuous typing bursts.
    digraphs = [g for g in flights if g < PAUSE_THRESHOLD_S]
    feats["kb_digraph_mean"] = _finite(_mean(digraphs))
    feats["kb_digraph_std"] = _finite(_std(digraphs))

    # Release-to-press latency: release[i] -> press[i+1].
    latencies = [b - a for a, b in zip(releases, presses[1:]) if b - a >= 0.0]
    feats["kb_release_press_latency_mean"] = _finite(_mean(latencies))

    # Typing velocity in keys per second of the analysis window.
    feats["kb_velocity"] = _finite(n / WINDOW_SECONDS)

    # Correction counts (aggregates only; see module docstring).
    if correction_counts:
        feats["kb_backspace_count"] = float(max(0, int(correction_counts.get("backspace", 0))))
        feats["kb_delete_count"] = float(max(0, int(correction_counts.get("delete", 0))))

    # Pause structure: gaps longer than PAUSE_THRESHOLD_S. Many short
    # pauses are a stress signal in the literature.
    pauses = [g for g in latencies if g > PAUSE_THRESHOLD_S]
    feats["kb_pause_count"] = float(len(pauses))
    feats["kb_pause_mean"] = _finite(_mean(pauses))

    # Key-press duty cycle: fraction of the window with a key held down.
    feats["kb_duty_cycle"] = _finite(min(1.0, sum(dwells) / WINDOW_SECONDS))

    return {k: _finite(float(v)) for k, v in feats.items()}


# ---------------------------------------------------------------------------
# Mouse features.
# ---------------------------------------------------------------------------

_MOUSE_KINDS = {"move", "click_down", "click_up", "scroll"}


def _perp_distance(ax, ay, bx, by, cx, cy) -> float:
    """Perpendicular distance of point B from the line through A and C."""
    dx, dy = cx - ax, cy - ay
    base = math.hypot(dx, dy)
    if base < _MIN_POSITIVE:
        return math.hypot(bx - ax, by - ay)
    return abs(dx * (ay - by) - dy * (ax - bx)) / base


def extract_mouse_features(events: list[dict]) -> dict:
    """
    Dynamics features from anonymized mouse events.

    events: list of {"ts": float, "x": float, "y": float, "kind": str} with
    kind in {"move", "click_down", "click_up", "scroll"}. Coordinates are
    relative screen positions used only for path geometry; no content is
    captured or derivable.

    Returns a dict of 12 float features. An empty window yields all zeros.
    """
    feats = {
        "ms_move_count": 0.0,
        "ms_click_count": 0.0,
        "ms_scroll_count": 0.0,
        "ms_velocity_mean": 0.0,
        "ms_velocity_std": 0.0,
        "ms_acceleration_mean": 0.0,
        "ms_deviation_mean": 0.0,
        "ms_direction_changes": 0.0,
        "ms_click_dwell_mean": 0.0,
        "ms_interclick_distance_mean": 0.0,
        "ms_interclick_time_mean": 0.0,
        "ms_speed_accuracy_interaction": 0.0,
    }
    known = [e for e in events if e.get("kind") in _MOUSE_KINDS]
    if not known:
        return feats
    known.sort(key=lambda e: float(e.get("ts", 0.0)))

    moves = [(float(e["ts"]), float(e.get("x", 0.0)), float(e.get("y", 0.0)))
             for e in known if e["kind"] == "move"]
    feats["ms_move_count"] = float(len(moves))
    feats["ms_click_count"] = float(sum(1 for e in known if e["kind"] == "click_down"))
    feats["ms_scroll_count"] = float(sum(1 for e in known if e["kind"] == "scroll"))

    # Segment velocities and accelerations between consecutive moves.
    seg_v: list[float] = []
    seg_dt: list[float] = []
    seg_vec: list[tuple[float, float]] = []
    for (t1, x1, y1), (t2, x2, y2) in zip(moves, moves[1:]):
        dt = t2 - t1
        if dt <= 0.0:
            continue
        dist = math.hypot(x2 - x1, y2 - y1)
        seg_v.append(dist / dt)
        seg_dt.append(dt)
        seg_vec.append((x2 - x1, y2 - y1))
    feats["ms_velocity_mean"] = _finite(_mean(seg_v))
    feats["ms_velocity_std"] = _finite(_std(seg_v))
    accels = [(b - a) / dt for a, b, dt in zip(seg_v, seg_v[1:], seg_dt[1:])]
    feats["ms_acceleration_mean"] = _finite(_mean(accels))

    # Deviation from the straight-line path (path jaggedness, Hibbeln 2017):
    # mean perpendicular distance of each middle point from the chord
    # through its neighbors.
    devs = [
        _perp_distance(ax, ay, bx, by, cx, cy)
        for (ax, ay), (bx, by), (cx, cy)
        in zip([(x, y) for _, x, y in moves],
               [(x, y) for _, x, y in moves[1:]],
               [(x, y) for _, x, y in moves[2:]])
    ]
    feats["ms_deviation_mean"] = _finite(_mean(devs))

    # Direction-change count: sign flips of the z cross product between
    # consecutive movement segments.
    changes = 0
    last_sign = 0
    for (dx1, dy1), (dx2, dy2) in zip(seg_vec, seg_vec[1:]):
        cross = dx1 * dy2 - dy1 * dx2
        sign = 1 if cross > 0 else (-1 if cross < 0 else 0)
        if sign != 0:
            if last_sign != 0 and sign != last_sign:
                changes += 1
            last_sign = sign
    feats["ms_direction_changes"] = float(changes)

    # Click dwell: click_down -> click_up pairing in event order.
    pending: list[float] = []
    dwells: list[float] = []
    click_pts: list[tuple[float, float, float]] = []
    for e in known:
        t = float(e["ts"])
        if e["kind"] == "click_down":
            pending.append(t)
            click_pts.append((t, float(e.get("x", 0.0)), float(e.get("y", 0.0))))
        elif e["kind"] == "click_up" and pending:
            dwells.append(max(0.0, t - pending.pop(0)))
    feats["ms_click_dwell_mean"] = _finite(_mean(dwells))

    # Inter-click distance and time between consecutive click presses.
    ic_dist = [
        math.hypot(x2 - x1, y2 - y1)
        for (_, x1, y1), (_, x2, y2) in zip(click_pts, click_pts[1:])
    ]
    ic_time = [b - a for (a, _, _), (b, _, _) in zip(click_pts, click_pts[1:])
               if b - a >= 0.0]
    feats["ms_interclick_distance_mean"] = _finite(_mean(ic_dist))
    feats["ms_interclick_time_mean"] = _finite(_mean(ic_time))

    # Speed x accuracy interaction: fast movement combined with jagged
    # paths marks degraded motor control under strain.
    feats["ms_speed_accuracy_interaction"] = _finite(
        feats["ms_velocity_mean"] * feats["ms_deviation_mean"]
    )

    return {k: _finite(float(v)) for k, v in feats.items()}


# ---------------------------------------------------------------------------
# Rolling per-user baseline for z-score normalization.
# ---------------------------------------------------------------------------

class RollingBaseline:
    """
    Rolling personal baseline: Welford running mean/variance per feature.

    Keep one instance per user (the integrator owns the mapping). Call
    update() with each new window's feature dict, then zscore() to
    normalize. Features with fewer than min_windows observations, or with
    zero variance, yield z = 0.0 so cold-start users get neutral scores.
    """

    def __init__(self, min_windows: int = 10):
        self.min_windows = max(2, int(min_windows))
        self._stats: dict[str, list[float]] = {}  # name -> [count, mean, M2]

    def update(self, f: dict) -> None:
        """Fold one window's feature dict into the running baseline."""
        for name, raw in f.items():
            v = float(raw)
            if not math.isfinite(v):
                continue
            st = self._stats.get(name)
            if st is None:
                self._stats[name] = [1.0, v, 0.0]
                continue
            count, mean, m2 = st
            count += 1.0
            delta = v - mean
            mean += delta / count
            m2 += delta * (v - mean)
            st[0], st[1], st[2] = count, mean, m2

    def zscore(self, f: dict) -> dict:
        """Per-feature z-scores against the baseline; unknown or cold
        features map to 0.0."""
        out: dict[str, float] = {}
        for name, raw in f.items():
            v = float(raw)
            st = self._stats.get(name)
            if st is None or st[0] < self.min_windows or not math.isfinite(v):
                out[name] = 0.0
                continue
            count, mean, m2 = st
            var = m2 / (count - 1.0) if count > 1.0 else 0.0
            std = math.sqrt(var) if var > 0.0 else 0.0
            out[name] = (v - mean) / std if std > 1e-9 else 0.0
        return out

    @property
    def window_count(self) -> int:
        """Number of windows folded in (max over features)."""
        return int(max((st[0] for st in self._stats.values()), default=0))


# ---------------------------------------------------------------------------
# Unsupervised strain score.
# ---------------------------------------------------------------------------

# (feature, weight, mode): "abs" counts deviation in either direction,
# "pos" counts only elevated values. Weights sum to 1.0 and follow the
# literature emphasis: dwell-time variability, flight-time variability,
# typing-velocity deviation from baseline, mouse path jaggedness, and
# pause-structure shift.
_STRAIN_COMPONENTS = (
    ("kb_dwell_std", 0.22, "abs"),
    ("kb_flight_std", 0.20, "abs"),
    ("kb_velocity", 0.18, "abs"),
    ("ms_deviation_mean", 0.12, "abs"),
    ("ms_direction_changes", 0.08, "abs"),
    ("kb_pause_count", 0.10, "pos"),
    ("kb_digraph_std", 0.05, "abs"),
    ("ms_speed_accuracy_interaction", 0.05, "abs"),
)


def unsupervised_strain_score(z: dict) -> float:
    """
    Weighted 0-100 strain score from per-user z-scored features.

    score = 100 * weighted_mean(|z| clipped at 4). Missing features
    contribute 0. Heuristic only, not a medical measure.
    """
    total = 0.0
    for name, weight, mode in _STRAIN_COMPONENTS:
        v = float(z.get(name, 0.0))
        if not math.isfinite(v):
            v = 0.0
        mag = max(v, 0.0) if mode == "pos" else abs(v)
        total += weight * min(mag, _Z_CLIP)
    return max(0.0, min(100.0, 100.0 * total / _Z_CLIP))


# ---------------------------------------------------------------------------
# Supervised model (joblib artifact trained by agent/train.py).
# ---------------------------------------------------------------------------

class SupervisedStressModel:
    """
    Wrapper around a joblib artifact produced by agent/train.py.

    The artifact is a dict {"model": estimator, "feature_names": [...]}.
    A bare estimator is also accepted; features then fall back to sorted
    dict keys. predict() returns a 0-100 strain score: the probability of
    the positive (stress) class for classifiers, the raw prediction
    (clipped) for regressors.
    """

    def __init__(self, path: str | None = None):
        self.model = None
        self.feature_names: list[str] | None = None
        if path is not None:
            self.load(path)

    def load(self, path: str) -> "SupervisedStressModel":
        """Load a joblib artifact; returns self for chaining."""
        from joblib import load as joblib_load

        payload = joblib_load(path)
        if isinstance(payload, dict) and "model" in payload:
            self.model = payload["model"]
            self.feature_names = payload.get("feature_names")
        else:
            self.model = payload
            self.feature_names = list(
                getattr(payload, "feature_names_in_", None) or []
            ) or None
        return self

    def predict(self, f: dict) -> float:
        """Return the 0-100 strain score for one window's feature dict."""
        if self.model is None:
            raise RuntimeError("No model loaded; call load(path) first.")
        names = (
            list(self.feature_names)
            if self.feature_names
            else sorted(f.keys())
        )
        row = [float(f.get(n, 0.0)) for n in names]
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba([row])[0]
            classes = list(self.model.classes_)
            pos = classes.index(1) if 1 in classes else len(classes) - 1
            return max(0.0, min(100.0, float(proba[pos]) * 100.0))
        return max(0.0, min(100.0, float(self.model.predict([row])[0])))
