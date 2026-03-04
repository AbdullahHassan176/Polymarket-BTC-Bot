"""
config.py  -  Single source of truth for all bot settings.

Edit this file to tune risk limits, signal thresholds, and timing.
API credentials live in .env, not here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# TRADING MODE
# ---------------------------------------------------------------------------

# Master switch. Must be True to place real orders on Polymarket.
# Leave False during paper testing - the bot logs "PAPER" instead of ordering.
REAL_TRADING: bool = False

# Auto-redeem winning positions to USDC for reinvestment (real mode only).
# Uses polymarket-apis. Requires POL for gas (EOA).
AUTO_REDEEM_ENABLED: bool = True

# ---------------------------------------------------------------------------
# POLYMARKET CREDENTIALS  (loaded from .env)
# ---------------------------------------------------------------------------

# Your MetaMask private key (no 0x prefix). Used to sign L1 EIP-712 messages
# and derive L2 HMAC API credentials. Never paste this directly in code.
POLY_PRIVATE_KEY: str = os.getenv("POLY_PRIVATE_KEY", "")

# Your Polygon wallet address (0x...). Used as the "funder" for USDC collateral.
POLY_WALLET_ADDRESS: str = os.getenv("POLY_WALLET_ADDRESS", "")

# Optional: Anthropic API key for arbitrage checker (python -m arbitrage.run_arbitrage_check --api).
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Polymarket CLOB host.
CLOB_HOST: str = "https://clob.polymarket.com"

# Gamma API for market discovery (public, no auth).
GAMMA_API: str = "https://gamma-api.polymarket.com"

# Polygon mainnet chain ID.
CHAIN_ID: int = 137

# Wallet type: 0 = EOA (MetaMask / hardware wallet).
# Use 1 for Magic/email wallets, 2 for Gnosis Safe.
SIGNATURE_TYPE: int = 0

# ---------------------------------------------------------------------------
# MARKET TARGETING
# ---------------------------------------------------------------------------

# Keyword used to find the rolling BTC 5-minute direction markets on Polymarket.
# The bot filters active markets whose question contains this string.
MARKET_KEYWORD: str = "Bitcoin Up or Down"

# Only enter orders within this many seconds of the window opening.
ENTRY_WINDOW_SECS: int = 90

# Late-window entry: when we have ~4 min of price data, we can trade strong moves.
# Enter in the last LATE_WINDOW_SECS if BTC move exceeds LATE_MIN_MOVE_PCT.
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

# ---------------------------------------------------------------------------
# RISK MANAGEMENT  (all USD / USDC values)
# ---------------------------------------------------------------------------

# Maximum USDC to risk on a single 5-minute bet (hard cap).
# $10 allows larger stakes when compounding.
RISK_PER_TRADE_USDC: float = 10.0

# Stop trading for the day if cumulative losses exceed this amount.
MAX_DAILY_LOSS_USDC: float = 25.0

# Maximum number of bets per calendar day (UTC).
MAX_TRADES_PER_DAY: int = 12

# ---------------------------------------------------------------------------
# COMPOUNDING (reinvest profits for controlled growth)
# ---------------------------------------------------------------------------

# Starting capital for position sizing. Paper: virtual bankroll. Real: can match wallet.
BANKROLL_START_USDC: float = 50.0

# When True, trade size = BANKROLL_START + cumulative_pnl, scaled by RISK_PCT_PER_TRADE, clamped to [MIN_TRADE_USDC, RISK_PER_TRADE_USDC].
COMPOUNDING_ENABLED: bool = True

# Fraction of current equity to risk per trade when compounding (e.g. 0.10 = 10%).
RISK_PCT_PER_TRADE: float = 0.10

# Minimum stake per trade (floor when bankroll is down).
MIN_TRADE_USDC: float = 1.0

# ---------------------------------------------------------------------------
# STRATEGY MODE
# ---------------------------------------------------------------------------

# "momentum"   = EMA breakout + follow trend (original).
# "contrarian" = buy cheap side when mean reversion (nervem13/pschosuit).
# "hybrid"     = try contrarian first, fall through to momentum if no cheap setup.
STRATEGY_MODE: str = "hybrid"

# ---------------------------------------------------------------------------
# SIGNAL FILTERS (momentum mode)
# ---------------------------------------------------------------------------

# EMA periods for the trend filter on 1-minute BTC candles.
EMA_FAST: int = 10
EMA_SLOW: int = 30

# ATR% threshold. Skip trade if volatility is too high (chaotic market).
ATR_THRESHOLD: float = 0.035

# Spike guard: if ATR% > threshold * multiplier, skip entirely.
ATR_SPIKE_MULTIPLIER: float = 2.0

# Price filter: don't buy if crowd already priced in the move.
MAX_ENTRY_PRICE: float = 0.65
MIN_ENTRY_PRICE: float = 0.35

# Min $ spread between EMAs before we trust the trend (avoids chop).
MIN_EMA_SPREAD_USD: float = 25.0

# IBS (Internal Bar Strength): (close - low) / (high - low). Confirms bar closed in our direction.
# BUY_YES requires IBS > IBS_MIN_FOR_UP (bar closed in upper half).
# BUY_NO requires IBS < IBS_MAX_FOR_DOWN (bar closed in lower half).
IBS_MIN_FOR_UP: float = 0.5
IBS_MAX_FOR_DOWN: float = 0.5

# ---------------------------------------------------------------------------
# CONTRARIAN / MISPRICING (like nervem13, pschosuit)
# ---------------------------------------------------------------------------

# Only buy when our side is priced at or below this (asymmetric payoff).
CONTRARIAN_MAX_PRICE: float = 0.35

# Avoid illiquid penny markets.
CONTRARIAN_MIN_PRICE: float = 0.03

# IBS extremes for mean reversion: buy YES when IBS < 0.25 (bar closed weak, expect bounce).
CONTRARIAN_IBS_BOUNCE: float = 0.25

# Buy NO when IBS > 0.75 (bar closed strong, expect pullback).
CONTRARIAN_IBS_FADE: float = 0.75

# ---------------------------------------------------------------------------
# FALLBACK (when contrarian + momentum both SKIP)
# ---------------------------------------------------------------------------

# Enable fallback tier: IBS-only and weak trend.
FALLBACK_ENABLED: bool = True

# IBS-only: extreme IBS + price in band. BUY_YES when IBS < this.
FALLBACK_IBS_LOW: float = 0.20

# IBS-only: BUY_NO when IBS > this.
FALLBACK_IBS_HIGH: float = 0.80

# Fallback price band (wider than momentum, still reasonable payoff).
FALLBACK_PRICE_MIN: float = 0.30
FALLBACK_PRICE_MAX: float = 0.55

# Weak trend: min EMA spread for fallback (lower than momentum's 25).
FALLBACK_MIN_EMA_SPREAD_USD: float = 10.0

# ---------------------------------------------------------------------------
# CANDLE DATA  (fetched from OKX public API - no OKX credentials needed)
# ---------------------------------------------------------------------------

# OKX BTC-USDT spot ticker used to fetch candles for indicator calculation.
SPOT_TICKER: str = "BTC-USDT"

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
# 10 seconds = more checkpoints per window, catches more opportunities.
LOOP_INTERVAL_SECONDS: int = 10

# HTTP request timeout in seconds.
REQUEST_TIMEOUT: int = 10
