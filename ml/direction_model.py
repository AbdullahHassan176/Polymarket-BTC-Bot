"""
ml/direction_model.py – ML-based P(UP) for 5-min window.

Uses sklearn classifier trained on [ema_fast, ema_slow, atr_pct, ibs, 
close_pct_change, secs_remaining] → UP/DOWN outcome.
Falls back to 0.5 when no model or insufficient data.

Train with: python -m ml.train_direction_model
"""

from pathlib import Path
from typing import Optional

import pandas as pd


def _get_model_path() -> Path:
    return Path(__file__).parent / "direction_model.pkl"


def ml_p_up(
    ema_fast: float,
    ema_slow: float,
    atr_pct: float,
    ibs: float,
    close_pct_change: float,
    secs_remaining: float,
    eth_close_pct_change: float = 0.0,
) -> float:
    """
    ML-implied P(UP) for the 5-min window.

    Returns 0.5 if no trained model available (neutral).
    """
    try:
        import joblib

        path = _get_model_path()
        if not path.exists():
            return 0.5

        clf = joblib.load(path)
        scaler = joblib.load(path.with_suffix(".scaler.pkl")) if (path.with_suffix(".scaler.pkl")).exists() else None

        feats = [ema_fast, ema_slow, atr_pct, ibs, close_pct_change, secs_remaining]
        n_in = getattr(scaler, "n_features_in_", 6) if scaler is not None else 6
        if n_in >= 7:
            feats.append(eth_close_pct_change)
        X = [feats]
        if scaler is not None:
            X = scaler.transform(X)

        proba = getattr(clf, "predict_proba", None)
        if proba is not None:
            p = proba(X)[0]
            # Assume class 1 = UP
            return float(p[1]) if len(p) > 1 else 0.5
        pred = clf.predict(X)[0]
        return 1.0 if pred == 1 else 0.0
    except Exception:
        return 0.5


def build_training_data(
    entries_csv: str = "logs/trade_entries_12h_profit.csv",
    trades_csv: str = "logs/trades_12h_profit.csv",
    out_csv: str = "logs/ml_training_data.csv",
) -> pd.DataFrame:
    """
    Build training dataset from trade_entries + trades (join by condition_id).

    Features: ema_fast, ema_slow, atr_pct, ibs, secs_remaining, close_pct_change (derived).
    Label: 1 if direction=YES and outcome in {WIN,TP}, or direction=NO and outcome in {LOSS,SL}; else 0 for UP.
    """
    entries_path = Path(entries_csv)
    trades_path = Path(trades_csv)
    if not entries_path.exists() or not trades_path.exists():
        return pd.DataFrame()

    entries = pd.read_csv(entries_path)
    trades = pd.read_csv(trades_path)

    # CLOSE rows only, with outcome
    closes = trades[trades["action"].str.upper() == "CLOSE"].copy()
    if closes.empty:
        return pd.DataFrame()

    # Merge entries (at OPEN) with closes to get outcome per condition_id
    closes = closes[["condition_id", "direction", "outcome", "entry_price"]]
    merged = entries.merge(closes, on="condition_id", how="inner", suffixes=("", "_close"))

    # Label: 1 if market resolved UP (YES won). WIN/TP when we bet YES, or LOSS/SL when we bet NO.
    # Actually: label = 1 if market resolved UP (YES won). So: WIN/TP when direction=YES, or LOSS/SL when direction=NO
    def resolved_up(row):
        o = (row.get("outcome") or "").upper()
        d = (row.get("direction") or "").upper()
        if d == "YES" and o in ("WIN", "TP"):
            return 1
        if d == "NO" and o in ("LOSS", "SL"):
            return 1
        return 0

    merged["label_up"] = merged.apply(resolved_up, axis=1)

    # close_pct_change: need window_start_btc; (current_btc - window_start) / window_start
    if "window_start_btc" in merged.columns and "btc_spot" in merged.columns:
        merged["close_pct_change"] = (merged["btc_spot"] - merged["window_start_btc"]) / merged["window_start_btc"].replace(0, 1)
    else:
        merged["close_pct_change"] = 0.0

    features = ["ema_fast", "ema_slow", "atr_pct", "ibs", "secs_remaining", "close_pct_change", "label_up"]
    available = [c for c in features if c in merged.columns]
    out = merged[available]
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    return out
