"""
features_v2.py — Feature engineering for ML direction model v2.

Based on research findings:
  Top features: DVOL 7-day percentile, 8/55 EMA spread, bars-since-Supertrend-flip.
  Key insight: hand-crafted domain features beat raw sequences by a wide margin.

Data sources (all free, no auth):
  - OKX 1m candles (already in data.py)
  - Deribit BTC DVOL index
  - Binance futures funding rate + open interest
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 8


# ---------------------------------------------------------------------------
# DERIBIT DVOL
# ---------------------------------------------------------------------------

def fetch_dvol_series(lookback_mins: int = 720) -> pd.Series:
    """
    Fetch BTC DVOL index from Deribit at 1-min resolution.
    Returns a pd.Series indexed by UTC timestamp.
    Lookback_mins default = 12 hours (enough for 7-day percentile from cache).
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_mins * 60 * 1000

    try:
        resp = requests.get(
            "https://www.deribit.com/api/v2/public/get_volatility_index_data",
            params={
                "currency": "BTC",
                "start_timestamp": start_ms,
                "end_timestamp": now_ms,
                "resolution": "60",  # 1-minute
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("result", {}).get("data", [])
        if not rows:
            return pd.Series(dtype=float)
        # Each row: [ts_ms, open, high, low, close]
        df = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close"])
        df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        s = df.set_index("ts")["close"].sort_index()
        return s
    except Exception as exc:
        logger.warning("DVOL fetch failed: %s", exc)
        return pd.Series(dtype=float)


def dvol_7day_percentile(dvol_series: pd.Series, current_val: Optional[float] = None) -> float:
    """
    Compute where the current DVOL sits within the last 7 days of DVOL values.
    Returns 0.0-1.0 (0 = lowest in 7 days, 1 = highest).
    Falls back to 0.5 if insufficient data.
    """
    if dvol_series.empty:
        return 0.5
    lookback = dvol_series.iloc[-min(len(dvol_series), 7 * 24 * 60):]
    val = current_val if current_val is not None else float(dvol_series.iloc[-1])
    lo, hi = lookback.min(), lookback.max()
    if hi == lo:
        return 0.5
    return float(np.clip((val - lo) / (hi - lo), 0.0, 1.0))


def fetch_current_dvol() -> Optional[float]:
    """Fetch just the latest BTC DVOL value."""
    s = fetch_dvol_series(lookback_mins=60)
    if s.empty:
        return None
    return float(s.iloc[-1])


# ---------------------------------------------------------------------------
# BINANCE FUTURES — funding rate + open interest
# ---------------------------------------------------------------------------

def fetch_funding_rate() -> Optional[float]:
    """Latest BTC perpetual funding rate from Binance (free, no auth)."""
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": "BTCUSDT"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("lastFundingRate", 0))
    except Exception as exc:
        logger.warning("Funding rate fetch failed: %s", exc)
        return None


def fetch_taker_imbalance(lookback_seconds: int = 120) -> dict:
    """
    Fetch recent aggTrades from Binance and compute taker buy/sell imbalance metrics.

    Returns dict with keys:
        taker_buy_ratio_30s, taker_buy_ratio_60s, taker_buy_ratio_120s, cvd_slope
    Falls back to neutral values on any error.
    """
    _neutral = {
        "taker_buy_ratio_30s":  0.5,
        "taker_buy_ratio_60s":  0.5,
        "taker_buy_ratio_120s": 0.5,
        "cvd_slope": 0.0,
    }
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/aggTrades",
            params={"symbol": "BTCUSDT", "limit": 1000},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        trades = resp.json()

        if not trades:
            return _neutral

        now_ms = int(time.time() * 1000)
        cutoff_120 = now_ms - 120_000
        cutoff_60  = now_ms - 60_000
        cutoff_30  = now_ms - 30_000

        buy_120 = sell_120 = 0.0
        buy_60  = sell_60  = 0.0
        buy_30  = sell_30  = 0.0

        # For CVD slope: split last 60s into first-30s and last-30s halves
        cvd_first30_buy  = cvd_first30_sell  = 0.0
        cvd_last30_buy   = cvd_last30_sell   = 0.0

        for t in trades:
            ts_ms = int(t["T"])
            qty   = float(t["q"])
            is_buyer_maker = bool(t["m"])  # True = taker is SELLER

            if ts_ms < cutoff_120:
                continue

            if is_buyer_maker:  # taker = seller
                sell_120 += qty
                if ts_ms >= cutoff_60:
                    sell_60 += qty
                    if ts_ms >= cutoff_30:
                        sell_30 += qty
                        cvd_last30_sell  += qty
                    else:
                        cvd_first30_sell += qty
            else:  # taker = buyer
                buy_120 += qty
                if ts_ms >= cutoff_60:
                    buy_60 += qty
                    if ts_ms >= cutoff_30:
                        buy_30 += qty
                        cvd_last30_buy  += qty
                    else:
                        cvd_first30_buy += qty

        total_120 = buy_120 + sell_120
        total_60  = buy_60  + sell_60
        total_30  = buy_30  + sell_30

        ratio_120 = buy_120 / total_120 if total_120 > 0 else 0.5
        ratio_60  = buy_60  / total_60  if total_60  > 0 else 0.5
        ratio_30  = buy_30  / total_30  if total_30  > 0 else 0.5

        # CVD slope: (cvd_last30s - cvd_first30s) / cvd_std, normalized
        cvd_first30 = cvd_first30_buy  - cvd_first30_sell
        cvd_last30  = cvd_last30_buy   - cvd_last30_sell
        cvd_std = (abs(cvd_first30) + abs(cvd_last30)) / 2
        if cvd_std > 0:
            cvd_slope = (cvd_last30 - cvd_first30) / cvd_std
        else:
            cvd_slope = 0.0

        return {
            "taker_buy_ratio_30s":  float(ratio_30),
            "taker_buy_ratio_60s":  float(ratio_60),
            "taker_buy_ratio_120s": float(ratio_120),
            "cvd_slope": float(np.clip(cvd_slope, -5.0, 5.0)),
        }
    except Exception as exc:
        logger.warning("taker imbalance fetch failed: %s", exc)
        return _neutral


def fetch_oi_change_pct(periods: int = 3) -> Optional[float]:
    """
    Fetch recent OI snapshots (5-min intervals) from Binance.
    Returns % change over last `periods` intervals.
    """
    try:
        resp = requests.get(
            "https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": "BTCUSDT", "period": "5m", "limit": periods + 1},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json()
        if len(rows) < 2:
            return None
        old_oi = float(rows[0]["sumOpenInterest"])
        new_oi = float(rows[-1]["sumOpenInterest"])
        if old_oi == 0:
            return None
        return (new_oi - old_oi) / old_oi
    except Exception as exc:
        logger.warning("OI change fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# SUPERTREND
# ---------------------------------------------------------------------------

def compute_supertrend(df: pd.DataFrame, period: int = 7, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Compute Supertrend indicator on OHLC data.
    Adds columns: supertrend_upper, supertrend_lower, supertrend, supertrend_direction (+1=up, -1=down).
    """
    df = df.copy()
    hl_avg = (df["high"] + df["low"]) / 2
    atr = (df["high"] - df["low"]).rolling(period).mean()

    df["_basic_upper"] = hl_avg + multiplier * atr
    df["_basic_lower"] = hl_avg - multiplier * atr

    upper = df["_basic_upper"].copy()
    lower = df["_basic_lower"].copy()
    direction = pd.Series(index=df.index, dtype=float)
    supertrend = pd.Series(index=df.index, dtype=float)

    for i in range(1, len(df)):
        # Upper band
        if df["_basic_upper"].iloc[i] < upper.iloc[i - 1] or df["close"].iloc[i - 1] > upper.iloc[i - 1]:
            upper.iloc[i] = df["_basic_upper"].iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i - 1]

        # Lower band
        if df["_basic_lower"].iloc[i] > lower.iloc[i - 1] or df["close"].iloc[i - 1] < lower.iloc[i - 1]:
            lower.iloc[i] = df["_basic_lower"].iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i - 1]

        # Direction
        prev_dir = direction.iloc[i - 1] if not pd.isna(direction.iloc[i - 1]) else 1
        if df["close"].iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = prev_dir

        supertrend.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    df["supertrend"] = supertrend
    df["supertrend_direction"] = direction
    df.drop(columns=["_basic_upper", "_basic_lower"], inplace=True)
    return df


def bars_since_supertrend_flip(df: pd.DataFrame) -> int:
    """
    Count how many bars since the last Supertrend direction change.
    Returns number of bars (0 = just flipped this bar).
    """
    if "supertrend_direction" not in df.columns:
        return 50  # unknown
    dirs = df["supertrend_direction"].dropna().values
    if len(dirs) < 2:
        return 50
    for i in range(len(dirs) - 1, 0, -1):
        if dirs[i] != dirs[i - 1]:
            return len(dirs) - 1 - i
    return len(dirs)  # never flipped in window


# ---------------------------------------------------------------------------
# MULTI-TIMEFRAME RESAMPLING
# ---------------------------------------------------------------------------

def resample_to_tf(df_1m: pd.DataFrame, tf: str = "15min") -> pd.DataFrame:
    """Resample 1m candles to a higher timeframe."""
    df = df_1m.set_index("ts").copy()
    resampled = df.resample(tf).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "vol": "sum",
    }).dropna(subset=["close"])
    resampled = resampled.reset_index()
    return resampled


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Standard RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# CORE FEATURE BUILDER
# ---------------------------------------------------------------------------

def compute_all_features(
    df_1m: pd.DataFrame,
    window_start_ts: Optional[pd.Timestamp] = None,
    dvol_series: Optional[pd.Series] = None,
    funding_rate: Optional[float] = None,
    oi_change_pct: Optional[float] = None,
    taker_imbalance: Optional[dict] = None,
    round_level_1: float = 1000.0,
    round_level_2: float = 5000.0,
    asset_id: int = 0,
) -> dict:
    # asset_id: 0=BTC, 1=ETH, 2=SOL, 3=XRP, 4=DOGE
    """
    Compute all v2 features from 1-min candles and external data.

    Args:
        df_1m: DataFrame with ts, open, high, low, close, vol (sorted oldest-first).
        window_start_ts: UTC timestamp when the current 5-min window started.
        dvol_series: pd.Series of DVOL values indexed by ts (from fetch_dvol_series).
        funding_rate: Current BTC perpetual funding rate (float or None).
        oi_change_pct: OI % change over last 3 periods (float or None).
        taker_imbalance: Dict with taker buy ratio / CVD metrics (from fetch_taker_imbalance).
                         If None, neutral defaults are used (0.5 / 0.0).

    Returns:
        Dict of feature_name -> float value.
    """
    feats = {}

    if df_1m.empty or len(df_1m) < 60:
        return feats

    df = df_1m.copy()

    # ---- Compute all derived columns first ----
    df["ema8"]  = df["close"].ewm(span=8,  adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema55"] = df["close"].ewm(span=55, adjust=False).mean()
    df["atr14"] = (df["high"] - df["low"]).rolling(14).mean()
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    df["rsi14_1m"] = compute_rsi(df["close"], 14)

    # Take last row AFTER all columns are added
    last = df.iloc[-1]

    # ---- 8/21/55 EMA (key features #2) ----
    feats["ema8_55_spread"] = float((last["ema8"] - last["ema55"]) / last["close"])
    feats["ema8_21_spread"] = float((last["ema8"] - last["ema21"]) / last["close"])
    feats["ema_trend_up"]   = 1.0 if last["ema8"] > last["ema55"] else 0.0

    # ---- ATR % (normalized volatility) ----
    feats["atr_pct"] = float(last["atr14"] / last["close"]) if last["close"] > 0 else 0.0

    # ---- IBS: internal bar strength ----
    h, l, c = float(last["high"]), float(last["low"]), float(last["close"])
    feats["ibs"] = (c - l) / (h - l) if (h - l) > 0 else 0.5

    # ---- RSI 1-min ----
    feats["rsi_1m"] = float(last["rsi14_1m"]) if not pd.isna(last["rsi14_1m"]) else 50.0

    # ---- Supertrend + bars since flip (key feature #3) ----
    # Compute on 15-min resampled candles — Supertrend flips rarely on 1-min bars
    # within a short lookback window, so 15-min gives meaningful signals.
    try:
        df_15m_st = resample_to_tf(df, "15min")
        if len(df_15m_st) >= 6:
            df_15m_st = compute_supertrend(df_15m_st, period=7, multiplier=3.0)
            feats["supertrend_direction"] = float(df_15m_st["supertrend_direction"].iloc[-1]) if not pd.isna(df_15m_st["supertrend_direction"].iloc[-1]) else 0.0
            feats["bars_since_st_flip"]   = float(bars_since_supertrend_flip(df_15m_st))
        else:
            feats["supertrend_direction"] = 0.0
            feats["bars_since_st_flip"]   = 10.0
    except Exception:
        feats["supertrend_direction"] = 0.0
        feats["bars_since_st_flip"]   = 10.0

    # ---- Realized volatility at multiple windows ----
    feats["rvol_5m"]  = float(df["log_ret"].iloc[-5:].std()  * np.sqrt(252 * 24 * 60)) if len(df) >= 5  else 0.0
    feats["rvol_30m"] = float(df["log_ret"].iloc[-30:].std() * np.sqrt(252 * 24 * 60)) if len(df) >= 30 else 0.0
    feats["rvol_4h"]  = float(df["log_ret"].iloc[-240:].std()* np.sqrt(252 * 24 * 60)) if len(df) >= 240 else 0.0
    feats["rvol_ratio_5_30"] = feats["rvol_5m"] / feats["rvol_30m"] if feats["rvol_30m"] > 0 else 1.0

    # ---- 15-min resampled RSI ----
    try:
        df_15m = resample_to_tf(df, "15min")
        if len(df_15m) >= 15:
            df_15m["rsi14"] = compute_rsi(df_15m["close"], 14)
            feats["rsi_15m"] = float(df_15m["rsi14"].iloc[-1]) if not pd.isna(df_15m["rsi14"].iloc[-1]) else 50.0
        else:
            feats["rsi_15m"] = 50.0
    except Exception:
        feats["rsi_15m"] = 50.0

    # ---- VWAP deviation ----
    try:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        recent = df.iloc[-60:]
        vwap = (typical.iloc[-60:] * df["vol"].iloc[-60:]).sum() / df["vol"].iloc[-60:].sum()
        feats["vwap_dev"] = float((last["close"] - vwap) / vwap) if vwap > 0 else 0.0
    except Exception:
        feats["vwap_dev"] = 0.0

    # ---- Volume ratio (current vs 20-bar avg) ----
    try:
        avg_vol = df["vol"].iloc[-20:].mean()
        feats["vol_ratio"] = float(last["vol"] / avg_vol) if avg_vol > 0 else 1.0
    except Exception:
        feats["vol_ratio"] = 1.0

    # ---- Partial candle: price move so far in current 5-min window ----
    if window_start_ts is not None:
        try:
            window_candles = df[df["ts"] >= window_start_ts]
            if not window_candles.empty:
                w_open = float(window_candles.iloc[0]["open"])
                feats["window_pct_change"] = (last["close"] - w_open) / w_open if w_open > 0 else 0.0
                feats["window_bars_in"] = float(len(window_candles))
            else:
                feats["window_pct_change"] = 0.0
                feats["window_bars_in"] = 0.0
        except Exception:
            feats["window_pct_change"] = 0.0
            feats["window_bars_in"] = 0.0
    else:
        feats["window_pct_change"] = 0.0
        feats["window_bars_in"] = 0.0

    # ---- Distance from nearest round number (level is asset-specific) ----
    # BTC: 1000/5000, ETH: 100/500, SOL: 10/50, XRP: 0.10/0.50, DOGE: 0.05/0.10
    price = last["close"]
    nearest_1k = round(price / round_level_1) * round_level_1 if round_level_1 > 0 else price
    feats["dist_round_1k"] = float((price - nearest_1k) / price) if price > 0 else 0.0
    nearest_5k = round(price / round_level_2) * round_level_2 if round_level_2 > 0 else price
    feats["dist_round_5k"] = float((price - nearest_5k) / price) if price > 0 else 0.0

    # ---- Asset one-hot encoding (BTC=0, ETH=1, SOL=2, XRP=3, DOGE=4) ----
    feats["is_btc"]  = 1.0 if asset_id == 0 else 0.0
    feats["is_eth"]  = 1.0 if asset_id == 1 else 0.0
    feats["is_sol"]  = 1.0 if asset_id == 2 else 0.0
    feats["is_xrp"]  = 1.0 if asset_id == 3 else 0.0
    feats["is_doge"] = 1.0 if asset_id == 4 else 0.0

    # ---- Consecutive directional bars ----
    rets = df["log_ret"].iloc[-10:].values
    up_streak = 0
    for r in reversed(rets):
        if pd.isna(r):
            break
        if r > 0:
            up_streak += 1
        else:
            break
    down_streak = 0
    for r in reversed(rets):
        if pd.isna(r):
            break
        if r < 0:
            down_streak += 1
        else:
            break
    feats["up_streak"]   = float(up_streak)
    feats["down_streak"] = float(down_streak)
    feats["momentum_10b"] = float(df["log_ret"].iloc[-10:].sum())

    # ---- Time of day (sin/cos encoding) ----
    now_utc = datetime.now(timezone.utc)
    hour_frac = now_utc.hour + now_utc.minute / 60.0
    feats["hour_sin"] = float(np.sin(2 * np.pi * hour_frac / 24))
    feats["hour_cos"] = float(np.cos(2 * np.pi * hour_frac / 24))
    feats["is_asian_session"] = 1.0 if 0 <= now_utc.hour < 8 else 0.0
    feats["is_us_session"]    = 1.0 if 13 <= now_utc.hour < 21 else 0.0

    # ---- DVOL features (key feature #1) ----
    if dvol_series is not None and not dvol_series.empty:
        current_dvol = float(dvol_series.iloc[-1])
        feats["dvol_current"] = current_dvol
        feats["dvol_7d_pct"]  = dvol_7day_percentile(dvol_series, current_dvol)
        feats["dvol_1h_chg"]  = float(
            (dvol_series.iloc[-1] - dvol_series.iloc[-60]) / dvol_series.iloc[-60]
            if len(dvol_series) >= 60 and dvol_series.iloc[-60] > 0 else 0.0
        )
    else:
        feats["dvol_current"] = 50.0   # neutral fallback
        feats["dvol_7d_pct"]  = 0.5
        feats["dvol_1h_chg"]  = 0.0

    # ---- Funding rate ----
    feats["funding_rate"] = float(funding_rate) if funding_rate is not None else 0.0

    # ---- Open interest change ----
    feats["oi_change_pct"] = float(oi_change_pct) if oi_change_pct is not None else 0.0

    # ---- Taker imbalance (real-time in live; neutral defaults in backfill) ----
    _ti = taker_imbalance if taker_imbalance is not None else {}
    feats["taker_buy_ratio_30s"]  = float(_ti.get("taker_buy_ratio_30s",  0.5))
    feats["taker_buy_ratio_60s"]  = float(_ti.get("taker_buy_ratio_60s",  0.5))
    feats["taker_buy_ratio_120s"] = float(_ti.get("taker_buy_ratio_120s", 0.5))
    feats["cvd_slope"]            = float(_ti.get("cvd_slope",            0.0))

    return feats


# Canonical feature order (must match training)
FEATURE_COLS = [
    "ema8_55_spread", "ema8_21_spread", "ema_trend_up",
    "atr_pct", "ibs",
    "rsi_1m", "rsi_15m",
    "supertrend_direction", "bars_since_st_flip",
    "rvol_5m", "rvol_30m", "rvol_4h", "rvol_ratio_5_30",
    "vwap_dev", "vol_ratio",
    "window_pct_change", "window_bars_in",
    "dist_round_1k", "dist_round_5k",
    "up_streak", "down_streak", "momentum_10b",
    "hour_sin", "hour_cos", "is_asian_session", "is_us_session",
    "dvol_current", "dvol_7d_pct", "dvol_1h_chg",
    "funding_rate", "oi_change_pct",
    "taker_buy_ratio_30s", "taker_buy_ratio_60s", "taker_buy_ratio_120s", "cvd_slope",
    "is_btc", "is_eth", "is_sol", "is_xrp", "is_doge",
]
