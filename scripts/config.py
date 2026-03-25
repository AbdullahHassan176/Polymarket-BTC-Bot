"""
config.py  -  Single source of truth for all bot settings.

Edit this file to tune risk limits, signal thresholds, and timing.
API credentials live in .env, not here.
"""

import os
from typing import List, Tuple
from dotenv import load_dotenv

# Load .env from project root (parent of scripts/)
_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
load_dotenv(os.path.join(_root, ".env"))

# ---------------------------------------------------------------------------
# TRADING MODE
# ---------------------------------------------------------------------------

# Master switch. Must be True to place real orders on Polymarket.
# Leave False during paper testing - the bot logs "PAPER" instead of ordering.
REAL_TRADING: bool = True

# Auto-redeem winning positions to USDC for reinvestment (real mode only).
# Uses polymarket-apis. Requires POL for gas (EOA).
AUTO_REDEEM_ENABLED: bool = True

# ---------------------------------------------------------------------------
# POLYMARKET CREDENTIALS  (loaded from .env)
# ---------------------------------------------------------------------------

# Private key: POLY_PRIVATE_KEY or PRIVATE_KEY (Oracle bot compat).
_raw_key: str = os.getenv("POLY_PRIVATE_KEY", "") or os.getenv("PRIVATE_KEY", "")
POLY_PRIVATE_KEY: str = _raw_key.strip()

# EOA mode: POLY_WALLET_ADDRESS = funder (same as signer).
# Proxy mode: PROXY_WALLET = Gnosis Safe that holds USDC; signer = EOA from private key.
POLY_WALLET_ADDRESS: str = os.getenv("POLY_WALLET_ADDRESS", "").strip()
PROXY_WALLET: str = os.getenv("PROXY_WALLET", "").strip()

# Optional explicit API creds (Oracle bot style). If unset, derived from private key.
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "").strip()
POLYMARKET_API_SECRET: str = os.getenv("POLYMARKET_API_SECRET", "").strip()
POLYMARKET_API_PASSPHRASE: str = os.getenv("POLYMARKET_API_PASSPHRASE", "").strip()

# Optional: Anthropic API key for arbitrage checker (python -m arbitrage.run_arbitrage_check --api).
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Polymarket CLOB host.
CLOB_HOST: str = "https://clob.polymarket.com"

# Gamma API for market discovery (public, no auth).
GAMMA_API: str = "https://gamma-api.polymarket.com"

# Polygon mainnet chain ID.
CHAIN_ID: int = 137

# Wallet type: 0 = EOA (MetaMask). 2 = Gnosis Safe (proxy). Set when PROXY_WALLET is set.
SIGNATURE_TYPE: int = 2 if PROXY_WALLET else 0

# ---------------------------------------------------------------------------
# MARKET TARGETING  (multi-asset 5-min "Up or Down" markets)
# ---------------------------------------------------------------------------

# Asset to trade. Set via env TRADING_ASSET (e.g. ETH, XRP, SOL, DOGE). Default BTC.
_TRADING_ASSET_RAW: str = (os.getenv("TRADING_ASSET", "BTC") or "BTC").strip().upper()
TRADING_ASSET: str = _TRADING_ASSET_RAW if _TRADING_ASSET_RAW else "BTC"

# Per-asset: Gamma search keyword, slug prefix, OKX ticker; optional reversal_entry_tiers (lowest = min of list).
# If reversal_entry_tiers is set, that asset uses it; else REVERSAL_ENTRY_TIERS is used. See docs/REVERSAL_TIERS_PER_ASSET.md.
ASSETS_CONFIG: dict = {
    # Entry tiers: pyramid enters at the higher tier first, adds at lower if price continues.
    # trading_hours_utc: list of UTC hours to allow entries. Empty = all hours allowed.
    # Derived from price path analysis across 750+ paper trades:
    #   - DOGE: works all hours, all entry prices (genuine mean-reversion edge)
    #   - BTC:  only profitable at 08-09 UTC and 21-22 UTC (European open + US afternoon)
    #   - ETH:  only enter at ≤10c (100% win rate) or restrict to hours 09/16/22-23 UTC
    #   - XRP:  enter at ≤13c (82% win) vs 15c (40% win). Hours 02-03/09/14 UTC best
    #   - SOL:  inconsistent everywhere; narrow hours only (02/06/09/16 UTC)
    "BTC":  {"keyword": "Bitcoin Up or Down",   "slug_prefix": "btc-updown-5m-",  "ticker": "BTC-USDT",
             "reversal_entry_tiers": [0.10, 0.13],  # was [0.10, 0.15]; 0.14-0.15 unprofitable
             "trading_hours_utc": [8, 9, 21, 22],   # 69% win vs 39% overall outside these
             "reversal_tp_tiers": {0.13: [(0.25, 10), (0.35, 20), (0.50, 25), (0.75, 45)],
                                    0.10: [(0.20, 10), (0.30, 15), (0.50, 35), (0.75, 40)]}},
    "ETH":  {"keyword": "Ethereum Up or Down",  "slug_prefix": "eth-updown-5m-",  "ticker": "ETH-USDT",
             "reversal_entry_tiers": [0.10, 0.12],  # 10c = 100% win; 12c = 48% win but need data. 13-15c negative.
             "trading_hours_utc": [9, 16, 22, 23],  # 72% win in good hours vs 57% overall
             "reversal_tp_tiers": {0.12: [(0.25, 10), (0.35, 50), (0.50, 40)],
                                    0.10: [(0.25, 10), (0.35, 50), (0.50, 40)]}},
    "XRP":  {"keyword": "XRP Up or Down",       "slug_prefix": "xrp-updown-5m-",  "ticker": "XRP-USDT",
             "reversal_entry_tiers": [0.10, 0.13],  # was [0.10, 0.15]; 0.15 only 40% win
             "trading_hours_utc": [2, 3, 9, 14],    # 71% win vs 54% overall outside these
             "reversal_tp_tiers": {0.13: [(0.25, 10), (0.35, 20), (0.50, 25), (0.75, 45)],
                                    0.10: [(0.20, 10), (0.30, 15), (0.50, 35), (0.75, 40)]}},
    "SOL":  {"keyword": "Solana Up or Down",    "slug_prefix": "sol-updown-5m-",  "ticker": "SOL-USDT",
             "reversal_entry_tiers": [0.10, 0.13],  # tighten same as BTC/XRP
             "trading_hours_utc": [2, 6, 9, 16],    # only hours with any positive EV
             "reversal_tp_tiers": {0.13: [(0.25, 10), (0.35, 55), (0.50, 35)],
                                    0.10: [(0.25, 10), (0.35, 55), (0.50, 35)]}},
    "DOGE": {"keyword": "Dogecoin Up or Down",  "slug_prefix": "doge-updown-5m-", "ticker": "DOGE-USDT",
             "reversal_entry_tiers": [0.10, 0.15],  # unchanged — works at all prices/hours
             "trading_hours_utc": [],               # all hours
             "reversal_tp_tiers": {0.15: [(0.25, 15), (0.35, 20), (0.50, 35), (0.75, 30)],
                                    0.10: [(0.20, 15), (0.30, 20), (0.50, 40), (0.75, 25)]}},
}
# Resolve current asset config; fallback to BTC if unknown.
_asset_cfg = ASSETS_CONFIG.get(TRADING_ASSET) or ASSETS_CONFIG["BTC"]
MARKET_KEYWORD: str = _asset_cfg["keyword"]
MARKET_SLUG_PREFIX: str = _asset_cfg["slug_prefix"]
SPOT_TICKER: str = _asset_cfg["ticker"]

# Only enter orders within this many seconds of the window opening.
ENTRY_WINDOW_SECS: int = 180

# Never enter when fewer than this many seconds left in the window (avoids buying in the last 60s).
ENTRY_MIN_SECS_REMAINING: int = 60
# Only allow entry within the first N seconds of the 5-min window (180 = first 3 min).
ENTRY_MAX_ELAPSED_SECS: int = 180

# Late-window entry: when we have ~4 min of price data, we can trade strong moves.
# Enter only when secs_remaining >= ENTRY_MIN_SECS_REMAINING (so never in last 60s).
LATE_ENTRY_ENABLED: bool = True
LATE_WINDOW_SECS: int = 90
LATE_MIN_MOVE_PCT: float = 0.003  # 0.3% move from window start required
LATE_MAX_PRICE: float = 0.90  # Don't buy if already priced above 90c (we want mispricing)

# Take profit: sell position early when best bid >= this (lock in gains).
TAKE_PROFIT_ENABLED: bool = True
TAKE_PROFIT_PRICE: float = 0.85

# Stop loss: sell position early when best bid <= this (cap loss).
STOP_LOSS_ENABLED: bool = True
STOP_LOSS_PRICE: float = 0.25

# Liquidity: cap notional per single SELL order (USDC). Splits large sells into smaller orders for thin books.
# Set to 0 to disable chunking (one order per exit). Lower (e.g. 1.5) = smaller chunks, more likely to fill in thin books.
# Sell execution: FOK (Fill-or-Kill) replaces GTC to prevent phantom PnL from unfilled limit orders.
# Each failed FOK attempt steps price down by SELL_FOK_PRICE_STEP, up to SELL_FOK_MAX_STEPS times.
SELL_FOK_PRICE_STEP: float = 0.01   # step down per retry (1 tick)
SELL_FOK_MAX_STEPS: int = 3         # max 3 retries → sell at bid, bid-0.01, bid-0.02, bid-0.03
MAX_SELL_ORDER_USDC: float = 2.5
# When a chunk fails, wait this many seconds and retry the remainder once (same tick) before returning partial.
SELL_CHUNK_RETRY_DELAY_SECS: float = 2.0

# Cheap entries: no SL when entry_price < this (hold to TP or resolution).
# Disable simple SL for entries below this price (reversal entries at 10-15c should not SL via STOP_LOSS_PRICE).
# Was 0.15 which caused immediate SL fire on 15c entries since STOP_LOSS_PRICE=0.25 > entry.
CHEAP_ENTRY_NO_SL_THRESHOLD: float = 0.25
# Very cheap: no TP/SL when entry_price <= this (hold to resolution only).
CHEAP_ENTRY_HOLD_TO_RESOLUTION_THRESHOLD: float = 0.10
# One trade per 5-min window (no re-entry in same window after TP/SL).
ONE_TRADE_PER_WINDOW: bool = True

# If we can't get bid (404) and market end was this many seconds ago, force-clear position
# to avoid blocking new trades. Prevents stuck bot when CLOB removes expired tokens.
# 300s (5 min) gives Gamma enough time to publish outcomePrices/winners before we give up.
# Gamma often lags the closed flag by 2-5 min even for fully resolved markets.
CLEAR_STALE_POSITION_AFTER_SECS: int = 300
# Before force-clear, retry resolution (Gamma can lag). Run reconcile_polymarket_history.py after.
RESOLUTION_RETRY_COUNT: int = 8
RESOLUTION_RETRY_DELAY_SECS: int = 20
# Quick retries at the stale force-clear threshold.
RESOLUTION_QUICK_RETRY_COUNT: int = 3
RESOLUTION_QUICK_RETRY_DELAY_SECS: int = 5
# How often (seconds) to poll get_market_result() after market end (throttled to avoid hammering Gamma).
RESOLUTION_POLL_INTERVAL_SECS: int = 30

# ---------------------------------------------------------------------------
# RISK MANAGEMENT  (all USD / USDC values)
# ---------------------------------------------------------------------------

# Maximum USDC to risk on a single 5-minute bet (hard cap).
# Scaled 15/50 for $15 bankroll test (from base $50).
RISK_PER_TRADE_USDC: float = 3.0
# Optional: lower cap for non-BTC assets (ETH, SOL, etc.). Unset or 0 = use RISK_PER_TRADE_USDC.
RISK_PER_TRADE_ALT_USDC: float = 0.0

# Stop trading for the day if cumulative losses exceed this amount.
MAX_DAILY_LOSS_USDC: float = 7.5

# Maximum number of bets per calendar day (UTC).
MAX_TRADES_PER_DAY: int = 1000

# Max concurrent open positions (one per market). Allows entering new window while waiting
# for previous trades to resolve, avoiding missed opportunities.
MAX_CONCURRENT_POSITIONS: int = 3

# ---------------------------------------------------------------------------
# COMPOUNDING (reinvest profits for controlled growth)
# ---------------------------------------------------------------------------

# Starting capital for position sizing. Paper: virtual bankroll. Real: can match wallet.
# Updated to match actual on-chain USDC balance as of 2026-03-19.
BANKROLL_START_USDC: float = 354.0

# When True, trade size = BANKROLL_START + cumulative_pnl, scaled by RISK_PCT_PER_TRADE, clamped to [MIN_TRADE_USDC, RISK_PER_TRADE_USDC].
COMPOUNDING_ENABLED: bool = True

# Fraction of current equity to risk per trade when compounding (e.g. 0.10 = 10%).
RISK_PCT_PER_TRADE: float = 0.10

# Minimum stake per trade (floor when bankroll is down).
MIN_TRADE_USDC: float = 0.50

# ---------------------------------------------------------------------------
# STRATEGY MODE
# ---------------------------------------------------------------------------

# "reversal"   = Buy at 10¢, sell at 50¢ (mean reversion, no indicators).
# "momentum"   = EMA breakout + follow trend (original).
# "contrarian" = buy cheap side when mean reversion.
# "hybrid"     = reversal (if enabled) -> contrarian -> momentum -> fallback.
STRATEGY_MODE: str = "ml_v2"

# ---------------------------------------------------------------------------
# ML V2 — LightGBM + XGBoost ensemble (hold-to-expiry, no spread cost on exit)
# ---------------------------------------------------------------------------

# Set STRATEGY_MODE = "ml_v2" to activate. Keep "reversal" to use existing strategy.
# When ml_v2 is active:
#   - Enters YES/NO at market price when model confidence >= ML_V2_CONFIDENCE_THRESHOLD
#   - Always holds to expiry (no TP/SL exits) — eliminates exit spread costs
#   - Only trades during ML_V2_TRADING_HOURS_UTC (Asian session by default)
ML_V2_ENABLED: bool = (STRATEGY_MODE == "ml_v2")

# |P(UP) - 0.50| must exceed this to place a bet. Research: 0.05 -> ~54.3% win, 0.12 -> ~60% win.
# Lower = more trades at moderate win rate. Raised after sufficient live data.
ML_V2_CONFIDENCE_THRESHOLD: float = 0.05

# Don't enter if the target side is already priced above this (edge is gone).
# At 54.9% CV win rate: max safe entry = 0.52 (EV = +2.9%). 0.58 = -3.1% (losing).
# Only >10% confidence (61.5% win) can profitably enter up to 0.58.
ML_V2_MAX_ENTRY_PRICE: float = 0.52

# Only trade during these UTC hours. Empty = all hours (CV shows similar accuracy all day).
ML_V2_TRADING_HOURS_UTC: list = []

# When True, always hold positions to expiry in ml_v2 mode (recommended — eliminates spread cost).
ML_V2_HOLD_TO_EXPIRY: bool = True

# When True, use Kelly criterion for position sizing (scales bet by model confidence).
ML_V2_KELLY_SIZING: bool = True

# Cap Kelly fraction at this fraction of bankroll per trade (e.g. 0.10 = 10%).
ML_V2_KELLY_MAX_FRACTION: float = 0.10

# ---------------------------------------------------------------------------
# SIGNAL FILTERS (momentum mode)
# ---------------------------------------------------------------------------

# EMA periods for the trend filter on 1-minute BTC candles.
EMA_FAST: int = 10
EMA_SLOW: int = 30

# ATR% threshold. Skip trade if volatility is too high (chaotic market).
ATR_THRESHOLD: float = 0.030

# Spike guard: if ATR% > threshold * multiplier, skip entirely.
ATR_SPIKE_MULTIPLIER: float = 2.0

# Price filter: don't buy if crowd already priced in the move.
MAX_ENTRY_PRICE: float = 0.65
MIN_ENTRY_PRICE: float = 0.35

# Min $ spread between EMAs before we trust the trend (avoids chop).
MIN_EMA_SPREAD_USD: float = 5.0

# IBS (Internal Bar Strength): (close - low) / (high - low). Confirms bar closed in our direction.
# BUY_YES requires IBS > IBS_MIN_FOR_UP (bar closed in upper half).
# BUY_NO requires IBS < IBS_MAX_FOR_DOWN (bar closed in lower half).
IBS_MIN_FOR_UP: float = 0.5
IBS_MAX_FOR_DOWN: float = 0.5

# ---------------------------------------------------------------------------
# CONTRARIAN / MISPRICING (like nervem13, pschosuit)
# ---------------------------------------------------------------------------

# Only buy when our side is priced at or below this (asymmetric payoff).
CONTRARIAN_MAX_PRICE: float = 0.20

# Avoid illiquid penny markets.
CONTRARIAN_MIN_PRICE: float = 0.03

# When True, only take contrarian bets when model-implied P(UP) or P(DOWN) is above market price + MIN_EDGE.
CONTRARIAN_USE_MODEL_FAIR_VALUE: bool = True
# Minimum edge: model prob must exceed market price by this (e.g. 0.04 = 4¢).
CONTRARIAN_MIN_EDGE: float = 0.04

# IBS extremes for mean reversion: buy YES when IBS < bounce (bar closed weak, expect bounce).
CONTRARIAN_IBS_BOUNCE: float = 0.20

# Buy NO when IBS > fade (bar closed strong, expect pullback).
CONTRARIAN_IBS_FADE: float = 0.80

# Skip contrarian when fighting strong trend (EMA spread > this in USD). Set to 0 to disable.
CONTRARIAN_MAX_EMA_SPREAD_AGAINST: float = 25.0

# ---------------------------------------------------------------------------
# FALLBACK (when contrarian + momentum both SKIP)
# ---------------------------------------------------------------------------

# Enable fallback tier: IBS-only and weak trend. Disabled after 0/9 wins in 2hr test.
FALLBACK_ENABLED: bool = False
# Enable weak-trend fallback entries (set False for fallback-IBS-only mode).
FALLBACK_TREND_ENABLED: bool = False

# IBS-only: extreme IBS + price in band. BUY_YES when IBS < this.
FALLBACK_IBS_LOW: float = 0.15

# IBS-only: BUY_NO when IBS > this.
FALLBACK_IBS_HIGH: float = 0.85

# Fallback price band (wider than momentum, still reasonable payoff).
FALLBACK_PRICE_MIN: float = 0.30
FALLBACK_PRICE_MAX: float = 0.45

# Weak trend: min EMA spread for fallback (lower than momentum's 25).
FALLBACK_MIN_EMA_SPREAD_USD: float = 10.0

# ---------------------------------------------------------------------------
# PURE REVERSAL (multi-tier entry + tiered TP)
# ---------------------------------------------------------------------------
REVERSAL_ENABLED: bool = True
# Entry mode: "pyramid" = add slices as price falls through tiers (15c first, then 10c if price continues).
_REVERSAL_ENTRY_MODE_RAW: str = os.getenv("REVERSAL_ENTRY_MODE", "pyramid").strip().lower()
REVERSAL_ENTRY_MODE: str = "pyramid" if _REVERSAL_ENTRY_MODE_RAW == "pyramid" else "single"
# Entry tiers: 15c = first tranche; 10c = second tranche if price continues falling.
# Analysis showed 93%+ of windows go to <5c avg (avg min=1.2c), so 10c is reached nearly every window.
REVERSAL_ENTRY_TIERS: List[float] = [0.10, 0.15]
# False = pyramid enters at EACH tier as price falls (15c first, 10c second). True = only at lowest tier.
REVERSAL_ENTER_AT_LOWEST_ONLY: bool = False
# Dynamic lowest: disabled (we now use explicit tiers [0.10, 0.15] per the price path analysis).
REVERSAL_USE_DYNAMIC_LOWEST: bool = False
REVERSAL_DYNAMIC_LOWEST_WINDOWS: int = 3
REVERSAL_DYNAMIC_LOWEST_TIERS: List[float] = [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]
# Pyramid allocation: 60% at 15c (first entry), 40% at 10c (second tranche if price continues).
# Blended entry on full pyramid: $5 * (0.6/0.15 + 0.4/0.10) = 24 tokens avg cost ~$0.125.
REVERSAL_PYRAMID_ALLOCATION: dict = {
    0.15: 0.6,
    0.10: 0.4,
}
# Minimum entry price: 10c (allow second pyramid tranche at 10c). Analysis shows 93%+ of windows hit <=10c.
REVERSAL_MIN_ENTRY_PRICE: float = 0.10
# Don't enter if fewer than this many seconds left in the 5-min window.
REVERSAL_MIN_SECS_REMAINING: int = 60
REVERSAL_BET_USDC: float = 5.0
# Per-entry TP: (level, pct). At 2x entry price we sell 50% to recover cost; rest sails to higher tiers.
REVERSAL_TP_BY_ENTRY: dict = {
    0.05: [(0.10, 50), (0.15, 15), (0.20, 15), (0.25, 10), (0.30, 10)],   # 50% at 10c = 2x from 5c
    0.08: [(0.12, 25), (0.16, 50), (0.20, 10), (0.25, 8), (0.30, 7)],   # 50% at 16c ~ 2x from 8c
    # 10c entry (pyramid second tranche): avg min is 1.2c so entering at 10c is late in the dip.
    # Price recovers fast; take tranches at 20c(15%)/30c(20%)/50c(40%)/75c(25%).
    0.10: [(0.20, 15), (0.30, 20), (0.50, 40), (0.75, 25)],
    0.12: [(0.18, 15), (0.24, 50), (0.30, 12), (0.35, 10), (0.40, 8), (0.50, 5)],   # 50% at 24c = 2x from 12c
    # 15c entry: price path analysis shows 99% recover to 35c+, 83% to 50c+.
    # Optimal: hold longer; take tranches at 25c(15%)/35c(20%)/50c(35%)/75c(30%).
    # EV at 35c exit = +$1.00-1.18/trade; at 50c = +$1.25-1.57/trade. DON'T exit early.
    0.15: [(0.25, 15), (0.35, 20), (0.50, 35), (0.75, 30)],
    0.18: [(0.30, 15), (0.40, 20), (0.55, 35), (0.80, 30)],
    0.20: [(0.30, 15), (0.35, 15), (0.40, 50), (0.45, 10), (0.50, 10)],   # 50% at 40c = 2x from 20c
}
REVERSAL_PRICE_THRESHOLD: float = 0.10
REVERSAL_TAKE_PROFIT_PRICE: float = 0.50
REVERSAL_TP_TIERS: List[Tuple[float, float]] = [(0.15, 14), (0.20, 14), (0.25, 14), (0.30, 14), (0.35, 10), (0.40, 10), (0.45, 10), (0.50, 14)]  # fallback
REVERSAL_TIERED_TP_ENABLED: bool = True
# Stuck-bid fallback: if bid >= min but cannot reach next TP tier for this long, sell remainder at bid.
# Lower min_bid (10c) and shorter timeout (10s) = exit at bid sooner when stuck below next TP.
# 10s = exit at bid sooner when stuck below next TP tier (reduces CLEARED_STALE).
# Sustained-dip filter: price must stay at/below entry threshold for this many seconds
# before the bot is allowed to enter. Eliminates momentary false-dip entries.
REVERSAL_MIN_DIP_SECS: int = 20
# Stale-dip filter: if price has been below threshold for longer than this, the market
# is in final resolution (not reversing). Skip entry to avoid guaranteed losses.
# Data shows 86% of SL exits are at <5c — markets that committed one direction and never recovered.
REVERSAL_MAX_DIP_SECS: int = 90
# Consecutive SL cooldown: after this many SLs in a row, pause entry on this asset
# for REVERSAL_SL_COOLDOWN_SECS seconds. Time-based so it works equally for
# high-frequency assets (DOGE ~8/hr) and low-frequency ones (BTC/SOL ~1/hr).
REVERSAL_CONSECUTIVE_SL_LIMIT: int = 3
REVERSAL_SL_COOLDOWN_SECS: int = 1800  # 30 minutes
# Pyramid slice gap: min seconds between first and second tranche.
# Prevents instant double-entry when the market is already at the low level on first observation.
REVERSAL_MIN_SLICE_GAP_SECS: int = 45
# Minimum seconds to hold before SL/stuck-bid can fire (gives reversal time to manifest).
# Price path analysis: median recovery to 25c is 22-40s; recovery to 35c is 40-74s.
REVERSAL_HOLD_MIN_SECS: int = 30
# Stuck-bid: raised from 10s to 45s so we don't exit before the reversal has time to play out.
REVERSAL_STUCK_BID_TIMEOUT_SECS: int = 45
REVERSAL_STUCK_FALLBACK_MIN_BID: float = 0.10
REVERSAL_STUCK_FALLBACK_ENABLED: bool = True
# In the last N seconds of the window, exit at current bid (TP if bid >= entry, else SL) so we don't hold to resolution.
# 60s = full last minute to exit at bid, reducing CLEARED_STALE.
REVERSAL_EXIT_AT_MARKET_LAST_SECS: int = 60
# Max seconds to hold a reversal position; force exit at bid when secs_remaining <= (300 - this). 180 = exit in last 2 min if no TP.
# 0 = disabled (only use EXIT_AT_MARKET_LAST_SECS). Idea from Oracle bot MAX_POSITION_DURATION_SECONDS.
# Max position duration: 240s = exit at 60s remaining, same as EXIT_AT_MARKET_LAST_SECS.
# Gives full window to capture the reversal via tiered TP.
REVERSAL_MAX_POSITION_DURATION_SECS: int = 240
# Exit mode: "tiered" = tiered TP (25-35c) + stuck fallback; "lock_50" / "lock_100" = close at 50% or 100% profit.
REVERSAL_EXIT_MODE: str = "tiered"
# Log bid samples during monitoring for tiered TP analysis. See analyze_reversal_tiers.py.
LOG_REVERSAL_BID_TRACE: bool = True

# ---------------------------------------------------------------------------
# 5-MIN MODEL FAIR VALUE  (for contrarian "oracle" filter)
# ---------------------------------------------------------------------------
# ATR% to annualized volatility multiplier for Black-Scholes digital P(UP).
# Tune so model_implied_p_up is sensible (e.g. ~0.5 when spot ≈ window open).
BTC5M_VOL_ANNUALIZATION: float = 50.0

# Global model EV gate (profitability-first): only trade if model edge clears costs.
# net_edge = model_prob(direction) - entry_price - MODEL_EV_COST_BUFFER
# Trade only if net_edge >= MODEL_EV_MIN_EDGE.
MODEL_EV_GATE_ENABLED: bool = False
MODEL_EV_MIN_EDGE: float = 0.03
MODEL_EV_COST_BUFFER: float = 0.015

# Use ML classifier (sklearn RandomForest) instead of B-S when trained. See ml/ and docs/ML_IMPROVEMENTS_FROM_RESEARCH.md.
MODEL_USE_ML: bool = False

# ---------------------------------------------------------------------------
# CANDLE DATA  (fetched from OKX public API - no OKX credentials needed)
# ---------------------------------------------------------------------------

# OKX spot ticker for candles (set from TRADING_ASSET: BTC-USDT, ETH-USDT, etc.).

# Candle timeframe. 1m gives a new candle every minute, sufficient for
# evaluating the signal at the start of each 5-minute window.
CANDLE_BAR: str = "1m"

# Number of candles to fetch. Must be >= EMA_SLOW + buffer for valid indicators.
CANDLE_LIMIT: int = 100

# ---------------------------------------------------------------------------
# ARBITRAGE LOOP (Polymarket vs options fair value)
# ---------------------------------------------------------------------------

# Search queries for Gamma API to find arbitrage-candidate markets.
ARBITRAGE_SEARCH_QUERIES: list = [
    "Amazon hit",
    "Amazon finish",
    "NVIDIA hit",
    "NVIDIA finish",
    "Google hit",
    "Google finish",
    "Meta finish",
    "Tesla finish",
    "Apple finish",
    "Palantir finish",
    "Netflix hit",
    "Netflix finish",
    "Microsoft finish",
    "Opendoor finish",
]

# Min mispricing to trade: buy Yes when polymarket_yes < fair_value - this (e.g. 0.05 = 5%).
ARBITRAGE_MIN_EDGE: float = 0.05

# Default IV if historical vol unavailable.
ARBITRAGE_DEFAULT_IV: float = 0.25

# Risk-free rate for fair value (e.g. 0.045 = 4.5%).
ARBITRAGE_RISK_FREE_RATE: float = 0.045

# Max USDC per arbitrage bet.
ARBITRAGE_RISK_PER_TRADE_USDC: float = 10.0

# Max arbitrage positions open at once.
ARBITRAGE_MAX_OPEN_POSITIONS: int = 3

# Paper trading: starting balance and exit rules.
ARBITRAGE_PAPER_STARTING_BALANCE_USDC: float = 50.0
ARBITRAGE_PAPER_TAKE_PROFIT: float = 0.85   # Close Yes position when mid >= 85c (lock profit).
ARBITRAGE_PAPER_STOP_LOSS: float = 0.25    # Close when mid <= 25c (cap loss).

# ---------------------------------------------------------------------------
# BOT LOOP TIMING
# ---------------------------------------------------------------------------

# How often (seconds) the main loop checks for a new market window.
LOOP_INTERVAL_SECONDS: int = 5

# Paper run with 1s price path: set by run_paper_with_price_logging (env PAPER_RUN_DIR). When set, paths go there.
PAPER_RUN_DIR: str = os.getenv("PAPER_RUN_DIR", "").strip()
# Log price path every loop tick when True (use with LOOP_INTERVAL_SECONDS=1 for per-second data).
LOG_PRICE_PATH: bool = bool(os.getenv("LOG_PRICE_PATH", "").strip().lower() in ("1", "true", "yes"))

# HTTP request timeout in seconds.
REQUEST_TIMEOUT: int = 10
