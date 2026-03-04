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
- **contrarian**: buy cheap side (3–35¢) when IBS extreme. ATR filter applies.
- **hybrid** (default): contrarian → momentum → fallback (IBS-only, weak trend).

## Market Lifecycle
- Each 5-min window is a NEW Polymarket market with new token IDs
- Bot discovers active market via Gamma API each loop
- Evaluates signals throughout full 5-min window (loop every 10s); late window = strong move + mispricing
- Position auto-resolves via Chainlink oracle - no manual exit needed
- Bot polls `is_market_closed()` and records WIN/LOSS after resolution

## Risk Controls
- `RISK_PER_TRADE_USDC = 10.0` — max USDC per bet
- `MAX_DAILY_LOSS_USDC = 30.0` — stop for the day after this loss
- `MAX_TRADES_PER_DAY = 6` — at most 6 bets per UTC day
- Max 1 open position at a time
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
