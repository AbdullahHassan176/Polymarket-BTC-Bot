"""
predict_v2.py — Runtime prediction using the trained v2 LightGBM + XGBoost ensemble.

Usage in bot:
    from ml.predict_v2 import get_signal_v2
    action, confidence, reason, half_kelly = get_signal_v2(df_1m, yes_price, no_price, window_start_ts)

Returns:
    action:      "BUY_YES" | "BUY_NO" | "SKIP"
    confidence:  float 0.0-1.0 (ensemble P(UP))
    reason:      str explaining the decision
    half_kelly:  float 0.0-1.0 (half-Kelly fraction of bankroll to bet)
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).parent
_lgb_model  = None
_xgb_model  = None
_models_loaded = False

# Cache for external data (refresh every N seconds to avoid hammering APIs)
_dvol_cache: Optional[pd.Series] = None
_dvol_cache_ts: float = 0
_funding_cache: Optional[float] = None
_funding_cache_ts: float = 0
_oi_cache: Optional[float] = None
_oi_cache_ts: float = 0
_CACHE_TTL_SECS = 120


def _load_models() -> bool:
    global _lgb_model, _xgb_model, _models_loaded
    if _models_loaded:
        return True
    try:
        import joblib
        lgb_path = _MODEL_DIR / "lgb_v2.pkl"
        xgb_path = _MODEL_DIR / "xgb_v2.pkl"
        if not lgb_path.exists() or not xgb_path.exists():
            logger.warning("v2 models not found at %s. Run train_v2.py first.", _MODEL_DIR)
            return False
        _lgb_model = joblib.load(lgb_path)
        _xgb_model = joblib.load(xgb_path)
        _models_loaded = True
        logger.info("Loaded v2 ensemble models (LGB + XGB)")
        return True
    except Exception as exc:
        logger.error("Failed to load v2 models: %s", exc)
        return False


def _get_external_data():
    """Fetch/cache DVOL, funding rate, OI change. Returns (dvol_series, funding_rate, oi_change)."""
    import time
    global _dvol_cache, _dvol_cache_ts, _funding_cache, _funding_cache_ts, _oi_cache, _oi_cache_ts

    now = time.time()

    # DVOL
    if now - _dvol_cache_ts > _CACHE_TTL_SECS:
        try:
            from ml.features_v2 import fetch_dvol_series
            _dvol_cache = fetch_dvol_series(lookback_mins=10_080)  # 7 days for percentile
            _dvol_cache_ts = now
        except Exception as exc:
            logger.debug("DVOL fetch failed: %s", exc)

    # Funding rate
    if now - _funding_cache_ts > _CACHE_TTL_SECS:
        try:
            from ml.features_v2 import fetch_funding_rate
            _funding_cache = fetch_funding_rate()
            _funding_cache_ts = now
        except Exception as exc:
            logger.debug("Funding rate fetch failed: %s", exc)

    # OI change
    if now - _oi_cache_ts > _CACHE_TTL_SECS:
        try:
            from ml.features_v2 import fetch_oi_change_pct
            _oi_cache = fetch_oi_change_pct()
            _oi_cache_ts = now
        except Exception as exc:
            logger.debug("OI change fetch failed: %s", exc)

    return _dvol_cache, _funding_cache, _oi_cache


_ASSET_ROUND_LEVELS = {
    "BTC":  (1000.0, 5000.0, 0),
    "ETH":  (100.0,  500.0,  1),
    "SOL":  (10.0,   50.0,   2),
    "XRP":  (0.10,   0.50,   3),
    "DOGE": (0.05,   0.10,   4),
}


def get_signal_v2(
    df_1m: pd.DataFrame,
    yes_price: float,
    no_price: float,
    window_start_ts: Optional[pd.Timestamp] = None,
    confidence_threshold: float = 0.12,
    max_entry_price: float = 0.58,
    asset: str = "BTC",
) -> Tuple[str, float, str, float]:
    """
    Get trading signal from the v2 ensemble.

    Args:
        df_1m: 1-min candle DataFrame (ts, open, high, low, close, vol), oldest first.
        yes_price: Current Polymarket YES token price (0-1).
        no_price: Current Polymarket NO token price (0-1).
        window_start_ts: UTC timestamp when current 5-min window opened.
        confidence_threshold: Minimum |P - 0.50| to trade (default 0.12 = 12 points).
        max_entry_price: Don't enter if target side already > this price.

    Returns:
        (action, p_up, reason, half_kelly)
        action:     "BUY_YES" | "BUY_NO" | "SKIP"
        p_up:       ensemble P(UP) probability (0-1)
        reason:     str explaining the decision
        half_kelly: half-Kelly fraction of bankroll suggested for this trade (0-1)
    """
    SKIP = "SKIP"
    BUY_YES = "BUY_YES"
    BUY_NO  = "BUY_NO"

    if not _load_models():
        return SKIP, 0.5, "v2 models not trained yet", 0.0

    if df_1m is None or len(df_1m) < 60:
        return SKIP, 0.5, "insufficient candle data", 0.0

    try:
        from ml.features_v2 import compute_all_features, FEATURE_COLS, fetch_taker_imbalance
        dvol_series, funding_rate, oi_change = _get_external_data()

        # Fetch real-time taker imbalance for live inference
        try:
            taker_imbalance = fetch_taker_imbalance(lookback_seconds=120)
        except Exception:
            taker_imbalance = None

        rl1, rl2, aid = _ASSET_ROUND_LEVELS.get(asset.upper(), (1000.0, 5000.0, 0))
        feats = compute_all_features(
            df_1m,
            window_start_ts=window_start_ts,
            dvol_series=dvol_series,
            funding_rate=funding_rate,
            oi_change_pct=oi_change,
            taker_imbalance=taker_imbalance,
            round_level_1=rl1,
            round_level_2=rl2,
            asset_id=aid,
        )

        if not feats:
            return SKIP, 0.5, "feature computation failed", 0.0

        # Build feature vector in canonical order
        X = np.array([[feats.get(c, 0.0) for c in FEATURE_COLS]], dtype=np.float32)

        # Ensemble prediction (simple average — beats stacking per research)
        p_lgb = float(_lgb_model.predict_proba(X)[0][1])
        p_xgb = float(_xgb_model.predict_proba(X)[0][1])
        p_up  = (p_lgb + p_xgb) / 2.0

        confidence = abs(p_up - 0.5)

        # Kelly sizing
        win_prob = p_up if p_up > 0.5 else (1.0 - p_up)
        kelly_fraction = max(0.0, 2 * win_prob - 1.0)
        half_kelly = kelly_fraction / 2.0

        dvol_info = f"DVOL_pct={feats.get('dvol_7d_pct', 0.5):.2f}"
        st_info   = f"ST_flip={int(feats.get('bars_since_st_flip', 99))}bars"
        ema_info  = f"EMA55_spread={feats.get('ema8_55_spread', 0)*100:.2f}%"

        if confidence < confidence_threshold:
            return SKIP, p_up, (
                f"ml_v2: low confidence P(UP)={p_up:.3f} (need |P-0.5|>={confidence_threshold}) "
                f"{dvol_info} {st_info} {ema_info}"
            ), half_kelly

        # Minimum edge required — model probability must exceed entry price by at least this.
        # Prevents thin-edge trades where the market has already priced in most of the move.
        # At 54.9% CV win rate: model_p=0.56, entry=0.54 → EV=+0.009 (noise, skip).
        # At 61.5% CV win rate (>10% conf): model_p=0.62, entry=0.54 → EV=+0.075 (trade).
        MIN_EDGE = 0.03

        if p_up > 0.5:
            # Model says UP — buy YES
            if yes_price > max_entry_price:
                return SKIP, p_up, f"ml_v2: BUY_YES signal but YES={yes_price:.2f} > max {max_entry_price}", half_kelly
            edge = p_up - yes_price
            if edge < MIN_EDGE:
                return SKIP, p_up, f"ml_v2: BUY_YES edge too thin ({edge:.3f} < {MIN_EDGE}) P(UP)={p_up:.3f} YES={yes_price:.2f}", half_kelly
            return BUY_YES, p_up, (
                f"ml_v2: P(UP)={p_up:.3f} edge={edge:.3f} conf={confidence:.3f} kelly={half_kelly:.3f} {dvol_info} {st_info} {ema_info}"
            ), half_kelly
        else:
            # Model says DOWN — buy NO
            if no_price > max_entry_price:
                return SKIP, p_up, f"ml_v2: BUY_NO signal but NO={no_price:.2f} > max {max_entry_price}", half_kelly
            p_down = 1.0 - p_up
            edge = p_down - no_price
            if edge < MIN_EDGE:
                return SKIP, p_up, f"ml_v2: BUY_NO edge too thin ({edge:.3f} < {MIN_EDGE}) P(DOWN)={p_down:.3f} NO={no_price:.2f}", half_kelly
            return BUY_NO, p_up, (
                f"ml_v2: P(DOWN)={p_down:.3f} edge={edge:.3f} conf={confidence:.3f} kelly={half_kelly:.3f} {dvol_info} {st_info} {ema_info}"
            ), half_kelly

    except Exception as exc:
        logger.exception("predict_v2 error: %s", exc)
        return SKIP, 0.5, f"predict_v2 error: {exc}", 0.0


def is_ready() -> bool:
    """True if both models are trained and loaded."""
    return _load_models()
