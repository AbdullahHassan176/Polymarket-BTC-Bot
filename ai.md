# Polymarket BTC 5-Minute Direction Bot

## Purpose
Automated paper/real betting bot targeting Polymarket's rolling "Up or Down - 5 Minutes" binary markets (BTC, ETH, XRP, SOL, DOGE). Uses OKX public 1m candle data for the chosen asset to bet YES (UP) or NO (DOWN) on each 5-minute window.

## Key Technologies
- **Python 3.9+** with `py-clob-client` for Polymarket order placement
- **Polygon blockchain** (chain_id=137) via EOA wallet (MetaMask)
- **OKX public candle API** for BTC-USDT 1m OHLCV data (no OKX account needed)
- **Polymarket Gamma API** for market discovery (public, no auth)
- **Polymarket CLOB API** for orderbook prices and order placement (L1+L2 auth)

## Architecture
```
scripts/bot.py -> polymarket_client, data, strategy, execution, risk
```

## Directory Structure
Root: .bat launchers + .env. Python in `scripts/`. See `docs/STRUCTURE.md`.
```
├── start_bot.bat, stop_bot.bat, restart_bot.bat, bot_status.bat, watch_bot.bat, dashboard_bot.bat
├── dashboard/
│   ├── server.py, index.html, sessions.html   # aiohttp + Chart.js (like Oracle bot)
├── scripts/
│   ├── bot.py, config.py, polymarket_client.py, data.py, strategy.py
│   ├── execution.py, risk.py, btc_5m_fair_value.py
│   ├── run_*.py, analyze_tier_performance.py
│   ├── watchdog_btc.py, launch/*.ps1
│   ├── ml/, arbitrage/
├── docs/, logs/, state.json
```

## Authentication
Two-level auth via `py-clob-client`. Two wallet modes:
- **EOA mode** (`POLY_WALLET_ADDRESS`): `signature_type=0`. Single wallet signs and holds USDC. Needs USDC + POL on Polygon.
- **Proxy mode** (`PROXY_WALLET`): `signature_type=2`, `funder=PROXY_WALLET`. Gnosis Safe holds USDC; EOA signs (like Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot). Set `PROXY_WALLET` in `.env` to enable. See `docs/PROXY_WALLET_SETUP.md`.

## Signal Logic (strategy.py)
**Modes** via `config.STRATEGY_MODE`:
- **reversal**: Pyramid entry + long-hold tiered TP. **Entry**: [0.10, 0.15] tiers -- 60% at 15c, 40% more at 10c if price continues falling (`REVERSAL_ENTRY_MODE=pyramid`, `REVERSAL_ENTER_AT_LOWEST_ONLY=False`). **Hold gate**: `REVERSAL_HOLD_MIN_SECS=30` -- stuck-bid and SL cannot fire until 30s held. **SL fix**: `CHEAP_ENTRY_NO_SL_THRESHOLD=0.25` -- disables instant SL on 15c entries. **Per-asset TP**: `ASSETS_CONFIG[asset]["reversal_tp_tiers"]` -- ETH/SOL live only (best ROI from on-chain analysis); BTC/XRP paused. **Exits**: last 60s exit at bid; max 240s; `REVERSAL_STUCK_BID_TIMEOUT_SECS=45`. **FOK exits**: sell orders use FOK (Fill-or-Kill) via `SELL_FOK_PRICE_STEP=0.01`, `SELL_FOK_MAX_STEPS=3` -- GTC was the root cause of phantom PnL (orders placed but never matched). **CLEARED_STALE accounting**: now logs `-size_usdc` as PnL (conservative full-loss) instead of $0; use `reconcile_polymarket_history.py` to correct after exporting Polymarket history.
- **momentum**: EMA breakout. Price band 35–65¢.
- **contrarian**: buy cheap side (3–20¢) when IBS extreme; model fair-value filter (when enabled) + optional EMA filter (don't fight strong trend).
- **hybrid** (default): reversal (if `REVERSAL_ENABLED`) → contrarian → momentum → fallback (IBS-only, weak trend).

## Market Lifecycle
- Each 5-min window is a NEW Polymarket market with new token IDs
- Bot discovers active market via Gamma API each loop
- Evaluates signals throughout full 5-min window (loop every 10s); late window = strong move + mispricing
- Position auto-resolves via Chainlink oracle - no manual exit needed
- Bot polls `get_market_result()` every `RESOLUTION_POLL_INTERVAL_SECS` (30s) after market end — **without** `is_market_closed()` gate (Gamma lags closed flag by 2-5 min). Records WIN/LOSS as soon as result appears.
- **Stale position recovery**: After `CLEAR_STALE_POSITION_AFTER_SECS` (300s = 5 min), bot tries `get_market_result` one last time (3 quick retries) then force-clears as CLEARED_STALE only on genuine failure. **Reconciliation**: `python scripts/reconcile_polymarket_history.py <Polymarket-History.csv> [--apply]` to correct PnL from Profile → Trading History → Export.
- **Multi-position**: Bot supports multiple concurrent positions (one per market). `MAX_CONCURRENT_POSITIONS` (default 3) limits exposure. Processing open positions no longer blocks entry into new windows—you can enter 4:15-4:20 while still monitoring 4:10-4:15.
- **Multi-asset**: Set `TRADING_ASSET=ETH` (or XRP, SOL, DOGE) in env to trade that asset. One process per asset; state files: `state.json` (BTC), `state_ETH.json`, etc. Optional smaller size for alts: `RISK_PER_TRADE_ALT_USDC` in config. Launch: `scripts/launch/run_btc_live_others_paper.ps1` = 1 live BTC + 1 live SOL + 3 paper (ETH, XRP, DOGE).
- **Paper run with 1s price path**: `.\run_paper_price_logging.bat` or `scripts/launch/run_paper_all_price_logging.ps1` — stops all bots, then starts 5 paper processes (BTC, ETH, XRP, SOL, DOGE) with 1s loop. All data goes to `logs/paper_run_YYYYMMDD_HHMMSS/`: `price_paths.csv` (yes/no every second, `issues` e.g. sum_off), `signals_evaluated.csv`, `paper_trades.csv`. Ensures model is always running and tracks price paths, signals, bets, and manipulation flags. Stop with `.\stop_bot.bat` or create `STOP_BOT.txt`.

## Risk Controls
- `RISK_PER_TRADE_USDC` — max USDC per bet (hard cap)
- `MAX_DAILY_LOSS_USDC` — stop for the day after this loss
- `MAX_TRADES_PER_DAY` — at most N bets per UTC day
- `MAX_CONCURRENT_POSITIONS` — max open positions at once (default 3)
- `REVERSAL_ENTRY_MODE` (env) — `single` (default) or `pyramid`; toggle via `.env`, restart bot. Run `analyze_reversal_entry_modes.py` for recommendation
- **Compounding**: `COMPOUNDING_ENABLED`, `BANKROLL_START_USDC`, `RISK_PCT_PER_TRADE`, `MIN_TRADE_USDC`. Size = (bankroll + cumulative_pnl) * pct, clamped to [MIN, RISK_PER_TRADE_USDC]. `cumulative_pnl_usdc` in state (not reset at midnight). **REAL-only**: PAPER trades do not update `cumulative_pnl_usdc` or `daily_pnl_usdc`, so they match wallet profit.
- **Take profit / stop loss**: optional early exit (TP/SL). Outcome logged as "TP" or "SL".
- **FOK exits**: SELL orders now use FOK (Fill-or-Kill) via `place_order(..., fok=True)`. `SELL_FOK_PRICE_STEP=0.01` steps price down per retry; `SELL_FOK_MAX_STEPS=3` max attempts. If all fail, position stays open (no phantom PnL). `MAX_SELL_ORDER_USDC=2.5` still caps per-order notional.
- Kill switch: create `STOP_BOT.txt` to stop gracefully

## Running
**Restart required** after any config or code change (e.g. `REVERSAL_EXIT_MODE`, dynamic lowest, exit-at-market last N secs). Use `.\restart_bot.bat` or stop then `.\start_bot.bat`.
```powershell
# Setup
python -m venv venv   # or .venv
pip install -r requirements.txt
copy .env.example .env

# Start/Stop (like Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot)
.\start_bot.bat       # Start bot + watchdog (paper mode)
.\stop_bot.bat        # Stop
.\restart_bot.bat     # Stop + start (use after config/code changes)
.\bot_status.bat      # Status, trades, PnL, win rate
.\watch_bot.bat       # Live output
.\dashboard_bot.bat   # Web dashboard (http://localhost:8765, like Oracle bot)

# Manual run (from repo root)
python scripts/bot.py --paper
python scripts/bot.py --paper --once
python scripts/run_12hr_reversal.py   # Reversal strategy, 12hr
python scripts/analyze_tier_performance.py --csv logs/trades_12hr_reversal.csv
```

## Key Config (config.py)
- `REAL_TRADING` — master switch, default False
- `ENTRY_WINDOW_SECS` — how long after window open to allow entry (default 90)
- `MAX_ENTRY_PRICE / MIN_ENTRY_PRICE` — price range filter for YES/NO tokens
- `CLEAR_STALE_POSITION_AFTER_SECS` (300) — force-clear stuck positions after 5min; polls result every 30s before that
- `EMA_FAST / EMA_SLOW` — momentum signal periods (default 10/30 on 1m candles)

## Notes for Future AI Assistants
- Do not mix state with the OKX bot (separate folder, separate state.json)
- `polymarket_client.py` wraps `py-clob-client`; never import `ClobClient` directly elsewhere
- Polymarket token IDs change every 5 minutes (new market = new tokens)
- Always check `REAL_TRADING=False` before adding any auto-execution logic
- See `docs/THEORY_AND_IMPLEMENTATION.md` for full theory, research, and replication guide
- **Auto-claim**: `lib/ctf_redeem.py` (on-chain via Gnosis Safe, like Oracle bot). `claim_unclaimed.bat` for manual recovery. See `docs/CLAIM_REDEEM.md`.
- **Arbitrage (Polymarket vs options)**: `scripts/arbitrage/` module: (1) **Automated loop** `run_arbitrage_loop.py` – Black-Scholes fair value, auto-trade when cheap, auto-redeem; no LLM; (2) Brief for OpenClaw. See `docs/ARBITRAGE_STRATEGY.md` and `scripts/arbitrage/README.md`
- **Contrarian + model “oracle”**: Optional filter using model-implied P(UP) for the 5-min window (`btc_5m_fair_value.py`, B-S digital). Set `CONTRARIAN_USE_MODEL_FAIR_VALUE=True` and `CONTRARIAN_MIN_EDGE`; see `docs/CONTRARIAN_ORACLE_AND_FORECASTING.md`
- **Fallback-lite option**: Set `FALLBACK_TREND_ENABLED=False` to allow only fallback IBS entries (disable weak-trend fallback entries that can overtrade choppy windows).
- **Tier analyzer**: `analyze_tier_performance.py` computes tier-level expectancy + profit factor. **Performance/drawdown**: `analyze_performance.py` (PnL by asset), `drawdown_report.py --live` (cumulative PnL, max drawdown, recent win/loss streak). **Reversal frequency**: `analyze_reversal_frequency.py` = daily reversal-captured rate from trades; `--paths` = market-level full-reversal rate from `logs/reversal_window_paths.csv` (bot logs min/max per window). Run weekly. **Profit ideas**: see `docs/MAX_PROFIT_IDEAS.md`. **Ideas from Oracle bot**: `docs/IMPROVEMENTS_FROM_ORACLE_BOT.md`.
- **No entry in last 60s**: `ENTRY_MIN_SECS_REMAINING=60` — never place new orders when &lt; 60s left. **Entry only in first 120s**: `ENTRY_MAX_ELAPSED_SECS=120` — allow buys only in the first 2 minutes of each 5-min window (enough for reversals).
- **Global EV gate**: Optional profitability gate in `bot.py` (`MODEL_EV_GATE_ENABLED`) blocks entries unless model-implied net edge clears cost buffer: `model_prob(direction) - entry_price - MODEL_EV_COST_BUFFER >= MODEL_EV_MIN_EDGE`.
- **ML direction model**: Optional sklearn RF or Bi-LSTM for P(UP). Backfill: `python scripts/ml/backfill_training_data.py --windows 600`. Train: `python scripts/ml/train_direction_model.py --backfill logs/ml_training_backfill.csv` or `python scripts/ml/train_lstm_model.py --candles 500`. Set `MODEL_USE_ML=True`. ETH cross-asset feature supported. See `docs/ML_IMPROVEMENTS_FROM_RESEARCH.md`.