"""
data.py  -  BTC candle fetching and technical indicator calculation.

Uses OKX's public candle API (no API keys needed) to fetch BTC-USDT 1-minute
candles and compute EMA and ATR indicators. These indicators are then fed
into strategy.py to determine whether to bet UP or DOWN on Polymarket.

OKX candles endpoint is public and free - no account or API key required.
"""

import logging
import random
from datetime import datetime, timezone
from typing import Optional, Union

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OKX_TICKER_URL  = "https://www.okx.com/api/v5/market/ticker"


# ---------------------------------------------------------------------------
# CANDLE FETCHING
# ---------------------------------------------------------------------------

def fetch_candles(bar: str = None, limit: int = None) -> pd.DataFrame:
    """
    Fetch recent BTC-USDT OHLCV candles from OKX's public API.

    OKX returns candles newest-first; we reverse to oldest-first for
    rolling indicator calculations.

    Falls back to synthetic candles if the API call fails (offline testing).

    Args:
        bar:   Candle timeframe (default: config.CANDLE_BAR = "1m").
        limit: Number of candles (default: config.CANDLE_LIMIT = 100).

    Returns:
        DataFrame with columns: ts, open, high, low, close, vol.
    """
    bar   = bar   or config.CANDLE_BAR
    limit = limit or config.CANDLE_LIMIT

    try:
        resp = requests.get(
            OKX_CANDLES_URL,
            params={"instId": config.SPOT_TICKER, "bar": bar, "limit": str(limit)},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "0" or not data.get("data"):
            raise ValueError(f"OKX API error: {data.get('msg', 'no data')}")

        return _parse_candles(data["data"])

    except Exception as exc:
        logger.warning("Candle fetch failed (%s) - using synthetic fallback.", exc)
        return _generate_synthetic_candles(limit)


def _parse_candles(raw: list) -> pd.DataFrame:
    """
    Parse raw OKX candle list into a clean DataFrame.

    OKX format per candle: [ts_ms, open, high, low, close, vol, ...]
    """
    rows = []
    for candle in raw:
        try:
            rows.append({
                "ts":    pd.to_datetime(int(candle[0]), unit="ms", utc=True),
                "open":  float(candle[1]),
                "high":  float(candle[2]),
                "low":   float(candle[3]),
                "close": float(candle[4]),
                "vol":   float(candle[5]),
            })
        except (IndexError, ValueError, TypeError) as exc:
            logger.debug("Skipping malformed candle: %s", exc)
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("ts").reset_index(drop=True)   # oldest first
    return df


def _generate_synthetic_candles(limit: int, base_price: float = 70_000.0) -> pd.DataFrame:
    """
    Generate synthetic BTC-like candles for offline testing.

    Simulates a mild uptrend with realistic price levels and noise.
    """
    rows = []
    current_price = base_price
    for i in range(limit):
        change_pct = random.gauss(0.0002, 0.003)
        current_price *= (1 + change_pct)
        candle_range = current_price * random.uniform(0.001, 0.005)
        open_px  = current_price
        close_px = current_price * (1 + random.gauss(0, 0.001))
        high_px  = max(open_px, close_px) + random.uniform(0, candle_range * 0.5)
        low_px   = min(open_px, close_px) - random.uniform(0, candle_range * 0.5)
        rows.append({
            "ts":    pd.Timestamp.utcnow() - pd.Timedelta(minutes=limit - i),
            "open":  round(open_px, 2),
            "high":  round(high_px, 2),
            "low":   round(low_px, 2),
            "close": round(close_px, 2),
            "vol":   round(random.uniform(10, 200), 4),
        })
    df = pd.DataFrame(rows)
    return df.sort_values("ts").reset_index(drop=True)


# ---------------------------------------------------------------------------
# INDICATOR CALCULATION
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add EMA and ATR% indicators to a candle DataFrame.

    Indicators:
      ema_fast  - EMA over config.EMA_FAST periods (default 10)
      ema_slow  - EMA over config.EMA_SLOW periods (default 30)
      atr14     - Rolling mean of (high - low) over 14 periods
      atr_pct   - atr14 / close  (normalised volatility, threshold-comparable)
    """
    if df.empty or len(df) < config.EMA_SLOW + 5:
        logger.warning(
            "Not enough candles (%d) to compute indicators (need %d+).",
            len(df), config.EMA_SLOW + 5,
        )
        return df

    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=config.EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=config.EMA_SLOW, adjust=False).mean()
    df["atr14"]    = (df["high"] - df["low"]).rolling(window=14).mean()
    df["atr_pct"]  = df["atr14"] / df["close"]
    return df


def get_latest_indicators(df: pd.DataFrame) -> Optional[dict]:
    """
    Extract the most recent row of indicator values as a plain dict.

    Returns None if any indicator is NaN (not enough data yet).
    """
    if df.empty or "ema_fast" not in df.columns:
        return None

    last = df.iloc[-1]
    if pd.isna(last.get("ema_fast")) or pd.isna(last.get("ema_slow")) or pd.isna(last.get("atr_pct")):
        logger.warning("Indicators are NaN - not enough historical candles yet.")
        return None

    # IBS = (close - low) / (high - low): 0 = weak, 1 = strong. 0.5 = neutral.
    h, l, c = float(last["high"]), float(last["low"]), float(last["close"])
    ibs = (c - l) / (h - l) if (h - l) > 0 else 0.5

    return {
        "close":           float(last["close"]),
        "ema_fast":        float(last["ema_fast"]),
        "ema_slow":        float(last["ema_slow"]),
        "atr14":           float(last["atr14"]),
        "atr_pct":         float(last["atr_pct"]),
        "ibs":             ibs,
        "rolling_high_20": float(df["high"].rolling(20).max().iloc[-1]),
        "prev_high":       float(df["high"].iloc[-2]) if len(df) >= 2 else float(last["high"]),
        "prev_low":        float(df["low"].iloc[-2])  if len(df) >= 2 else float(last["low"]),
    }


# ---------------------------------------------------------------------------
# BTC PRICE AT TIME
# ---------------------------------------------------------------------------

def get_btc_price_at_time(
    df: pd.DataFrame, target_ts: Union[pd.Timestamp, datetime]
) -> Optional[float]:
    """
    Return the open price of the 1m candle active at target_ts.
    Used for late-window logic: BTC price at the start of the 5-min window.

    Args:
        df: Candle DataFrame (must have ts, open columns).
        target_ts: Timestamp (timezone-aware) of the desired price.

    Returns:
        Open price of the candle at that time, or None if not found.
    """
    if df.empty or "ts" not in df.columns or "open" not in df.columns:
        return None
    # Ensure timezone-aware for comparison with df["ts"]
    target = pd.Timestamp(target_ts)
    if target.tzinfo is None:
        target = target.tz_localize("UTC")
    mask = df["ts"] <= target
    if not mask.any():
        return None
    row = df.loc[mask].iloc[-1]
    return float(row["open"])


# ---------------------------------------------------------------------------
# BTC SPOT PRICE
# ---------------------------------------------------------------------------

def get_btc_spot_price() -> Optional[float]:
    """
    Fetch the current BTC/USDT spot price from OKX's public ticker endpoint.

    Used for informational logging only (Polymarket positions are in USDC,
    not BTC-denominated, so we don't need this for sizing).
    """
    try:
        resp = requests.get(
            OKX_TICKER_URL,
            params={"instId": config.SPOT_TICKER},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            return float(data["data"][0]["last"])
    except Exception as exc:
        logger.warning("Could not fetch BTC spot price: %s", exc)
    return None
