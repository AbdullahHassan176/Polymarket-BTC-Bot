#!/usr/bin/env python3
"""
Backfill training data from OKX historical 1m candles.

Builds 5-min windows: for each window, label = 1 if close >= open (UP), else 0.
Features: ema_fast, ema_slow, atr_pct, ibs, close_pct_change, secs_remaining (simulated 60s),
          eth_close_pct_change (cross-asset).

Usage:
  python -m ml.backfill_training_data --windows 600 --out logs/ml_training_backfill.csv

Requires: data module, pandas
"""

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import config
import data


def _fetch_btc_history_paginated(minutes: int) -> pd.DataFrame:
    """Fetch minutes of BTC 1m candles. Uses regular candles first, then history pagination."""
    # First batch: recent candles (OKX allows up to 300 per request)
    batch1 = data.fetch_candles(bar="1m", limit=min(minutes, 300))
    if batch1.empty:
        return pd.DataFrame()
    if len(batch1) >= minutes:
        return batch1

    # Paginate history for more
    all_dfs = [batch1]
    before_ms = int(batch1["ts"].min().timestamp() * 1000) - 1
    remaining = minutes - len(batch1)
    while remaining > 0:
        df = data.fetch_candles_history(inst_id=config.SPOT_TICKER, bar="1m", limit=min(100, remaining), before_ms=before_ms)
        if df.empty:
            break
        all_dfs.append(df)
        before_ms = int(df["ts"].min().timestamp() * 1000) - 1
        remaining -= len(df)
        time.sleep(0.2)

    out = pd.concat(all_dfs, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return out


def _get_eth_close_map(btc_df: pd.DataFrame) -> dict:
    """Map (ts -> ETH close) for each BTC candle ts. Fetches ETH in chunks."""
    if btc_df.empty:
        return {}
    t0 = btc_df["ts"].min()
    t1 = btc_df["ts"].max()
    mins = int((t1 - t0).total_seconds() / 60) + 60
    eth = data.fetch_candles_history(inst_id="ETH-USDT", bar="1m", limit=min(mins, 300))
    if eth.empty:
        return {}
    eth_map = dict(zip(eth["ts"], eth["close"]))
    out = {}
    eth_ts = sorted(eth_map.keys())
    for _, row in btc_df.iterrows():
        t = row["ts"]
        # Find latest eth close at or before t
        candidates = [ts for ts in eth_ts if ts <= t]
        out[t] = eth_map[candidates[-1]] if candidates else None
    return out


def build_backfill_data(num_windows: int = 600, include_eth: bool = True, out_csv: str = "logs/ml_training_backfill.csv") -> pd.DataFrame:
    """
    Build training data from historical 5-min windows.

    Each window: 5 consecutive 1m candles. Label = 1 if last close >= first open.
    Features at 4th minute (1 min before close): ema_fast, ema_slow, atr_pct, ibs, close_pct_change.
    """
    # Need num_windows * 5 + 40 1m candles (5 per window + 35 for EMA/ATR warmup)
    minutes_needed = num_windows * 5 + 50
    print(f"Fetching {minutes_needed} 1m BTC candles...")
    btc = _fetch_btc_history_paginated(minutes_needed)
    if btc.empty or len(btc) < 50:
        print("Failed to fetch enough BTC data.")
        return pd.DataFrame()

    btc = data.compute_indicators(btc)
    btc = btc.dropna(subset=["ema_fast", "ema_slow", "atr_pct"]).reset_index(drop=True)

    eth_close_map = {}
    if include_eth:
        print("Fetching ETH candles for cross-asset feature...")
        eth_close_map = _get_eth_close_map(btc)

    # Align to 5-min boundaries (e.g. :00, :05, :10)
    btc["minute"] = btc["ts"].dt.minute
    btc["hour"] = btc["ts"].dt.hour
    btc["round5"] = (btc["ts"].dt.minute // 5) * 5
    btc["window_start"] = btc["ts"] - pd.to_timedelta(btc["ts"].dt.minute % 5, unit="m") - pd.to_timedelta(btc["ts"].dt.second, unit="s")

    rows = []
    for ws, grp in btc.groupby("window_start"):
        if len(grp) < 5:
            continue
        grp = grp.sort_values("ts").reset_index(drop=True)
        first_open = float(grp.iloc[0]["open"])
        last_close = float(grp.iloc[-1]["close"])
        label_up = 1 if last_close >= first_open else 0

        # Use 4th candle (index 3) for features - simulates "1 min before window end"
        row4 = grp.iloc[3]
        ema_fast = float(row4["ema_fast"])
        ema_slow = float(row4["ema_slow"])
        atr_pct = float(row4["atr_pct"])
        h, l, c = float(row4["high"]), float(row4["low"]), float(row4["close"])
        ibs = (c - l) / (h - l) if (h - l) > 0 else 0.5
        close_pct_change = (c - first_open) / first_open if first_open else 0
        secs_remaining = 60.0  # 1 min left

        eth_chg = 0.0
        if eth_close_map:
            eth_val = eth_close_map.get(row4["ts"])
            eth_first = eth_close_map.get(grp.iloc[0]["ts"])
            if eth_val is not None and eth_first is not None and eth_first > 0:
                eth_chg = (eth_val - eth_first) / eth_first

        rows.append({
            "window_start": ws,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "atr_pct": atr_pct,
            "ibs": ibs,
            "close_pct_change": close_pct_change,
            "secs_remaining": secs_remaining,
            "eth_close_pct_change": eth_chg,
            "label_up": label_up,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"Wrote {len(df)} windows -> {out_csv}")
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=int, default=600, help="Number of 5-min windows")
    parser.add_argument("--out", default="logs/ml_training_backfill.csv", help="Output CSV")
    parser.add_argument("--no-eth", action="store_true", help="Skip ETH cross-asset feature")
    args = parser.parse_args()

    build_backfill_data(num_windows=args.windows, include_eth=not args.no_eth, out_csv=args.out)


if __name__ == "__main__":
    main()
