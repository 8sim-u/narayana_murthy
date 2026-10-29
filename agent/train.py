"""
Train a supervised stress classifier on labeled feature CSVs.

Usage:
    python -m agent.train --csv labeled.csv --out models/stress_rf.joblib

The CSV must have one row per 60-second window, columns = feature names
produced by agent.ml_stress.extract_keystroke_features /
extract_mouse_features, plus a binary label column named `stress`
(1 = stressed window, 0 = calm window).

The script prints stratified k-fold cross-validation accuracy, refits on
the full data, and dumps a joblib artifact:
    {"model": RandomForestClassifier, "feature_names": [...], "meta": {...}}
loadable by agent.ml_stress.SupervisedStressModel.

Privacy note: training data must contain timing aggregates only, never key
identities or key content. See agent/ml_stress.py.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

LABEL_COLUMN = "stress"


def load_labeled_csv(path: str) -> tuple[list[str], list[list[float]], list[int]]:
    """Read a labeled CSV; returns (feature_names, X, y)."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: empty CSV, no header row")
        if LABEL_COLUMN not in reader.fieldnames:
            raise ValueError(
                f"{path}: missing label column '{LABEL_COLUMN}'; "
                f"columns found: {reader.fieldnames}"
            )
        feature_names = [c for c in reader.fieldnames if c != LABEL_COLUMN]
        if not feature_names:
            raise ValueError(f"{path}: no feature columns found")
        X: list[list[float]] = []
        y: list[int] = []
        for i, row in enumerate(reader, start=2):
            try:
                X.append([float(row[c]) for c in feature_names])
                y.append(int(float(row[LABEL_COLUMN])))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path} line {i}: bad value ({exc})") from exc
    if not X:
        raise ValueError(f"{path}: no data rows")
    labels = sorted(set(y))
    if len(labels) < 2:
        raise ValueError(
            f"{path}: need both classes 0 and 1 in '{LABEL_COLUMN}', "
            f"found only {labels}"
        )
    return feature_names, X, y


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Train a RandomForest stress classifier on labeled "
                    "ml_stress feature CSVs."
    )
    ap.add_argument("--csv", required=True, help="labeled CSV input path")
    ap.add_argument("--out", required=True, help="joblib output path")
    ap.add_argument("--test-size", type=float, default=0.0,
                    help="reserved for future holdout split; currently "
                         "cross-validation is used (default 0.0)")
    ap.add_argument("--cv-folds", type=int, default=5,
                    help="stratified CV folds (default 5)")
    ap.add_argument("--n-estimators", type=int, default=300,
                    help="RandomForest trees (default 300)")
    ap.add_argument("--seed", type=int, default=42,
                    help="random seed (default 42)")
    args = ap.parse_args(argv)

    try:
        import joblib
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import StratifiedKFold, cross_val_score
    except ImportError:
        print(
            "error: scikit-learn and joblib are required "
            "(pip install -r agent/requirements.txt)",
            file=sys.stderr,
        )
        return 2

    feature_names, X_list, y_list = load_labeled_csv(args.csv)
    X = np.asarray(X_list, dtype=float)
    y = np.asarray(y_list, dtype=int)
    n_pos = int((y == 1).sum())
    print(f"samples: {len(y)}  features: {len(feature_names)}  "
          f"stressed: {n_pos}  calm: {len(y) - n_pos}")

    folds = max(2, min(args.cv_folds, n_pos, len(y) - n_pos))
    clf = RandomForestClassifier(
        n_estimators=args.n_estimators,
        random_state=args.seed,
        n_jobs=-1,
        class_weight="balanced",
    )
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=args.seed)
    scores = cross_val_score(clf, X, y, cv=cv, scoring="accuracy")
    print(f"cv accuracy ({folds}-fold stratified): "
          f"{scores.mean():.3f} +/- {scores.std():.3f}")
    print(f"fold scores: {', '.join(f'{s:.3f}' for s in scores)}")

    clf.fit(X, y)
    payload = {
        "model": clf,
        "feature_names": feature_names,
        "meta": {
            "n_samples": len(y),
            "n_stressed": n_pos,
            "cv_accuracy_mean": float(scores.mean()),
            "cv_accuracy_std": float(scores.std()),
            "cv_folds": folds,
            "seed": args.seed,
            "n_estimators": args.n_estimators,
            "source_csv": os.path.basename(args.csv),
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    joblib.dump(payload, args.out)
    print(f"model saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
