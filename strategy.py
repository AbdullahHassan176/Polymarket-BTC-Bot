"""
strategy.py  -  Signal generation for Polymarket BTC direction bets.

Three modes (config.STRATEGY_MODE):
  momentum   - EMA breakout, follow trend.
  contrarian - Mean reversion, buy cheap side (3-35¢) when IBS extreme.
  hybrid     - Try contrarian first; if no cheap setup, use momentum (best of both).

Late-window: When in last 90s, trade strong BTC moves if market misprices (YES/NO < 90c).
Fallback: When hybrid (contrarian+momentum) both SKIP, try IBS-only and weak trend.
"""

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

BUY_YES = "BUY_YES"
BUY_NO  = "BUY_NO"
SKIP    = "SKIP"


def check_signal(
    indicators: dict,
    yes_price: float,
    no_price: float,
    context: Optional[dict] = None,
) -> tuple:
    """
    Evaluate signal to decide action.

    Args:
        indicators:  Dict from data.get_latest_indicators().
        yes_price:   Current mid-price of YES (UP) token (0.0 - 1.0).
        no_price:    Current mid-price of NO (DOWN) token (0.0 - 1.0).
        context:     Optional dict with in_late_window, window_start_btc, current_btc.

    Returns:
        Tuple (action: str, debug_info: dict).
    """
    if not indicators:
        return SKIP, {"reason": "no indicators available"}

    # Late-window: strong move + mispriced side (only when enabled)
    ctx = context or {}
    if ctx.get("in_late_window") and getattr(config, "LATE_ENTRY_ENABLED", False):
        action, debug = _check_late_window(
            ctx.get("window_start_btc"),
            ctx.get("current_btc"),
            yes_price,
            no_price,
            indicators,
        )
        if action != SKIP:
            return action, debug
        # Late-window returned SKIP - fall through to hybrid (and fallback).

    mode = getattr(config, "STRATEGY_MODE", "momentum")
    if mode == "contrarian":
        return _check_contrarian(indicators, yes_price, no_price)
    if mode == "hybrid":
        return _check_hybrid(indicators, yes_price, no_price)
    return _check_momentum(indicators, yes_price, no_price)


def _check_late_window(
    window_start_btc: Optional[float],
    current_btc: Optional[float],
    yes_price: float,
    no_price: float,
    indicators: dict,
) -> tuple:
    """
    Late-window: when we have ~4 min of data, trade strong moves if market misprices.

    BUY_YES when BTC move > 0.3% and YES <= 90c.
    BUY_NO when BTC move < -0.3% and NO <= 90c.
    """
    if window_start_btc is None or current_btc is None or window_start_btc <= 0:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "late: no window_start_btc")

    move_pct = (current_btc - window_start_btc) / window_start_btc
    min_move = getattr(config, "LATE_MIN_MOVE_PCT", 0.003)
    max_price = getattr(config, "LATE_MAX_PRICE", 0.90)

    # BUY_YES: strong up move, YES not yet priced to 90c+
    if move_pct >= min_move and yes_price <= max_price:
        logger.info(
            "Signal BUY_YES (late): move=%.3f%% >= %.3f%%, YES=%.2f <= %.2f",
            move_pct * 100, min_move * 100, yes_price, max_price,
        )
        return BUY_YES, _build_debug(
            indicators, yes_price, no_price, BUY_YES,
            f"late window: move={move_pct*100:.3f}%, YES mispriced",
        )

    # BUY_NO: strong down move, NO not yet priced to 90c+
    if move_pct <= -min_move and no_price <= max_price:
        logger.info(
            "Signal BUY_NO (late): move=%.3f%% <= -%.3f%%, NO=%.2f <= %.2f",
            move_pct * 100, min_move * 100, no_price, max_price,
        )
        return BUY_NO, _build_debug(
            indicators, yes_price, no_price, BUY_NO,
            f"late window: move={move_pct*100:.3f}%, NO mispriced",
        )

    reason = "late: "
    if abs(move_pct) < min_move:
        reason += f"move {move_pct*100:.3f}% too small (need {min_move*100:.1f}%)"
    elif move_pct >= min_move and yes_price > max_price:
        reason += f"YES already {yes_price:.2f} (max {max_price})"
    elif move_pct <= -min_move and no_price > max_price:
        reason += f"NO already {no_price:.2f} (max {max_price})"
    else:
        reason += "no setup"
    return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)


def _check_hybrid(indicators: dict, yes_price: float, no_price: float) -> tuple:
    """
    Contrarian -> momentum -> fallback (if enabled).
    """
    action, debug = _check_contrarian(indicators, yes_price, no_price)
    if action != SKIP:
        return action, debug
    action, debug = _check_momentum(indicators, yes_price, no_price)
    if action != SKIP:
        return action, debug
    if getattr(config, "FALLBACK_ENABLED", False):
        return _check_fallback(indicators, yes_price, no_price)
    return action, debug


def _check_fallback(indicators: dict, yes_price: float, no_price: float) -> tuple:
    """
    Fallback when contrarian + momentum both SKIP.
    IBS-only: extreme IBS + price in band.
    Weak trend: EMA direction + relaxed spread, no breakout required.
    """
    atr_pct  = indicators["atr_pct"]
    ibs      = indicators.get("ibs", 0.5)
    ema_fast = indicators.get("ema_fast", 0)
    ema_slow = indicators.get("ema_slow", 0)

    # Volatility filter
    if atr_pct >= config.ATR_THRESHOLD * config.ATR_SPIKE_MULTIPLIER:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "fallback: volatility spike")
    if atr_pct >= config.ATR_THRESHOLD:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "fallback: ATR% too high")

    pmin = getattr(config, "FALLBACK_PRICE_MIN", 0.30)
    pmax = getattr(config, "FALLBACK_PRICE_MAX", 0.55)
    ibs_low  = getattr(config, "FALLBACK_IBS_LOW", 0.20)
    ibs_high = getattr(config, "FALLBACK_IBS_HIGH", 0.80)

    # IBS-only: extreme IBS + price in band
    if ibs < ibs_low and pmin <= yes_price <= pmax:
        logger.info(
            "Signal BUY_YES (fallback IBS): IBS=%.2f < %.2f, YES=%.2f in [%.2f-%.2f]",
            ibs, ibs_low, yes_price, pmin, pmax,
        )
        return BUY_YES, _build_debug(
            indicators, yes_price, no_price, BUY_YES, "fallback: IBS oversold bounce",
        )
    if ibs > ibs_high and pmin <= no_price <= pmax:
        logger.info(
            "Signal BUY_NO (fallback IBS): IBS=%.2f > %.2f, NO=%.2f in [%.2f-%.2f]",
            ibs, ibs_high, no_price, pmin, pmax,
        )
        return BUY_NO, _build_debug(
            indicators, yes_price, no_price, BUY_NO, "fallback: IBS overbought fade",
        )

    # Weak trend: EMA direction, relaxed spread, no breakout
    ema_spread = abs(ema_fast - ema_slow)
    min_spread = getattr(config, "FALLBACK_MIN_EMA_SPREAD_USD", 10.0)
    if ema_spread < min_spread:
        return SKIP, _build_debug(
            indicators, yes_price, no_price, SKIP,
            f"fallback: EMA spread ${ema_spread:.0f} < ${min_spread:.0f}",
        )

    trend_up   = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow
    yes_ok = pmin <= yes_price <= pmax
    no_ok  = pmin <= no_price <= pmax

    if trend_up and yes_ok:
        logger.info(
            "Signal BUY_YES (fallback trend): EMA up, spread=$%.0f, YES=%.2f",
            ema_spread, yes_price,
        )
        return BUY_YES, _build_debug(
            indicators, yes_price, no_price, BUY_YES, "fallback: weak uptrend",
        )
    if trend_down and no_ok:
        logger.info(
            "Signal BUY_NO (fallback trend): EMA down, spread=$%.0f, NO=%.2f",
            ema_spread, no_price,
        )
        return BUY_NO, _build_debug(
            indicators, yes_price, no_price, BUY_NO, "fallback: weak downtrend",
        )

    return SKIP, _build_debug(
        indicators, yes_price, no_price, SKIP, "fallback: no IBS or trend setup",
    )


def _check_contrarian(indicators: dict, yes_price: float, no_price: float) -> tuple:
    """
    Contrarian / mispricing: buy the cheap side when mean reversion signals.
    BUY_YES when YES cheap and IBS low (oversold, expect bounce).
    BUY_NO when NO cheap and IBS high (overbought, expect pullback).
    """
    atr_pct = indicators["atr_pct"]
    ibs     = indicators.get("ibs", 0.5)

    # Volatility filter
    if atr_pct >= config.ATR_THRESHOLD * config.ATR_SPIKE_MULTIPLIER:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "volatility spike")
    if atr_pct >= config.ATR_THRESHOLD:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "ATR% too high")

    # Cheap price band: we want asymmetric payoff (buy at 10-35c)
    yes_cheap = config.CONTRARIAN_MIN_PRICE <= yes_price <= config.CONTRARIAN_MAX_PRICE
    no_cheap  = config.CONTRARIAN_MIN_PRICE <= no_price  <= config.CONTRARIAN_MAX_PRICE

    # BUY_YES: IBS low (bar closed weak) = oversold, expect bounce
    if ibs < config.CONTRARIAN_IBS_BOUNCE and yes_cheap:
        logger.info(
            "Signal BUY_YES (contrarian): IBS=%.2f < %.2f oversold, YES=%.2f cheap",
            ibs, config.CONTRARIAN_IBS_BOUNCE, yes_price,
        )
        return BUY_YES, _build_debug(indicators, yes_price, no_price, BUY_YES, "contrarian bounce")

    # BUY_NO: IBS high (bar closed strong) = overbought, expect pullback
    if ibs > config.CONTRARIAN_IBS_FADE and no_cheap:
        logger.info(
            "Signal BUY_NO (contrarian): IBS=%.2f > %.2f overbought, NO=%.2f cheap",
            ibs, config.CONTRARIAN_IBS_FADE, no_price,
        )
        return BUY_NO, _build_debug(indicators, yes_price, no_price, BUY_NO, "contrarian fade")

    reason = "no contrarian setup (IBS/price)"
    if not yes_cheap and not no_cheap:
        reason = f"no cheap side (YES=%.2f, NO=%.2f)" % (yes_price, no_price)
    elif ibs >= config.CONTRARIAN_IBS_BOUNCE and ibs <= config.CONTRARIAN_IBS_FADE:
        reason = f"IBS=%.2f not extreme" % ibs
    return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)


def _check_momentum(indicators: dict, yes_price: float, no_price: float) -> tuple:
    """Original momentum: EMA breakout, follow trend."""
    ema_fast     = indicators["ema_fast"]
    ema_slow     = indicators["ema_slow"]
    close        = indicators["close"]
    atr_pct      = indicators["atr_pct"]
    rolling_high = indicators["rolling_high_20"]
    prev_high    = indicators["prev_high"]
    prev_low     = indicators["prev_low"]
    ibs          = indicators.get("ibs", 0.5)

    atr_ok   = atr_pct < config.ATR_THRESHOLD
    spike_ok = atr_pct < (config.ATR_THRESHOLD * config.ATR_SPIKE_MULTIPLIER)
    if not spike_ok:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "volatility spike")
    if not atr_ok:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "ATR% above threshold")

    ema_spread = abs(ema_fast - ema_slow)
    if ema_spread < config.MIN_EMA_SPREAD_USD:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP,
            f"EMAs too close (${ema_spread:.0f} < ${config.MIN_EMA_SPREAD_USD:.0f})")

    # --- Direction detection ---
    trend_up     = ema_fast > ema_slow
    trend_down   = ema_fast < ema_slow
    breakout_up  = close > prev_high or close > rolling_high
    breakout_down = close < prev_low
    up_signal    = trend_up and breakout_up
    down_signal  = trend_down and breakout_down

    # Price filter (momentum)
    # If yes_price > MAX_ENTRY_PRICE, the crowd already sees UP as likely - edge is gone.
    # If yes_price < MIN_ENTRY_PRICE, the crowd strongly disagrees - too risky.
    yes_price_ok = config.MIN_ENTRY_PRICE <= yes_price <= config.MAX_ENTRY_PRICE
    no_price_ok  = config.MIN_ENTRY_PRICE <= no_price  <= config.MAX_ENTRY_PRICE

    if up_signal:
        if not yes_price_ok:
            reason = (
                f"UP signal but YES price ({yes_price:.2f}) out of range "
                f"[{config.MIN_ENTRY_PRICE}-{config.MAX_ENTRY_PRICE}]"
            )
            logger.info("Signal SKIP: %s", reason)
            return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)
        if ibs <= config.IBS_MIN_FOR_UP:
            reason = f"UP signal but IBS={ibs:.2f} <= {config.IBS_MIN_FOR_UP} - bar closed weak"
            logger.info("Signal SKIP: %s", reason)
            return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)

        logger.info(
            "Signal BUY_YES: EMA_F=%.1f > EMA_S=%.1f, close=%.1f, "
            "breakout=True, ATR%%=%.3f%%, IBS=%.2f, YES price=%.2f",
            ema_fast, ema_slow, close, atr_pct * 100, ibs, yes_price,
        )
        return BUY_YES, _build_debug(indicators, yes_price, no_price, BUY_YES, "uptrend + breakout + IBS")

    if down_signal:
        if not no_price_ok:
            reason = (
                f"DOWN signal but NO price ({no_price:.2f}) out of range "
                f"[{config.MIN_ENTRY_PRICE}-{config.MAX_ENTRY_PRICE}]"
            )
            logger.info("Signal SKIP: %s", reason)
            return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)
        if ibs >= config.IBS_MAX_FOR_DOWN:
            reason = f"DOWN signal but IBS={ibs:.2f} >= {config.IBS_MAX_FOR_DOWN} - bar closed strong"
            logger.info("Signal SKIP: %s", reason)
            return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)

        logger.info(
            "Signal BUY_NO: EMA_F=%.1f < EMA_S=%.1f, close=%.1f, "
            "breakout_down=True, ATR%%=%.3f%%, IBS=%.2f, NO price=%.2f",
            ema_fast, ema_slow, close, atr_pct * 100, ibs, no_price,
        )
        return BUY_NO, _build_debug(indicators, yes_price, no_price, BUY_NO, "downtrend + breakdown + IBS")

    # No clear signal
    reasons = []
    if not trend_up and not trend_down:
        reasons.append("EMAs are flat/crossing")
    elif trend_up and not breakout_up:
        reasons.append("uptrend but no breakout")
    elif trend_down and not breakout_down:
        reasons.append("downtrend but no breakdown")
    reason = "; ".join(reasons) or "no clear direction"

    logger.debug("Signal SKIP: %s", reason)
    return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, reason)


def _build_debug(
    indicators: dict,
    yes_price: float,
    no_price: float,
    action: str,
    reason: str,
) -> dict:
    """Build a debug info dict for logging and dashboard display."""
    return {
        "action":       action,
        "reason":       reason,
        "ema_fast":     round(indicators.get("ema_fast", 0), 2),
        "ema_slow":     round(indicators.get("ema_slow", 0), 2),
        "close":        round(indicators.get("close", 0), 2),
        "atr_pct":      round(indicators.get("atr_pct", 0) * 100, 3),
        "ibs":          round(indicators.get("ibs", 0.5), 2),
        "yes_price":    yes_price,
        "no_price":     no_price,
        "trend_up":     indicators.get("ema_fast", 0) > indicators.get("ema_slow", 0),
        "trend_down":   indicators.get("ema_fast", 0) < indicators.get("ema_slow", 0),
    }
