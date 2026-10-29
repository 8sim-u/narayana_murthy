"""
Session-to-session fatigue detection for the WAMSS workstation agent.

Implements the literature pattern from Acien et al. 2022: each completed
work session is summarized as a behavioral feature vector, and the current
session is compared against the employee's recent session history. The
comparison uses k-NN distance (k=3, euclidean on z-normalized features).
When that distance is an outlier relative to the history of past distances
(robust z-score above 2.5, requiring at least 4 prior sessions), a
``fatigue_suspected`` event is returned.

This is a heuristic workload signal. It is NOT a medical measure of
fatigue, stress, or any health condition.

Privacy: only numeric feature aggregates are kept, in process memory.
No key content, no key identities, no text, no coordinates are stored.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Dict, List, Optional

logger = logging.getLogger("wamss.agent.fatigue")


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def robust_z(x: float, history: List[float]) -> float:
    """
    Robust z-score of x against a history, using median and MAD
    (median absolute deviation), scaled by 0.6745 to match a normal sigma.

    Returns +inf when the history has zero spread and x exceeds its median
    (a genuine shift), and 0.0 when x equals the median.
    """
    if not history:
        return 0.0
    med = _median(history)
    mad = _median([abs(v - med) for v in history])
    if mad == 0:
        return float("inf") if x > med else 0.0
    return 0.6745 * (x - med) / mad


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class FatigueTracker:
    """
    Per-employee session-to-session fatigue detector.

    Keeps the most recent ``max_sessions`` session feature vectors in memory
    (default 10) plus the history of k-NN distances. Call ``add_session``
    once per completed work session with that session's behavioral feature
    dict (the shape produced by ``agent.ml_stress.extract_*``; plain dicts
    of numbers also work, which keeps this testable with synthetic input).

    Returns a ``fatigue_suspected`` event dict when the session is an
    outlier, else None. Every session is stored regardless of the verdict.
    """

    def __init__(
        self,
        employee_id: Optional[str] = None,
        max_sessions: int = 10,
        k: int = 3,
        z_threshold: float = 2.5,
    ) -> None:
        self.employee_id = employee_id
        self.max_sessions = max(2, int(max_sessions))
        self.k = max(1, int(k))
        self.z_threshold = float(z_threshold)
        self._feature_keys: Optional[List[str]] = None
        self._history: Deque[List[float]] = deque(maxlen=self.max_sessions)
        self._distances: Deque[float] = deque(maxlen=self.max_sessions)

    # ------------------------------------------------------------ internals

    def _vectorize(self, feature_dict: Dict) -> Optional[List[float]]:
        """Align a feature dict to the tracker's fixed key order."""
        if not isinstance(feature_dict, dict):
            return None
        if self._feature_keys is None:
            keys = sorted(k for k in feature_dict.keys()
                          if isinstance(k, str) and _is_number(feature_dict[k]))
            if not keys:
                return None
            self._feature_keys = keys
        vec = []
        for key in self._feature_keys:
            value = feature_dict.get(key, 0.0)
            vec.append(float(value) if _is_number(value) else 0.0)
        return vec

    @staticmethod
    def _z_normalize(vec: List[float], history: List[List[float]]) -> List[float]:
        """Z-normalize vec using per-feature mean/std of the history."""
        if not history:
            return list(vec)
        n_features = len(vec)
        out = []
        for i in range(n_features):
            col = [h[i] for h in history if i < len(h)]
            if not col:
                out.append(vec[i])
                continue
            mean = sum(col) / len(col)
            var = sum((x - mean) ** 2 for x in col) / len(col)
            std = var ** 0.5
            out.append((vec[i] - mean) / std if std > 0 else vec[i] - mean)
        return out

    def _knn_distance(self, vec: List[float]) -> float:
        """Mean distance to the k nearest history vectors (z-normalized)."""
        history = list(self._history)
        znormed_history = [self._z_normalize(h, history) for h in history]
        zvec = self._z_normalize(vec, history)

        def _dist(a: List[float], b: List[float]) -> float:
            n = min(len(a), len(b))
            return sum((a[i] - b[i]) ** 2 for i in range(n)) ** 0.5

        dists = sorted(_dist(zvec, h) for h in znormed_history)
        k = min(self.k, len(dists))
        return sum(dists[:k]) / k if k else 0.0

    # ------------------------------------------------------------------ API

    def add_session(self, feature_dict: Dict) -> Optional[Dict]:
        """
        Record one completed session and check it for suspected fatigue.

        Returns {"type": "fatigue_suspected", "employee_id": ...,
        "severity": "moderate"|"high", "detail": {...}} when the session's
        k-NN distance is an outlier (robust z > threshold, needs at least
        4 prior sessions); otherwise None.
        """
        vec = self._vectorize(feature_dict)
        if vec is None:
            logger.debug("fatigue: ignoring session with no numeric features")
            return None

        event: Optional[Dict] = None
        n_prior = len(self._history)
        if n_prior >= 1:
            distance = self._knn_distance(vec)
            self._distances.append(distance)
            if n_prior >= 4:
                z = robust_z(distance, list(self._distances))
                if z > self.z_threshold:
                    severity = "high" if z >= 4.0 else "moderate"
                    event = {
                        "type": "fatigue_suspected",
                        "employee_id": self.employee_id,
                        "severity": severity,
                        "detail": {
                            "robust_z": round(z, 3) if z != float("inf") else None,
                            "robust_z_infinite": z == float("inf"),
                            "knn_distance": round(distance, 4),
                            "k": min(self.k, n_prior),
                            "sessions_compared": n_prior,
                            "z_threshold": self.z_threshold,
                            "note": ("heuristic workload signal only; "
                                     "not a medical assessment"),
                        },
                    }
                    logger.info("fatigue_suspected for employee=%s severity=%s "
                                "robust_z=%s", self.employee_id, severity,
                                "inf" if z == float("inf") else round(z, 3))
        self._history.append(vec)
        return event

    @property
    def session_count(self) -> int:
        return len(self._history)

    def reset(self) -> None:
        """Clear history (used by tests)."""
        self._history.clear()
        self._distances.clear()
        self._feature_keys = None
