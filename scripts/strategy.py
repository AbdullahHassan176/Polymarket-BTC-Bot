"""
strategy.py  -  Signal generation for Polymarket BTC direction bets.

Modes (config.STRATEGY_MODE):
  reversal   - Pure price: bet $REVERSAL_BET_USDC when YES or NO <= REVERSAL_PRICE_THRESHOLD (e.g. 10¢). No indicators.
  momentum   - EMA breakout, follow trend.
  contrarian - Mean reversion, buy cheap side (3-35¢) when IBS extreme.
  hybrid     - Reversal (if REVERSAL_ENABLED) -> contrarian -> momentum -> fallback.

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

    # Late-window: strong move + mispriced side (only when enabled). Never enter in last 60s.
    ctx = context or {}
    min_secs = getattr(config, "ENTRY_MIN_SECS_REMAINING", 60)
    secs_left = ctx.get("secs_remaining")
    if secs_left is not None and secs_left < min_secs:
        return SKIP, _build_debug(
            indicators or {}, yes_price, no_price, SKIP,
            "no entry: %.0fs left (min %ds required)" % (secs_left, min_secs),
        )
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

    ctx = context or {}
    mode = getattr(config, "STRATEGY_MODE", "momentum")
    if mode == "ml_v2":
        return _check_ml_v2(yes_price, no_price, ctx)
    if mode == "reversal":
        return _check_reversal(yes_price, no_price, ctx)
    if mode == "contrarian":
        return _check_contrarian(indicators, yes_price, no_price, ctx)
    if mode == "hybrid":
        return _check_hybrid(indicators, yes_price, no_price, ctx)
    return _check_momentum(indicators, yes_price, no_price)


def _check_ml_v2(yes_price: float, no_price: float, context: Optional[dict] = None) -> tuple:
    """
    ML v2 strategy: LightGBM + XGBoost ensemble, hold-to-expiry.

    Only fires when model confidence >= ML_V2_CONFIDENCE_THRESHOLD.
    Ignores technical indicators — relies entirely on trained ensemble.
    Time filter: only trade during ML_V2_TRADING_HOURS_UTC.
    """
    ctx = context or {}

    # Time-of-day filter
    trading_hours = getattr(config, "ML_V2_TRADING_HOURS_UTC", [])
    if trading_hours and ctx.get("outside_trading_hours"):
        return SKIP, {"reason": "ml_v2: outside allowed trading hours", "action": SKIP,
                      "yes_price": yes_price, "no_price": no_price,
                      "ema_fast": 0, "ema_slow": 0, "close": 0, "atr_pct": 0,
                      "ibs": 0.5, "trend_up": False, "trend_down": False}

    # Seconds remaining check
    min_secs = getattr(config, "ENTRY_MIN_SECS_REMAINING", 60)
    secs_left = ctx.get("secs_remaining")
    if secs_left is not None and secs_left < min_secs:
        return SKIP, {"reason": f"ml_v2: only {secs_left:.0f}s left", "action": SKIP,
                      "yes_price": yes_price, "no_price": no_price,
                      "ema_fast": 0, "ema_slow": 0, "close": 0, "atr_pct": 0,
                      "ibs": 0.5, "trend_up": False, "trend_down": False}

    # Get prediction from ensemble
    try:
        from ml.predict_v2 import get_signal_v2, is_ready
        if not is_ready():
            return SKIP, {"reason": "ml_v2: models not ready — run train_v2.py", "action": SKIP,
                          "yes_price": yes_price, "no_price": no_price,
                          "ema_fast": 0, "ema_slow": 0, "close": 0, "atr_pct": 0,
                          "ibs": 0.5, "trend_up": False, "trend_down": False}

        df_1m = ctx.get("df_1m")
        window_start_ts = ctx.get("window_start_ts")
        threshold = getattr(config, "ML_V2_CONFIDENCE_THRESHOLD", 0.12)
        max_price = getattr(config, "ML_V2_MAX_ENTRY_PRICE", 0.58)

        action, p_up, reason, half_kelly = get_signal_v2(
            df_1m, yes_price, no_price,
            window_start_ts=window_start_ts,
            confidence_threshold=threshold,
            max_entry_price=max_price,
        )
        debug = {
            "action": action, "reason": reason,
            "yes_price": yes_price, "no_price": no_price,
            "p_up": round(p_up, 4),
            "half_kelly": round(half_kelly, 4),
            "ema_fast": 0, "ema_slow": 0, "close": 0, "atr_pct": 0,
            "ibs": 0.5, "trend_up": p_up > 0.5, "trend_down": p_up < 0.5,
        }
        return action, debug

    except Exception as exc:
        logger.exception("ml_v2 signal error: %s", exc)
        return SKIP, {"reason": f"ml_v2 error: {exc}", "action": SKIP,
                      "yes_price": yes_price, "no_price": no_price,
                      "ema_fast": 0, "ema_slow": 0, "close": 0, "atr_pct": 0,
                      "ibs": 0.5, "trend_up": False, "trend_down": False}


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
            tier="late_window",
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
            tier="late_window",
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


def _check_reversal(yes_price: float, no_price: float, context: Optional[dict] = None) -> tuple:
    """
    Multi-tier reversal: try 10c, then 15c, then 20c. Skip if secs_remaining < REVERSAL_MIN_SECS_REMAINING.
    """
    ctx = context or {}
    secs = ctx.get("secs_remaining")
    min_secs = getattr(config, "REVERSAL_MIN_SECS_REMAINING", 60)
    if secs is not None and secs < min_secs:
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            f"reversal: < {min_secs}s left (%.0fs). No entry." % (secs or 0),
        )
    # Sustained-dip filter: price must have been below threshold for MIN seconds.
    if ctx.get("dip_too_fresh"):
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            "reversal: dip too fresh — waiting for sustained low (REVERSAL_MIN_DIP_SECS)",
        )
    # Stale-dip filter: price has been below threshold too long — market likely resolving.
    if ctx.get("dip_too_stale"):
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            "reversal: dip too stale — market likely resolving, not reversing (REVERSAL_MAX_DIP_SECS)",
        )
    # SL cooldown: too many consecutive SLs, pause this asset.
    if ctx.get("sl_cooldown"):
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            "reversal: asset in SL cooldown — skipping to avoid cascade losses",
        )
    # Trading hours filter: this UTC hour is not in the asset's allowed window.
    if ctx.get("outside_trading_hours"):
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            "reversal: outside trading hours for this asset (trading_hours_utc)",
        )

    entry_mode = getattr(config, "REVERSAL_ENTRY_MODE", "single")
    # Per-asset tiers: ASSETS_CONFIG[TRADING_ASSET].reversal_entry_tiers overrides global REVERSAL_ENTRY_TIERS.
    asset_cfg = (getattr(config, "ASSETS_CONFIG", None) or {}).get(getattr(config, "TRADING_ASSET", "BTC")) or {}
    tiers = asset_cfg.get("reversal_entry_tiers") or getattr(config, "REVERSAL_ENTRY_TIERS", None) or [getattr(config, "REVERSAL_PRICE_THRESHOLD", 0.10)]
    filled_tiers = set(ctx.get("filled_entry_tiers") or [])
    enter_at_lowest_only = getattr(config, "REVERSAL_ENTER_AT_LOWEST_ONLY", True)
    # Dynamic lowest from last N windows' price paths (when enabled and available)
    if getattr(config, "REVERSAL_USE_DYNAMIC_LOWEST", False) and ctx.get("dynamic_lowest_tier") is not None:
        lowest_tier = float(ctx["dynamic_lowest_tier"])
    else:
        lowest_tier = min(tiers) if tiers else 0.10

    min_entry = getattr(config, "REVERSAL_MIN_ENTRY_PRICE", 0.05)
    # Ensure we only buy at or above min_entry (e.g. 15c); cap lowest_tier so "enter at lowest" can fire.
    lowest_tier = max(lowest_tier, min_entry)
    if entry_mode == "pyramid":
        # Block second slice if first was opened too recently (REVERSAL_MIN_SLICE_GAP_SECS).
        if ctx.get("block_add_slice") and filled_tiers:
            return SKIP, _build_debug(
                {}, yes_price, no_price, SKIP,
                f"reversal pyramid: slice gap too short — waiting before adding second tranche",
            )
        # When enter_at_lowest_only: only add when price hits the lowest tier (buy at the low).
        if enter_at_lowest_only:
            if lowest_tier in filled_tiers:
                return SKIP, _build_debug(
                    {}, yes_price, no_price, SKIP,
                    f"reversal pyramid: lowest tier %.2f already filled" % lowest_tier,
                )
            if yes_price <= lowest_tier and yes_price >= min_entry:
                logger.info("Signal BUY_YES (reversal pyramid, lowest only): YES=%.2f <= %.2f", yes_price, lowest_tier)
                debug = _build_debug({}, yes_price, no_price, BUY_YES,
                    f"reversal pyramid: YES at {yes_price:.2f} <= lowest {lowest_tier:.2f}", tier="reversal")
                debug["entry_threshold"] = lowest_tier
                debug["entry_mode"] = "pyramid"
                debug["is_add_slice"] = len(filled_tiers) > 0
                return BUY_YES, debug
            if no_price <= lowest_tier and no_price >= min_entry:
                logger.info("Signal BUY_NO (reversal pyramid, lowest only): NO=%.2f <= %.2f", no_price, lowest_tier)
                debug = _build_debug({}, yes_price, no_price, BUY_NO,
                    f"reversal pyramid: NO at {no_price:.2f} <= lowest {lowest_tier:.2f}", tier="reversal")
                debug["entry_threshold"] = lowest_tier
                debug["entry_mode"] = "pyramid"
                debug["is_add_slice"] = len(filled_tiers) > 0
                return BUY_NO, debug
            return SKIP, _build_debug(
                {}, yes_price, no_price, SKIP,
                f"reversal pyramid (lowest only): wait for %.2f (YES=%.2f NO=%.2f)" % (lowest_tier, yes_price, no_price),
            )
        for threshold in sorted(tiers, reverse=True):
            if threshold in filled_tiers:
                continue
            if yes_price <= threshold:
                if yes_price < min_entry:
                    return SKIP, _build_debug(
                        {}, yes_price, no_price, SKIP,
                        f"reversal: YES=%.2f < min_entry %.2f (skip)" % (yes_price, min_entry),
                    )
                logger.info("Signal BUY_YES (reversal pyramid): YES=%.2f <= %.2f", yes_price, threshold)
                debug = _build_debug({}, yes_price, no_price, BUY_YES,
                    f"reversal pyramid: YES at {yes_price:.2f} <= {threshold:.2f}", tier="reversal")
                debug["entry_threshold"] = threshold
                debug["entry_mode"] = "pyramid"
                debug["is_add_slice"] = len(filled_tiers) > 0
                return BUY_YES, debug
            if no_price <= threshold:
                if no_price < min_entry:
                    return SKIP, _build_debug(
                        {}, yes_price, no_price, SKIP,
                        f"reversal: NO=%.2f < min_entry %.2f (skip)" % (no_price, min_entry),
                    )
                logger.info("Signal BUY_NO (reversal pyramid): NO=%.2f <= %.2f", no_price, threshold)
                debug = _build_debug({}, yes_price, no_price, BUY_NO,
                    f"reversal pyramid: NO at {no_price:.2f} <= {threshold:.2f}", tier="reversal")
                debug["entry_threshold"] = threshold
                debug["entry_mode"] = "pyramid"
                debug["is_add_slice"] = len(filled_tiers) > 0
                return BUY_NO, debug
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            f"reversal pyramid: no new tier (filled={sorted(filled_tiers)}) YES=%.2f NO=%.2f" % (yes_price, no_price),
        )

    # Single mode: when enter_at_lowest_only, only enter when price hits the lowest tier (buy at the low).
    if entry_mode == "single" and enter_at_lowest_only:
        if yes_price <= lowest_tier and yes_price >= min_entry:
            logger.info("Signal BUY_YES (reversal, lowest only): YES=%.2f <= %.2f", yes_price, lowest_tier)
            debug = _build_debug({}, yes_price, no_price, BUY_YES,
                f"reversal: YES at {yes_price:.2f} <= lowest {lowest_tier:.2f}", tier="reversal")
            debug["entry_threshold"] = lowest_tier
            debug["entry_mode"] = "single"
            debug["is_add_slice"] = False
            return BUY_YES, debug
        if no_price <= lowest_tier and no_price >= min_entry:
            logger.info("Signal BUY_NO (reversal, lowest only): NO=%.2f <= %.2f", no_price, lowest_tier)
            debug = _build_debug({}, yes_price, no_price, BUY_NO,
                f"reversal: NO at {no_price:.2f} <= lowest {lowest_tier:.2f}", tier="reversal")
            debug["entry_threshold"] = lowest_tier
            debug["entry_mode"] = "single"
            debug["is_add_slice"] = False
            return BUY_NO, debug
        return SKIP, _build_debug(
            {}, yes_price, no_price, SKIP,
            f"reversal (lowest only): wait for %.2f (YES=%.2f NO=%.2f)" % (lowest_tier, yes_price, no_price),
        )
    for threshold in sorted(tiers):
        if yes_price <= threshold:
            if yes_price < min_entry:
                return SKIP, _build_debug(
                    {}, yes_price, no_price, SKIP,
                    f"reversal: YES=%.2f < min_entry %.2f (skip)" % (yes_price, min_entry),
                )
            logger.info("Signal BUY_YES (reversal): YES=%.2f <= %.2f", yes_price, threshold)
            debug = _build_debug({}, yes_price, no_price, BUY_YES,
                f"reversal: YES at {yes_price:.2f} <= {threshold:.2f}", tier="reversal")
            debug["entry_threshold"] = threshold
            debug["entry_mode"] = "single"
            debug["is_add_slice"] = False
            return BUY_YES, debug
        if no_price <= threshold:
            if no_price < min_entry:
                return SKIP, _build_debug(
                    {}, yes_price, no_price, SKIP,
                    f"reversal: NO=%.2f < min_entry %.2f (skip)" % (no_price, min_entry),
                )
            logger.info("Signal BUY_NO (reversal): NO=%.2f <= %.2f", no_price, threshold)
            debug = _build_debug({}, yes_price, no_price, BUY_NO,
                f"reversal: NO at {no_price:.2f} <= {threshold:.2f}", tier="reversal")
            debug["entry_threshold"] = threshold
            debug["entry_mode"] = "single"
            debug["is_add_slice"] = False
            return BUY_NO, debug
    return SKIP, _build_debug(
        {}, yes_price, no_price, SKIP,
        f"reversal: neither side <= {max(tiers):.2f} (YES=%.2f NO=%.2f)" % (yes_price, no_price),
    )


def _check_hybrid(indicators: dict, yes_price: float, no_price: float, context: Optional[dict] = None) -> tuple:
    """
    Reversal (if enabled) -> contrarian -> momentum -> fallback (if enabled).
    """
    if getattr(config, "REVERSAL_ENABLED", False):
        action, debug = _check_reversal(yes_price, no_price, context)
        if action != SKIP:
            return action, debug
    action, debug = _check_contrarian(indicators, yes_price, no_price, context or {})
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
            tier="fallback",
        )
    if ibs > ibs_high and pmin <= no_price <= pmax:
        logger.info(
            "Signal BUY_NO (fallback IBS): IBS=%.2f > %.2f, NO=%.2f in [%.2f-%.2f]",
            ibs, ibs_high, no_price, pmin, pmax,
        )
        return BUY_NO, _build_debug(
            indicators, yes_price, no_price, BUY_NO, "fallback: IBS overbought fade",
            tier="fallback",
        )

    # Optional weak-trend fallback (disabled in fallback-lite mode).
    if not getattr(config, "FALLBACK_TREND_ENABLED", True):
        return SKIP, _build_debug(
            indicators, yes_price, no_price, SKIP, "fallback: no IBS setup",
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
            tier="fallback",
        )
    if trend_down and no_ok:
        logger.info(
            "Signal BUY_NO (fallback trend): EMA down, spread=$%.0f, NO=%.2f",
            ema_spread, no_price,
        )
        return BUY_NO, _build_debug(
            indicators, yes_price, no_price, BUY_NO, "fallback: weak downtrend",
            tier="fallback",
        )

    return SKIP, _build_debug(
        indicators, yes_price, no_price, SKIP, "fallback: no IBS or trend setup",
    )


def _check_contrarian(indicators: dict, yes_price: float, no_price: float, context: Optional[dict] = None) -> tuple:
    """
    Contrarian / mispricing: buy the cheap side when mean reversion signals.
    BUY_YES when YES cheap and IBS low (oversold, expect bounce).
    BUY_NO when NO cheap and IBS high (overbought, expect pullback).
    When CONTRARIAN_USE_MODEL_FAIR_VALUE: only take bet if model-implied P exceeds market price + MIN_EDGE.
    Optional EMA filter: skip when fighting strong trend (CONTRARIAN_MAX_EMA_SPREAD_AGAINST).
    """
    atr_pct  = indicators["atr_pct"]
    ibs      = indicators.get("ibs", 0.5)
    ema_fast = indicators.get("ema_fast", 0)
    ema_slow = indicators.get("ema_slow", 0)
    ctx      = context or {}

    # Volatility filter
    if atr_pct >= config.ATR_THRESHOLD * config.ATR_SPIKE_MULTIPLIER:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "volatility spike")
    if atr_pct >= config.ATR_THRESHOLD:
        return SKIP, _build_debug(indicators, yes_price, no_price, SKIP, "ATR% too high")

    # Trend strength for "don't fight strong momentum" filter
    ema_spread  = abs(ema_fast - ema_slow)
    trend_down  = ema_fast < ema_slow
    trend_up    = ema_fast > ema_slow
    max_spread  = getattr(config, "CONTRARIAN_MAX_EMA_SPREAD_AGAINST", 0)
    block_yes   = max_spread > 0 and trend_down and ema_spread >= max_spread
    block_no    = max_spread > 0 and trend_up and ema_spread >= max_spread

    # Cheap price band: we want asymmetric payoff (buy at 10-25c)
    yes_cheap = config.CONTRARIAN_MIN_PRICE <= yes_price <= config.CONTRARIAN_MAX_PRICE
    no_cheap  = config.CONTRARIAN_MIN_PRICE <= no_price  <= config.CONTRARIAN_MAX_PRICE

    # Optional: model-as-oracle filter — only bet when model says this side is cheap
    model_ok_yes = True
    model_ok_no  = True
    if getattr(config, "CONTRARIAN_USE_MODEL_FAIR_VALUE", False):
        try:
            from btc_5m_fair_value import model_implied_p_up
            window_open = ctx.get("window_start_btc")
            secs_left   = ctx.get("secs_remaining")
            current_btc = ctx.get("current_btc") or indicators.get("close", 0)
            if window_open is not None and secs_left is not None and secs_left > 0:
                p_yes = model_implied_p_up(
                    window_open, current_btc, secs_left, atr_pct,
                    ema_fast=indicators.get("ema_fast"), ema_slow=indicators.get("ema_slow"), ibs=indicators.get("ibs"),
                )
                p_no = 1.0 - p_yes
                edge = getattr(config, "CONTRARIAN_MIN_EDGE", 0.05)
                model_ok_yes = p_yes >= (yes_price + edge)
                model_ok_no = p_no >= (no_price + edge)
                logger.debug(
                    "Model P(YES)=%.2f P(NO)=%.2f | YES=%.2f NO=%.2f | edge=%.2f",
                    p_yes, p_no, yes_price, no_price, edge,
                )
        except Exception as exc:
            logger.debug("Model fair value check skipped: %s", exc)

    # BUY_YES: IBS low (bar closed weak) = oversold, expect bounce
    # Skip if fighting strong downtrend (don't catch a falling knife)
    if ibs < config.CONTRARIAN_IBS_BOUNCE and yes_cheap and model_ok_yes and not block_yes:
        logger.info(
            "Signal BUY_YES (contrarian): IBS=%.2f < %.2f oversold, YES=%.2f cheap",
            ibs, config.CONTRARIAN_IBS_BOUNCE, yes_price,
        )
        return BUY_YES, _build_debug(indicators, yes_price, no_price, BUY_YES, "contrarian bounce", tier="contrarian")

    # BUY_NO: IBS high (bar closed strong) = overbought, expect pullback
    # Skip if fighting strong uptrend (don't fade a strong rally)
    if ibs > config.CONTRARIAN_IBS_FADE and no_cheap and model_ok_no and not block_no:
        logger.info(
            "Signal BUY_NO (contrarian): IBS=%.2f > %.2f overbought, NO=%.2f cheap",
            ibs, config.CONTRARIAN_IBS_FADE, no_price,
        )
        return BUY_NO, _build_debug(indicators, yes_price, no_price, BUY_NO, "contrarian fade", tier="contrarian")

    reason = "no contrarian setup (IBS/price)"
    if block_yes:
        reason = f"contrarian BUY_YES blocked: strong downtrend (EMA spread ${ema_spread:.0f})"
    elif block_no:
        reason = f"contrarian BUY_NO blocked: strong uptrend (EMA spread ${ema_spread:.0f})"
    elif not yes_cheap and not no_cheap:
        reason = f"no cheap side (YES=%.2f, NO=%.2f)" % (yes_price, no_price)
    elif ibs >= config.CONTRARIAN_IBS_BOUNCE and ibs <= config.CONTRARIAN_IBS_FADE:
        reason = f"IBS=%.2f not extreme" % ibs
    elif getattr(config, "CONTRARIAN_USE_MODEL_FAIR_VALUE", False) and (yes_cheap or no_cheap):
        if yes_cheap and not model_ok_yes:
            reason = "model edge too small for YES"
        elif no_cheap and not model_ok_no:
            reason = "model edge too small for NO"
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
        return BUY_YES, _build_debug(indicators, yes_price, no_price, BUY_YES, "uptrend + breakout + IBS", tier="momentum")

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
        return BUY_NO, _build_debug(indicators, yes_price, no_price, BUY_NO, "downtrend + breakdown + IBS", tier="momentum")

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
    tier: Optional[str] = None,
) -> dict:
    """Build a debug info dict for logging and dashboard display."""
    out = {
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
    if tier is not None:
        out["tier"] = tier
    return out