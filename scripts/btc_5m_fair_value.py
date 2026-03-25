"""
btc_5m_fair_value.py – Model-implied P(UP) for the active 5-minute window.

Uses Black-Scholes digital probability: P(BTC close >= window open at expiry).
Volatility is derived from ATR% (annualized via config.BTC5M_VOL_ANNUALIZATION).
Used to enhance contrarian: only buy YES/NO when the model says that side is cheap.
"""

from typing import Optional

import config

try:
    from arbitrage.fair_value import digital_probability
except ImportError:
    digital_probability = None  # type: ignore


def model_implied_p_up(
    window_open_btc: Optional[float],
    current_btc: float,
    secs_remaining: float,
    atr_pct: float,
    risk_free_rate: float = 0.0,
    ema_fast: Optional[float] = None,
    ema_slow: Optional[float] = None,
    ibs: Optional[float] = None,
) -> float:
    """
    Model-implied probability that the 5-min market resolves YES (BTC close >= window open).

    When MODEL_USE_ML=True and a trained model exists: uses sklearn classifier.
    Otherwise: uses B-S digital probability (vol from ATR%).

    Returns:
        Probability in [0, 1]. Returns 0.5 if inputs missing or model unavailable.
    """
    if getattr(config, "MODEL_USE_ML", False) and ema_fast is not None and ema_slow is not None and ibs is not None:
        try:
            from ml.direction_model import ml_p_up

            close_pct = (current_btc - window_open_btc) / window_open_btc if window_open_btc and window_open_btc > 0 else 0.0
            eth_chg = 0.0  # Optional: add context.get("eth_close_pct_change", 0) if bot fetches ETH
            return ml_p_up(ema_fast, ema_slow, atr_pct, ibs, close_pct, secs_remaining, eth_close_pct_change=eth_chg)
        except Exception:
            pass

    if digital_probability is None:
        return 0.5
    if window_open_btc is None or window_open_btc <= 0 or current_btc <= 0:
        return 0.5
    if secs_remaining <= 0:
        return 0.5
    time_years = secs_remaining / (365.0 * 24 * 3600)
    sigma_mult = getattr(config, "BTC5M_VOL_ANNUALIZATION", 50.0)
    sigma = max(0.01, atr_pct * sigma_mult)  # avoid zero vol
    try:
        p = digital_probability(
            spot=current_btc,
            strike=window_open_btc,
            time_years=time_years,
            risk_free_rate=risk_free_rate,
            sigma=sigma,
            event_type="finish_above",
        )
        return max(0.0, min(1.0, p))
    except Exception:
        return 0.5
