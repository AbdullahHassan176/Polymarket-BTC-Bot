#!/usr/bin/env python3
"""
backfill_v2.py — Build training dataset for ML direction model v2.

Uses Binance for historical 1m candles (free, no auth, up to 90 days).
Fetches Deribit DVOL and Binance funding rate history for each window.
Labels each 5-min window: 1 if close >= open (UP), else 0.

Usage (from project root):
  python scripts/ml/backfill_v2.py --days 60 --out logs/ml_training_v2.csv
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import argparse
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from ml.features_v2 import compute_all_features, FEATURE_COLS

REQUEST_TIMEOUT = 10
BINANCE_KLINES_URL  = "https://api.binance.com/api/v3/klines"
DERIBIT_DVOL_URL    = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BINANCE_OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"

# Per-asset configuration.
# dvol_currency: "BTC" or "ETH" (Deribit only has BTC + ETH DVOL;
#                SOL/XRP/DOGE use BTC DVOL as a macro volatility proxy).
# round_level_1/2: psychological round number levels for dist_round_* features.
ASSET_CONFIG = {
    "BTC":  {"symbol": "BTCUSDT",  "dvol_currency": "BTC", "round_level_1": 1000.0,  "round_level_2": 5000.0,  "asset_id": 0},
    "ETH":  {"symbol": "ETHUSDT",  "dvol_currency": "ETH", "round_level_1": 100.0,   "round_level_2": 500.0,   "asset_id": 1},
    "SOL":  {"symbol": "SOLUSDT",  "dvol_currency": "BTC", "round_level_1": 10.0,    "round_level_2": 50.0,    "asset_id": 2},
    "XRP":  {"symbol": "XRPUSDT",  "dvol_currency": "BTC", "round_level_1": 0.10,    "round_level_2": 0.50,    "asset_id": 3},
    "DOGE": {"symbol": "DOGEUSDT", "dvol_currency": "BTC", "round_level_1": 0.05,    "round_level_2": 0.10,    "asset_id": 4},
}
ALL_ASSETS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]


# ---------------------------------------------------------------------------
# BINANCE 1m CANDLES
# ---------------------------------------------------------------------------

def fetch_binance_candles(start_ms: int, end_ms: int, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Fetch 1m candles from Binance between start and end (ms timestamps)."""
    all_rows = []
    current_end = end_ms
    while True:
        try:
            resp = requests.get(
                BINANCE_KLINES_URL,
                params={
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": start_ms,
                    "endTime": current_end,
                    "limit": 1000,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:
            print(f"  Binance fetch error: {exc}")
            break

        if not rows:
            break

        for r in rows:
            all_rows.append({
                "ts":    pd.to_datetime(int(r[0]), unit="ms", utc=True),
                "open":  float(r[1]),
                "high":  float(r[2]),
                "low":   float(r[3]),
                "close": float(r[4]),
                "vol":   float(r[5]),
            })

        # Binance returns oldest-first; if we got fewer than 1000 we're done
        if len(rows) < 1000:
            break

        # Next batch: start from last candle ts + 1 minute
        last_ts_ms = int(rows[-1][0])
        start_ms = last_ts_ms + 60_000
        if start_ms >= current_end:
            break

        time.sleep(0.05)  # gentle rate limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows).drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# DERIBIT DVOL HISTORICAL
# ---------------------------------------------------------------------------

def fetch_dvol_historical(start_ms: int, end_ms: int, currency: str = "BTC") -> pd.Series:
    """
    Fetch historical DVOL at hourly resolution with pagination.
    currency: "BTC" or "ETH" (Deribit only supports these two).
    Deribit caps at 1000 rows per request; paginate to cover full range.
    Hourly DVOL is sufficient for the 7-day percentile calculation.
    """
    all_rows = []
    chunk_start = start_ms

    while chunk_start < end_ms:
        try:
            resp = requests.get(
                DERIBIT_DVOL_URL,
                params={
                    "currency": currency,
                    "start_timestamp": chunk_start,
                    "end_timestamp": end_ms,
                    "resolution": "3600",  # hourly — gives ~41 days per request
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("result", {}).get("data", [])
        except Exception as exc:
            print(f"  DVOL fetch error: {exc}")
            break

        if not rows:
            break

        all_rows.extend(rows)

        # Last row timestamp + 1 hour = next batch start
        last_ts_ms = int(rows[-1][0])
        chunk_start = last_ts_ms + 3_600_000

        if len(rows) < 1000:
            break  # got all remaining data

        time.sleep(0.2)

    if not all_rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(all_rows, columns=["ts_ms", "open", "high", "low", "close"])
    df["ts"] = pd.to_datetime(df["ts_ms"].astype(int), unit="ms", utc=True)
    s = df.drop_duplicates(subset=["ts"]).set_index("ts")["close"].sort_index()
    return s


# ---------------------------------------------------------------------------
# BINANCE FUNDING RATE HISTORICAL
# ---------------------------------------------------------------------------

def fetch_funding_rate_historical(start_ms: int, end_ms: int, symbol: str = "BTCUSDT") -> pd.Series:
    """Fetch historical 8-hourly funding rates, returns Series indexed by ts."""
    try:
        resp = requests.get(
            BINANCE_FUNDING_URL,
            params={
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms", utc=True)
        df["rate"] = df["fundingRate"].astype(float)
        s = df.set_index("ts")["rate"].sort_index()
        return s
    except Exception as exc:
        print(f"  Funding rate fetch error: {exc}")
        return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# BINANCE OPEN INTEREST HISTORICAL
# ---------------------------------------------------------------------------

def fetch_oi_hist_all(start_ms: int, end_ms: int, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    Fetch historical 5-min OI snapshots from Binance paginated by endTime.
    Binance returns up to 500 rows per request (newest first when using endTime).
    Returns DataFrame with columns ['ts', 'oi'] indexed by ts, sorted ascending.
    """
    all_rows = []
    current_end = end_ms

    while True:
        try:
            resp = requests.get(
                BINANCE_OI_HIST_URL,
                params={
                    "symbol": symbol,
                    "period": "5m",
                    "limit": 500,
                    "endTime": current_end,
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:
            print(f"  OI hist fetch error: {exc}")
            break

        if not rows:
            break

        for r in rows:
            ts_ms = int(r["timestamp"])
            if ts_ms < start_ms:
                continue
            all_rows.append({
                "ts": pd.Timestamp(ts_ms, unit="ms", tz="UTC"),
                "oi": float(r["sumOpenInterest"]),
            })

        # Check if oldest row in this batch is before start_ms — done
        oldest_ts_ms = int(rows[-1]["timestamp"]) if rows else 0
        if oldest_ts_ms <= start_ms or len(rows) < 500:
            break

        # Paginate: move endTime back to oldest row in this batch minus 1 ms
        current_end = oldest_ts_ms - 1
        time.sleep(0.1)

    if not all_rows:
        return pd.DataFrame(columns=["ts", "oi"])

    df = pd.DataFrame(all_rows).drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return df


def fetch_oi_hist_for_window(ws_ts: pd.Timestamp, oi_df: pd.DataFrame) -> float:
    """
    Look up OI % change between the snapshot closest to ws_ts-5min and ws_ts.
    Returns 0.0 if oi_df is empty or lookup fails.
    """
    if oi_df.empty:
        return 0.0
    try:
        target_now  = ws_ts
        target_prev = ws_ts - pd.Timedelta(minutes=5)

        # Find closest row to each target
        idx_now  = (oi_df["ts"] - target_now).abs().idxmin()
        idx_prev = (oi_df["ts"] - target_prev).abs().idxmin()

        oi_now  = float(oi_df.loc[idx_now,  "oi"])
        oi_prev = float(oi_df.loc[idx_prev, "oi"])

        if oi_prev == 0:
            return 0.0
        return (oi_now - oi_prev) / oi_prev
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# MAIN BACKFILL
# ---------------------------------------------------------------------------

def build_backfill(days: int = 60, out_csv: str = "logs/ml_training_v2.csv",
                   asset: str = "BTC") -> pd.DataFrame:
    """
    Build a labeled training dataset from the last `days` days of 5-min windows.

    For each window:
      - Features computed from 1m candles BEFORE the window starts (no lookahead)
      - Label = 1 if window_close >= window_open (UP)

    asset: one of BTC/ETH/SOL/XRP/DOGE, or "ALL" to loop over all five.
    When asset="ALL", writes per-asset CSVs then concatenates into out_csv.
    """
    if asset.upper() == "ALL":
        frames = []
        for a in ALL_ASSETS:
            tmp = out_csv.replace(".csv", f"_{a}.csv")
            df_a = build_backfill(days=days, out_csv=tmp, asset=a)
            if not df_a.empty:
                frames.append(df_a)
        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sample(frac=1, random_state=42).reset_index(drop=True)
        combined.to_csv(out_csv, index=False)
        print(f"\nCombined all assets: {len(combined)} rows -> {out_csv}")
        print(f"  Per-asset counts: {combined['asset'].value_counts().to_dict()}")
        return combined

    asset = asset.upper()
    cfg = ASSET_CONFIG.get(asset)
    if cfg is None:
        print(f"  ERROR: Unknown asset '{asset}'. Choose from {list(ASSET_CONFIG.keys())} or ALL.")
        return pd.DataFrame()

    symbol       = cfg["symbol"]
    dvol_ccy     = cfg["dvol_currency"]
    round_lvl_1  = cfg["round_level_1"]
    round_lvl_2  = cfg["round_level_2"]
    asset_id     = cfg["asset_id"]

    print(f"Building v2 training data [{asset}]: {days} days of 5-min windows...")

    now_utc = datetime.now(timezone.utc)
    start_utc = now_utc - timedelta(days=days)
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms   = int(now_utc.timestamp() * 1000)

    # Need extra history before start for indicator warmup (60 min)
    fetch_start_ms = start_ms - 90 * 60 * 1000

    # 1. Fetch candles
    print(f"  Fetching Binance 1m candles ({symbol}, {days} days + 90min warmup)...")
    btc = fetch_binance_candles(fetch_start_ms, end_ms, symbol=symbol)
    if btc.empty or len(btc) < 100:
        print(f"  ERROR: Not enough {asset} candle data.")
        return pd.DataFrame()
    print(f"  Got {len(btc)} 1m candles.")

    # 2. Fetch DVOL (ETH uses ETH DVOL; SOL/XRP/DOGE use BTC DVOL as macro proxy)
    dvol_start_ms = start_ms - 7 * 24 * 60 * 60 * 1000
    print(f"  Fetching Deribit {dvol_ccy} DVOL...")
    dvol_series = fetch_dvol_historical(dvol_start_ms, end_ms, currency=dvol_ccy)
    print(f"  Got {len(dvol_series)} DVOL data points.")
    time.sleep(0.3)

    # 3. Fetch funding rate history (per-asset perpetual)
    print(f"  Fetching Binance funding rate history ({symbol})...")
    funding_series = fetch_funding_rate_historical(fetch_start_ms, end_ms, symbol=symbol)
    print(f"  Got {len(funding_series)} funding rate records.")

    # 4. Fetch OI history (5-min snapshots, paginated to cover all `days` days)
    oi_fetch_start_ms = start_ms - 10 * 60 * 1000  # extra 10 min for first window lookup
    print(f"  Fetching Binance OI history ({symbol}, 5m snapshots, paginated)...")
    oi_df = fetch_oi_hist_all(oi_fetch_start_ms, end_ms, symbol=symbol)
    print(f"  Got {len(oi_df)} OI snapshots.")

    # 5. Identify 5-min windows
    # Align to 5-min UTC boundaries between start and end
    windows_start = []
    t = start_utc.replace(second=0, microsecond=0)
    # Snap to nearest 5-min
    t = t - timedelta(minutes=t.minute % 5)
    while t < now_utc - timedelta(minutes=6):
        windows_start.append(t)
        t += timedelta(minutes=5)

    print(f"  Processing {len(windows_start)} 5-min windows...")

    rows = []
    for ws in windows_start:
        we = ws + timedelta(minutes=5)
        ws_ts = pd.Timestamp(ws)
        we_ts = pd.Timestamp(we)

        # Window candles (for labeling)
        win_candles = btc[(btc["ts"] >= ws_ts) & (btc["ts"] < we_ts)]
        if len(win_candles) < 4:
            continue

        first_open  = float(win_candles.iloc[0]["open"])
        last_close  = float(win_candles.iloc[-1]["close"])
        label_up    = 1 if last_close >= first_open else 0

        # Skip tiny doji moves (<0.03%) — effectively unforecastable noise
        move_pct = abs(last_close - first_open) / first_open if first_open > 0 else 0
        if move_pct < 0.0003:
            continue

        # Features: use candles from [ws - 120min, ws) strictly before window
        feat_start = ws_ts - pd.Timedelta(minutes=240)  # 4h = 16 15-min bars for Supertrend
        df_feat = btc[(btc["ts"] >= feat_start) & (btc["ts"] < ws_ts)].copy()
        if len(df_feat) < 60:
            continue

        # Get DVOL at window start
        dvol_at_ws = None
        dvol_window = None
        if not dvol_series.empty:
            before_dvol = dvol_series[dvol_series.index <= ws_ts]
            if not before_dvol.empty:
                dvol_at_ws = float(before_dvol.iloc[-1])
                # 7-day window for percentile
                lookback_start = ws_ts - pd.Timedelta(days=7)
                dvol_window = dvol_series[
                    (dvol_series.index >= lookback_start) & (dvol_series.index <= ws_ts)
                ]

        # Get funding rate at window start (most recent before ws)
        funding_at_ws = None
        if not funding_series.empty:
            before_funding = funding_series[funding_series.index <= ws_ts]
            if not before_funding.empty:
                funding_at_ws = float(before_funding.iloc[-1])

        # Look up OI change for this window
        oi_change_at_ws = fetch_oi_hist_for_window(ws_ts, oi_df)

        # Compute features
        feats = compute_all_features(
            df_feat,
            window_start_ts=ws_ts,
            dvol_series=dvol_window,
            funding_rate=funding_at_ws,
            oi_change_pct=oi_change_at_ws,
            round_level_1=round_lvl_1,
            round_level_2=round_lvl_2,
            asset_id=asset_id,
        )

        if not feats:
            continue

        # Override time features with actual window time
        feats["hour_sin"] = float(np.sin(2 * np.pi * (ws.hour + ws.minute / 60) / 24))
        feats["hour_cos"] = float(np.cos(2 * np.pi * (ws.hour + ws.minute / 60) / 24))
        feats["is_asian_session"] = 1.0 if 0 <= ws.hour < 8 else 0.0
        feats["is_us_session"]    = 1.0 if 13 <= ws.hour < 21 else 0.0

        row = {"window_start": ws, "asset": asset, "label_up": label_up}
        row.update(feats)
        rows.append(row)

    if not rows:
        print("  ERROR: No valid windows produced.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"\nDone. Wrote {len(df)} labeled windows -> {out_csv}")
    print(f"Label balance: {df['label_up'].mean():.2%} UP")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",  type=int,   default=60,                        help="Days of history to backfill")
    parser.add_argument("--out",   type=str,   default="logs/ml_training_v2.csv", help="Output CSV path")
    parser.add_argument("--asset", type=str,   default="BTC",
                        help="Asset to backfill: BTC/ETH/SOL/XRP/DOGE or ALL (default: BTC)")
    args = parser.parse_args()

    import os
    os.chdir(ROOT)
    build_backfill(days=args.days, out_csv=args.out, asset=args.asset)


if __name__ == "__main__":
    main()
