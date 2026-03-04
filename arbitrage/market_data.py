"""
arbitrage/market_data.py – Fetch spot and volatility for arbitrage pricing.

- Spot: yfinance for stocks (GOOGL, NVDA, etc.), OKX for BTC/crypto.
- Vol: Config default IV, or annualized historical vol from price history.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Ticker mapping: Polymarket question keywords -> yfinance symbol
TICKER_MAP = {
    "google": "GOOGL",
    "googl": "GOOGL",
    "alphabet": "GOOGL",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "tesla": "TSLA",
    "tsla": "TSLA",
    "apple": "AAPL",
    "aapl": "AAPL",
    "meta": "META",
    "amazon": "AMZN",
    "amzn": "AMZN",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "netflix": "NFLX",
    "nflx": "NFLX",
    "palantir": "PLTR",
    "pltr": "PLTR",
    "opendoor": "OPEN",
    "open": "OPEN",
    "coinbase": "COIN",
    "coin": "COIN",
    "hood": "HOOD",
    "costco": "COST",
    "cost": "COST",
}

OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker"


def _infer_ticker_from_question(question: str) -> Optional[str]:
    """Infer stock ticker from Polymarket question text."""
    q = question.lower()
    for keyword, ticker in TICKER_MAP.items():
        if keyword in q:
            return ticker
    return None


def _is_crypto_question(question: str) -> bool:
    q = question.lower()
    return "btc" in q or "bitcoin" in q or "eth" in q or "ethereum" in q or "crypto" in q


def get_spot_price(ticker_or_crypto: str) -> Optional[float]:
    """
    Fetch current spot price.

    Args:
        ticker_or_crypto: Stock ticker (GOOGL, NVDA) or 'BTC', 'ETH'.

    Returns:
        Spot price or None on failure.
    """
    t = ticker_or_crypto.upper()
    if t in ("BTC", "BITCOIN"):
        try:
            resp = requests.get(
                OKX_TICKER_URL,
                params={"instId": "BTC-USDT"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return float(data["data"][0]["last"])
        except Exception as e:
            logger.warning("OKX spot fetch failed: %s", e)
        return None

    # Stock via yfinance
    try:
        import yfinance as yf
        obj = yf.Ticker(t)
        hist = obj.history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except ImportError:
        logger.warning("yfinance not installed. pip install yfinance")
        return None
    except Exception as e:
        logger.warning("yfinance spot fetch failed for %s: %s", t, e)
        return None


def get_historical_vol(ticker_or_crypto: str, days: int = 30) -> Optional[float]:
    """Annualized volatility from log returns (stocks) or OKX candles (BTC)."""
    t = ticker_or_crypto.upper()
    if t in ("BTC", "BITCOIN"):
        try:
            resp = requests.get(
                "https://www.okx.com/api/v5/market/candles",
                params={"instId": "BTC-USDT", "bar": "1D", "limit": str(min(days, 60))},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                return None
            closes = [float(c[4]) for c in reversed(data["data"])]
            if len(closes) < 5:
                return None
            import math
            log_returns = []
            for i in range(1, len(closes)):
                log_returns.append(math.log(closes[i] / closes[i - 1]))
            std = (sum(r * r for r in log_returns) / (len(log_returns) - 1)) ** 0.5 if len(log_returns) > 1 else 0
            return std * (252 ** 0.5)  # annualize
        except Exception as e:
            logger.warning("OKX vol fetch failed: %s", e)
            return None

    try:
        import yfinance as yf
        import math
        obj = yf.Ticker(t)
        hist = obj.history(period=f"{days}d")
        if hist.empty or len(hist) < 5:
            return None
        closes = hist["Close"].tolist()
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        std = (sum(r * r for r in log_returns) / (len(log_returns) - 1)) ** 0.5 if len(log_returns) > 1 else 0
        return std * (252 ** 0.5)
    except Exception as e:
        logger.warning("yfinance vol fetch failed for %s: %s", t, e)
        return None


def _is_price_target_question(question: str) -> bool:
    """True if question describes a price-level target (not quantity, event, etc.)."""
    q = question.lower()
    keywords = ("hit ", "reach ", "above ", "below ", "dip to ", "drop to ", "worth ", " at $", ">$", "<$")
    return any(kw in q for kw in keywords) or ("$" in q and any(c.isdigit() for c in q))


def parse_barrier_and_direction(question: str) -> Tuple[Optional[float], str]:
    """
    Extract barrier/strike and event direction from question.

    Returns:
        (barrier, event_type) where event_type is "finish_above" or "finish_below".
        (None, ...) if this is not a price-target question.
        Examples: "hit $375" -> (375, "finish_above"), "dip to $215" -> (215, "finish_below").
    """
    if not _is_price_target_question(question):
        return None, "finish_above"

    q = question.lower()
    event_type = "finish_above"  # hit, reach, above, exceed
    if "dip" in q or "drop" in q or "below" in q or "fall" in q:
        event_type = "finish_below"

    # Match $375, $70k, $100,000 (price targets). Avoid ">1000 BTC" (quantity).
    m = re.search(r"\$\s*([\d,]+(?:\.[\d]+)?)\s*k?", q, re.IGNORECASE)
    if not m:
        # Fallback: number with k (70k, 100k) as price in thousands
        m = re.search(r"([\d,]+)\s*k\b", q, re.IGNORECASE)
    if not m:
        return None, event_type
    s = m.group(1).replace(",", "")
    if "k" in m.group(0).lower():
        try:
            return float(s) * 1000, event_type
        except ValueError:
            pass
    try:
        return float(s), event_type
    except ValueError:
        return None, event_type


def get_spot_and_vol(
    question: str,
    default_iv: float = 0.25,
    use_historical_vol: bool = True,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Get spot, volatility, and underlying id for a Polymarket question.

    Returns:
        (spot, sigma, ticker_or_crypto) or (None, None, None).
    """
    ticker = None
    if _is_crypto_question(question):
        ticker = "BTC"
    else:
        ticker = _infer_ticker_from_question(question)
    if not ticker:
        return None, None, None

    spot = get_spot_price(ticker)
    if spot is None:
        return None, None, ticker

    sigma = None
    if use_historical_vol:
        sigma = get_historical_vol(ticker)
    if sigma is None or sigma <= 0:
        sigma = default_iv
    return spot, sigma, ticker
