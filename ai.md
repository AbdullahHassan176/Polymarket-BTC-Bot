# Polymarket BTC 5-Minute Direction Bot

## Purpose
Automated paper/real betting bot targeting Polymarket's rolling "Bitcoin Up or Down - 5 Minutes" binary markets. Uses BTC momentum signals (EMA + ATR) from OKX public candle data to bet YES (UP) or NO (DOWN) on each 5-minute BTC price window.

## Key Technologies
- **Python 3.9+** with `py-clob-client` for Polymarket order placement
- **Polygon blockchain** (chain_id=137) via EOA wallet (MetaMask)
- **OKX public candle API** for BTC-USDT 1m OHLCV data (no OKX account needed)
- **Polymarket Gamma API** for market discovery (public, no auth)
- **Polymarket CLOB API** for orderbook prices and order placement (L1+L2 auth)

## Architecture
```
bot.py -> polymarket_client.py  (find market, get prices, place orders)
       -> data.py               (OKX candles + EMA/ATR indicators)
       -> strategy.py           (BUY_YES / BUY_NO / SKIP)
       -> execution.py          (paper_enter, real_enter, record_outcome)
       -> risk.py               (daily limits, state.json)
```

## Directory Structure
```
Polymarket-BTC-Bot/
  bot.py                # Main loop
  polymarket_client.py  # API wrapper (Gamma + CLOB)
  data.py               # Candle fetch + indicators
  strategy.py           # Signal logic
  execution.py          # Order entry + outcome recording
  risk.py               # RiskManager + state.json
  config.py             # All settings
  .env                  # Private key + wallet address (never commit)
  .env.example          # Template for .env
  requirements.txt
  .gitignore
  logs/bot.log
  logs/trades.csv
  state.json
```

## Authentication
Two-level auth via `py-clob-client`:
- **L1**: Private key signs EIP-712 typed messages (proves wallet ownership)
- **L2**: Derived HMAC API credentials for each trading request
- Wallet type: EOA (`signature_type=0`) for MetaMask
- Needs USDC.e (collateral) and POL (gas) on Polygon mainnet

## Signal Logic (strategy.py)
**Three modes** via `config.STRATEGY_MODE`:
- **momentum**: EMA breakout. Price band 35–65¢.
- **contrarian**: buy cheap side (3–20¢) when IBS extreme; model fair-value filter (when enabled) + optional EMA filter (don't fight strong trend).
- **hybrid** (default): contrarian → momentum → fallback (IBS-only, weak trend).

## Market Lifecycle
- Each 5-min window is a NEW Polymarket market with new token IDs
- Bot discovers active market via Gamma API each loop
- Evaluates signals throughout full 5-min window (loop every 10s); late window = strong move + mispricing
- Position auto-resolves via Chainlink oracle - no manual exit needed
- Bot polls `is_market_closed()` and records WIN/LOSS after resolution

## Risk Controls
- `RISK_PER_TRADE_USDC` — max USDC per bet (hard cap)
- `MAX_DAILY_LOSS_USDC` — stop for the day after this loss
- `MAX_TRADES_PER_DAY` — at most N bets per UTC day
- **Compounding**: `COMPOUNDING_ENABLED`, `BANKROLL_START_USDC`, `RISK_PCT_PER_TRADE`, `MIN_TRADE_USDC`. Size = (bankroll + cumulative_pnl) * pct, clamped to [MIN, RISK_PER_TRADE_USDC]. `cumulative_pnl_usdc` in state (not reset at midnight).
- **Take profit / stop loss**: optional early exit (TP/SL). Outcome logged as "TP" or "SL".
- Kill switch: create `STOP_BOT.txt` to stop gracefully

## Running
```powershell
# Setup
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then fill in your private key and wallet address

# Paper trading (safe, no real money)
python bot.py --paper

# Single test iteration
python bot.py --paper --once

# Real trading (set REAL_TRADING=True in config.py first)
python bot.py --real
```

## Key Config (config.py)
- `REAL_TRADING` — master switch, default False
- `ENTRY_WINDOW_SECS` — how long after window open to allow entry (default 90)
- `MAX_ENTRY_PRICE / MIN_ENTRY_PRICE` — price range filter for YES/NO tokens
- `EMA_FAST / EMA_SLOW` — momentum signal periods (default 10/30 on 1m candles)

## Notes for Future AI Assistants
- Do not mix state with the OKX bot (separate folder, separate state.json)
- `polymarket_client.py` wraps `py-clob-client`; never import `ClobClient` directly elsewhere
- Polymarket token IDs change every 5 minutes (new market = new tokens)
- Always check `REAL_TRADING=False` before adding any auto-execution logic
- See `docs/THEORY_AND_IMPLEMENTATION.md` for full theory, research, and replication guide
- See `docs/CLAIM_REDEEM.md` for options to auto-claim/redeem winnings
- **Arbitrage (Polymarket vs options)**: `arbitrage/` module: (1) **Automated loop** `run_arbitrage_loop.py` – Black-Scholes fair value, auto-trade when cheap, auto-redeem; no LLM; (2) Brief for OpenClaw. See `docs/ARBITRAGE_STRATEGY.md` and `arbitrage/README.md`
- **Contrarian + model “oracle”**: Optional filter using model-implied P(UP) for the 5-min window (`btc_5m_fair_value.py`, B-S digital). Set `CONTRARIAN_USE_MODEL_FAIR_VALUE=True` and `CONTRARIAN_MIN_EDGE`; see `docs/CONTRARIAN_ORACLE_AND_FORECASTING.md`
- **Fallback-lite option**: Set `FALLBACK_TREND_ENABLED=False` to allow only fallback IBS entries (disable weak-trend fallback entries that can overtrade choppy windows).
- **Tier analyzer**: `analyze_tier_performance.py` computes tier-level expectancy + profit factor and outputs KEEP/DROP/HOLDOUT based on thresholds (`--min-sample`, `--pf-threshold`).
- **Global EV gate**: Optional profitability gate in `bot.py` (`MODEL_EV_GATE_ENABLED`) blocks entries unless model-implied net edge clears cost buffer: `model_prob(direction) - entry_price - MODEL_EV_COST_BUFFER >= MODEL_EV_MIN_EDGE`.
- **ML direction model**: Optional sklearn RF or Bi-LSTM for P(UP). Backfill: `python -m ml.backfill_training_data --windows 600`. Train: `python -m ml.train_direction_model --backfill logs/ml_training_backfill.csv` or `python -m ml.train_lstm_model --candles 500`. Set `MODEL_USE_ML=True`. ETH cross-asset feature supported. See `docs/ML_IMPROVEMENTS_FROM_RESEARCH.md`.