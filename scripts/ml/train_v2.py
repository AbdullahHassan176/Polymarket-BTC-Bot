#!/usr/bin/env python3
"""
train_v2.py — Train LightGBM + XGBoost ensemble for 5-min BTC direction.

Key design decisions (per research):
  - Expanding window CV with 3-hour purge gap (prevents lookahead bias)
  - Simple average ensemble — beats stacking/meta-learners with weak signal
  - Confidence threshold: only useful when |P - 0.50| > threshold
  - Doji masking already done in backfill (moves <0.03% excluded)

Usage (from project root):
  python scripts/ml/train_v2.py --data logs/ml_training_v2.csv
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import argparse
import json

import numpy as np
import pandas as pd
import joblib

from ml.features_v2 import FEATURE_COLS


# ---------------------------------------------------------------------------
# EXPANDING WINDOW CROSS-VALIDATION
# ---------------------------------------------------------------------------

def expanding_window_splits(n: int, min_train: int = 500, val_size: int = 100,
                             step: int = 50, purge: int = 36):
    """
    Generate (train_idx, val_idx) splits for expanding window CV.
    purge: number of rows to skip between train end and val start (prevents leakage).
           At 5-min intervals, purge=36 = 3 hours gap.
    """
    splits = []
    start = min_train
    while start + purge + val_size <= n:
        train_end = start
        val_start = train_end + purge
        val_end   = val_start + val_size
        if val_end > n:
            break
        train_idx = list(range(0, train_end))
        val_idx   = list(range(val_start, val_end))
        splits.append((train_idx, val_idx))
        start += step
    return splits


def evaluate_confidence(y_true, y_prob, thresholds=(0.05, 0.10, 0.12, 0.15)):
    """Print accuracy at various confidence thresholds."""
    print(f"  Overall accuracy: {(y_prob.round() == y_true).mean():.3f} (n={len(y_true)})")
    for t in thresholds:
        mask = np.abs(y_prob - 0.5) >= t
        if mask.sum() < 10:
            continue
        acc = (y_prob[mask].round() == y_true[mask]).mean()
        print(f"  Confidence >{t:.2f}: accuracy={acc:.3f}, n={mask.sum()} ({mask.mean():.1%} of trades)")


# ---------------------------------------------------------------------------
# MAIN TRAINING
# ---------------------------------------------------------------------------

def train(data_csv: str = "logs/ml_training_v2.csv",
          model_dir: str = None,
          min_samples: int = 200) -> dict:
    """
    Train LightGBM + XGBoost ensemble.
    Returns dict with model paths and validation metrics.
    """
    if model_dir is None:
        model_dir = str(Path(__file__).parent)

    print(f"Loading training data: {data_csv}")
    df = pd.read_csv(data_csv)
    print(f"  Rows: {len(df)}, Label balance: {df['label_up'].mean():.2%} UP")

    if len(df) < min_samples:
        print(f"ERROR: Need at least {min_samples} samples, got {len(df)}. Run backfill_v2.py first.")
        return {}

    # Build feature matrix — handle missing columns with appropriate neutral values
    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        print(f"  Warning: {len(missing)} features missing, filling with neutral defaults: {missing[:5]}...")
    for col in FEATURE_COLS:
        if col not in df.columns:
            if "taker_buy_ratio" in col:
                df[col] = 0.5   # neutral: equal buy/sell pressure
            else:
                df[col] = 0.0

    # Fill any remaining NaNs with appropriate neutral values per column
    for col in FEATURE_COLS:
        if df[col].isna().any():
            neutral = 0.5 if "taker_buy_ratio" in col else 0.0
            df[col] = df[col].fillna(neutral)

    X = df[FEATURE_COLS].values.astype(np.float32)
    y = df["label_up"].values.astype(np.int32)

    print(f"  Feature matrix: {X.shape}")

    # --- Expanding window CV ---
    splits = expanding_window_splits(len(X), min_train=min(500, len(X) // 3),
                                     val_size=min(100, len(X) // 10),
                                     step=min(50, len(X) // 20), purge=36)
    print(f"  Expanding window CV: {len(splits)} folds (3h purge gap)")

    # --- LightGBM ---
    import lightgbm as lgb

    lgb_params = {
        "objective": "binary",
        "metric": "auc",
        "n_estimators": 800,
        "learning_rate": 0.005,
        "num_leaves": 31,       # was 63 — fewer leaves = less overfit
        "max_depth": 5,         # was 8 — shallower trees generalize better
        "min_child_samples": 50,  # was 20 — require more samples per leaf
        "feature_fraction": 0.7,  # was 0.8
        "bagging_fraction": 0.7,  # was 0.8
        "bagging_freq": 3,
        "lambda_l1": 1.0,       # was 0.1 — stronger L1
        "lambda_l2": 1.0,       # was 0.1 — stronger L2
        "verbose": -1,
        "random_state": 42,
    }

    # --- XGBoost ---
    import xgboost as xgb

    xgb_params = {
        "n_estimators": 800,
        "learning_rate": 0.005,
        "max_depth": 4,         # was 7 — much shallower
        "min_child_weight": 20, # was unset — require more samples per split
        "subsample": 0.7,       # was 0.8
        "colsample_bytree": 0.7,  # was 0.8
        "reg_alpha": 2.0,       # was 0.1 — strong L1
        "reg_lambda": 2.0,      # was 0.1 — strong L2
        "eval_metric": "auc",
        "use_label_encoder": False,
        "verbosity": 0,
        "random_state": 42,
    }

    lgb_val_probs = np.zeros(len(X))
    xgb_val_probs = np.zeros(len(X))
    val_counts    = np.zeros(len(X))

    for fold_i, (train_idx, val_idx) in enumerate(splits):
        X_tr, y_tr = X[train_idx], y[train_idx]
        X_va, y_va = X[val_idx], y[val_idx]

        # LightGBM fold
        lgb_model = lgb.LGBMClassifier(**lgb_params)
        lgb_model.fit(X_tr, y_tr,
                      eval_set=[(X_va, y_va)],
                      callbacks=[lgb.early_stopping(30, verbose=False),
                                 lgb.log_evaluation(period=-1)])
        lgb_val_probs[val_idx] += lgb_model.predict_proba(X_va)[:, 1]

        # XGBoost fold
        xgb_model = xgb.XGBClassifier(**xgb_params)
        xgb_model.fit(X_tr, y_tr,
                      eval_set=[(X_va, y_va)],
                      verbose=False)
        xgb_val_probs[val_idx] += xgb_model.predict_proba(X_va)[:, 1]

        val_counts[val_idx] += 1

        if (fold_i + 1) % 5 == 0 or fold_i == 0:
            print(f"  Fold {fold_i + 1}/{len(splits)} done")

    # Average over folds where val_counts > 0
    mask_val = val_counts > 0
    lgb_val_avg = np.where(mask_val, lgb_val_probs / np.maximum(val_counts, 1), 0.5)
    xgb_val_avg = np.where(mask_val, xgb_val_probs / np.maximum(val_counts, 1), 0.5)
    ens_val     = (lgb_val_avg + xgb_val_avg) / 2

    print(f"\n  CV Results (rows seen in validation: {mask_val.sum()}):")
    print("  [LightGBM]")
    evaluate_confidence(y[mask_val], lgb_val_avg[mask_val])
    print("  [XGBoost]")
    evaluate_confidence(y[mask_val], xgb_val_avg[mask_val])
    print("  [Ensemble Average]")
    evaluate_confidence(y[mask_val], ens_val[mask_val])

    # --- Final models trained on ALL data ---
    print("\nTraining final models on full dataset...")

    lgb_final = lgb.LGBMClassifier(**lgb_params)
    lgb_final.fit(X, y, callbacks=[lgb.log_evaluation(period=-1)])

    xgb_final = xgb.XGBClassifier(**xgb_params)
    xgb_final.fit(X, y, verbose=False)

    # --- Save ---
    lgb_path = Path(model_dir) / "lgb_v2.pkl"
    xgb_path = Path(model_dir) / "xgb_v2.pkl"
    joblib.dump(lgb_final, lgb_path)
    joblib.dump(xgb_final, xgb_path)
    print(f"  Saved: {lgb_path}")
    print(f"  Saved: {xgb_path}")

    # Save feature list for runtime validation
    meta = {
        "feature_cols": FEATURE_COLS,
        "n_train": int(len(X)),
        "label_balance": float(y.mean()),
    }
    meta_path = Path(model_dir) / "model_v2_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  Saved: {meta_path}")

    # Feature importance
    print("\n  Top 10 features (LightGBM):")
    importances = lgb_final.feature_importances_
    feat_imp = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)
    for name, imp in feat_imp[:10]:
        print(f"    {name}: {imp}")

    return {
        "lgb_path": str(lgb_path),
        "xgb_path": str(xgb_path),
        "n_train": len(X),
        "cv_folds": len(splits),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data",      default="logs/ml_training_v2.csv", help="Training data CSV")
    parser.add_argument("--model-dir", default=None,                       help="Directory to save models")
    parser.add_argument("--min-samples", type=int, default=200,            help="Minimum rows required")
    args = parser.parse_args()

    import os
    os.chdir(ROOT)
    train(data_csv=args.data, model_dir=args.model_dir, min_samples=args.min_samples)


if __name__ == "__main__":
    main()
