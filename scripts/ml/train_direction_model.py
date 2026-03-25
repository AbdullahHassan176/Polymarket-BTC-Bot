#!/usr/bin/env python3
"""
Train the ML direction model from collected training data.

Usage (from project root):
  python scripts/ml/train_direction_model.py
  python scripts/ml/train_direction_model.py --entries logs/trade_entries_24h.csv --trades logs/trades_24h.csv

Requires: pip install scikit-learn joblib
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import argparse
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entries", default="logs/trade_entries_12h_profit.csv", help="Trade entries CSV")
    parser.add_argument("--trades", default="logs/trades_12h_profit.csv", help="Trades CSV (OPEN+CLOSE)")
    parser.add_argument("--data", default="logs/ml_training_data.csv", help="Output training data CSV")
    parser.add_argument("--backfill", default="", help="Merge with backfill CSV (e.g. logs/ml_training_backfill.csv)")
    parser.add_argument("--min-samples", type=int, default=50, help="Minimum samples to train")
    args = parser.parse_args()

    from ml.direction_model import build_training_data

    df = build_training_data(args.entries, args.trades, args.data)

    # Merge with backfill for more training data
    if args.backfill and Path(args.backfill).exists():
        bf = pd.read_csv(args.backfill)
        if "eth_close_pct_change" not in bf.columns:
            bf["eth_close_pct_change"] = 0.0
        want = ["ema_fast", "ema_slow", "atr_pct", "ibs", "secs_remaining", "close_pct_change", "eth_close_pct_change", "label_up"]
        if not df.empty:
            if "eth_close_pct_change" not in df.columns:
                df["eth_close_pct_change"] = 0.0
            common = [c for c in want if c in df.columns and c in bf.columns]
            df = pd.concat([df[common], bf[common]], ignore_index=True)
        else:
            df = bf[[c for c in want if c in bf.columns]].copy()
        print(f"Merged backfill. Total: {len(df)}")

    if df.empty:
        print("No training data. Run paper trades to collect entries + outcomes.")
        return

    n = len(df)
    print(f"Training data: {n} samples -> {args.data}")

    if n < args.min_samples:
        print(f"Need at least {args.min_samples} samples. Collect more data.")
        return

    try:
        import joblib
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler

        feat_cols = ["ema_fast", "ema_slow", "atr_pct", "ibs", "secs_remaining", "close_pct_change"]
        if "eth_close_pct_change" in df.columns:
            feat_cols.append("eth_close_pct_change")
        X = df[feat_cols].fillna(0)
        y = df["label_up"]

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        clf = RandomForestClassifier(n_estimators=50, max_depth=5, random_state=42)
        scores = cross_val_score(clf, X_scaled, y, cv=3)
        print(f"Cross-val accuracy: {scores.mean():.2%} (+/- {scores.std():.2%})")

        clf.fit(X_scaled, y)

        out_dir = Path(__file__).parent
        joblib.dump(clf, out_dir / "direction_model.pkl")
        joblib.dump(scaler, out_dir / "direction_model.scaler.pkl")
        print(f"Model saved to {out_dir}/direction_model.pkl")
    except ImportError as e:
        print(f"Install scikit-learn and joblib: pip install scikit-learn joblib")
        raise e


if __name__ == "__main__":
    main()
