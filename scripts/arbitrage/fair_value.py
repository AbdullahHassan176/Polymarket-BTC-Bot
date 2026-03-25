"""
arbitrage/fair_value.py – Risk-neutral fair value for binary/digital events.

Implements Black-Scholes terminal digital probability (finish above/below)
and barrier-hitting probability. Used by the arbitrage loop to compare
Polymarket Yes price with option-implied fair value.
"""

from typing import Literal
import math

try:
    from scipy.stats import norm
except ImportError:
    norm = None  # type: ignore


def _norm_cdf(x: float) -> float:
    """CDF of standard normal; fallback if scipy missing."""
    if norm is not None:
        return float(norm.cdf(x))
    # Abramowitz & Stegun approximation
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    if x < 0:
        return 1.0 - _norm_cdf(-x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2)
    return y


def digital_probability(
    spot: float,
    strike: float,
    time_years: float,
    risk_free_rate: float,
    sigma: float,
    dividend_yield: float = 0.0,
    event_type: Literal["finish_above", "finish_below"] = "finish_above",
) -> float:
    """
    Risk-neutral probability of underlying finishing above (or below) strike at expiry.

    Uses Black-Scholes N(d2) for call digital, N(-d2) for put digital.
    Returns undiscounted probability (0–1); for discounted fair value multiply by exp(-r*T).

    Args:
        spot:        Current underlying price.
        strike:      Barrier/strike level.
        time_years:  Time to expiry in years.
        risk_free_rate: Risk-free rate (e.g. 0.05 for 5%).
        sigma:       Volatility (e.g. 0.25 for 25%).
        dividend_yield: Continuous dividend yield (default 0).
        event_type:  "finish_above" (call digital) or "finish_below" (put digital).

    Returns:
        Probability in [0, 1]. For Polymarket Yes price, use finish_above or finish_below
        depending on how the market resolves.
    """
    if spot <= 0 or strike <= 0 or time_years <= 0 or sigma <= 0:
        return 0.0
    # μ = r - q - ½σ²; d2 = [ln(S/K) + (r - q - ½σ²)T] / (σ√T) = [ln(S/K) + μT] / (σ√T)
    mu = risk_free_rate - dividend_yield - 0.5 * sigma * sigma
    sqrt_t = math.sqrt(time_years)
    d2 = (math.log(spot / strike) + mu * time_years) / (sigma * sqrt_t)
    if event_type == "finish_above":
        p = _norm_cdf(d2)
    else:
        p = _norm_cdf(-d2)
    return max(0.0, min(1.0, p))


def discounted_fair_value(
    spot: float,
    strike: float,
    time_years: float,
    risk_free_rate: float,
    sigma: float,
    dividend_yield: float = 0.0,
    event_type: Literal["finish_above", "finish_below"] = "finish_above",
) -> float:
    """
    Discounted fair value of a binary/digital option (present value of $1 if event occurs).

    This is the theoretical Polymarket Yes price in risk-neutral terms.
    """
    p = digital_probability(
        spot, strike, time_years, risk_free_rate, sigma, dividend_yield, event_type
    )
    discount = math.exp(-risk_free_rate * time_years)
    return p * discount
