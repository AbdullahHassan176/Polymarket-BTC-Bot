#!/usr/bin/env python3
"""
Train Bi-LSTM model from candle data (backfill or live).

Usage:
  python -m ml.train_lstm_model --backfill  # Use backfill data
  python -m ml.train_lstm_model --candles 600  # Fetch 600 1m candles and build sequences

Requires: pip install torch pandas
"""

import argparse
from pathlib import Path

import pandas as pd

import data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="Use backfill CSV (logs/ml_training_backfill.csv)")
    parser.add_argument("--candles", type=int, default=0, help="Fetch N 1m candles and build sequences")
    parser.add_argument("--seq-len", type=int, default=20, help="Sequence length")
    parser.add_argument("--epochs", type=int, default=50)
    args = parser.parse_args()

    X, y = None, None

    if args.backfill:
        path = Path("logs/ml_training_backfill.csv")
        if not path.exists():
            print("Run: python -m ml.backfill_training_data --windows 200 first.")
            return
        df = pd.read_csv(path)
        # Backfill has flat features - convert to sequences by sliding window
        # Actually backfill is per-window, not sequences. Use candles path for LSTM.
        print("Backfill has flat features. Use --candles for LSTM training.")
        return

    if args.candles > 0:
        print(f"Fetching {args.candles} 1m BTC candles...")
        btc = data.fetch_candles(bar="1m", limit=args.candles)
        if btc.empty or len(btc) < 80:
            print("Need 80+ candles. Fetch failed or insufficient data.")
            return
        btc = data.compute_indicators(btc)
        from ml.lstm_model import build_sequences_from_candles, train_lstm

        X, y = build_sequences_from_candles(btc, seq_len=args.seq_len, window_minutes=5)
        if len(X) < 50:
            print(f"Only {len(X)} sequences. Need 50+. Try --candles 500.")
            return
        print(f"Training Bi-LSTM on {len(X)} sequences...")
        metrics = train_lstm(X, y, epochs=args.epochs)
        print(f"Done: {metrics}")
        return

    # Default: fetch 500 candles
    print("Fetching 500 1m candles (default)...")
    btc = data.fetch_candles(bar="1m", limit=500)
    if btc.empty or len(btc) < 80:
        print("Need 80+ candles.")
        return
    btc = data.compute_indicators(btc)
    from ml.lstm_model import build_sequences_from_candles, train_lstm

    X, y = build_sequences_from_candles(btc, seq_len=args.seq_len)
    if len(X) < 50:
        print(f"Only {len(X)} sequences.")
        return
    metrics = train_lstm(X, y, epochs=args.epochs)
    print(f"Done: {metrics}")


if __name__ == "__main__":
    main()
