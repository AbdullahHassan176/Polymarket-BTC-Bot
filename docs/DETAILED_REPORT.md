# Polymarket BTC 5-Minute Bot — Detailed Report

**Purpose of this document:** A single reference for strategy, architecture, calibrations, what we do and do not do (and why), and observed results from paper trading.

---

## 1. Executive Summary

We run an automated bot that trades Polymarket’s **“Bitcoin Up or Down - 5 Minutes”** binary markets. Each market resolves **YES** if BTC closes at or above its open at the 5-minute mark, and **NO** otherwise. The bot:

- Discovers the active 5-min market via Polymarket’s Gamma API.
- Uses **OKX 1-minute BTC-USDT candles** (public, no account) to compute **EMA**, **ATR%**, and **IBS** (Internal Bar Strength).
- Generates signals in **hybrid** mode: **Late-window** → **Contrarian** → **Momentum** → **Fallback**.
- Enters **one position per 5-min window** (configurable), with **take profit** (85¢) and **stop loss** (25¢), and **softer TP/SL rules for very cheap entries** (e.g. no SL &lt; 15¢, hold to resolution ≤ 10¢).
- Tracks PnL, daily limits, and compounding in **state.json**; logs every trade to CSV with **strategy_tier**, balance, and cumulative PnL.

**Real trading is off by default** (`REAL_TRADING = False`). Paper mode logs the same flow without placing orders. A separate **arbitrage** module exists for stock price-target markets vs option-implied fair value; it is **not** used for the BTC 5-min strategy.

---

## 2. Architecture

### 2.1 High-level flow

```
bot.py (main loop, every LOOP_INTERVAL_SECONDS = 10s)
  │
  ├─► Kill switch (STOP_BOT.txt) → exit if present
  ├─► risk.reset_if_new_day() → reset daily_trades / daily_pnl at UTC midnight
  ├─► polymarket_client.find_active_btc_market() → current 5-min market (Gamma API)
  │
  ├─► If open position exists:
  │     ├─► Poll polymarket_client.is_market_closed() until resolved
  │     ├─► execution.paper_record_outcome() or real_record_outcome() → WIN/LOSS
  │     └─► Else: check TP/SL (with cheap-entry rules); paper_close_early / real_close_early if hit
  │
  └─► If no open position:
        ├─► data.fetch_candles() → OKX 1m BTC-USDT
        ├─► data.compute_indicators() → EMA, ATR%, IBS
        ├─► data.get_latest_indicators() + get_btc_spot_price()
        ├─► client.get_mid_price() for YES/NO tokens (CLOB)
        ├─► strategy.check_signal(indicators, yes_price, no_price, context) → BUY_YES | BUY_NO | SKIP
        ├─► risk.can_trade(market=market) → one-trade-per-window + daily limits + loss limit
        └─► execution.paper_enter() or real_enter() → position stored in state; risk.record_trade_opened()
```

### 2.2 Components

| Component | Role |
|-----------|------|
| **bot.py** | Main loop: kill switch, daily reset, market discovery, position handling, candle fetch, signal evaluation, risk gate, entry. Passes `context` (window_start_btc, secs_remaining, current_btc, in_late_window) to strategy. |
| **polymarket_client.py** | Gamma API (market discovery), CLOB (mid/best price, order placement). L1/L2 auth for orders; no auth for discovery/prices. |
| **data.py** | OKX candles (1m), compute EMA/ATR/IBS, get_btc_price_at_time (for window open), get_btc_spot_price(). |
| **strategy.py** | check_signal() and tier logic: late_window → contrarian → momentum → fallback. Returns (action, debug_info) with `tier` for CSV. |
| **execution.py** | paper_enter / real_enter (with strategy_tier), paper_record_outcome / real_record_outcome, paper_close_early / real_close_early. Writes every OPEN/CLOSE to CSV (with balance columns). |
| **risk.py** | RiskManager: state.json (open_position, daily_trades, daily_pnl_usdc, cumulative_pnl_usdc, last_traded_condition_id, etc.). can_trade(market), get_trade_size_usdc(), record_trade_opened/Closed. |
| **config.py** | Single source of truth: mode, credentials paths, market targeting, TP/SL, risk, strategy mode, all thresholds. |
| **btc_5m_fair_value.py** | Optional: model_implied_p_up(window_open, current_btc, secs_remaining, atr_pct) via Black-Scholes digital; used only when CONTRARIAN_USE_MODEL_FAIR_VALUE is True. |

### 2.3 Data and credentials

- **OKX**: Public candle and ticker APIs; no API key. Used for BTC price and volatility.
- **Polymarket Gamma**: Public; used to find the active “Bitcoin Up or Down” 5-min market.
- **Polymarket CLOB**: Public for orderbook/mid; **private key + wallet** (in .env) required for order placement. Paper mode does not need .env for market data or prices.
- **Resolution**: Markets resolve on-chain (e.g. Chainlink). We poll Gamma/CLOB until the market is closed, then record WIN/LOSS from the resolved outcome.

---

## 3. Strategy — What We Do

### 3.1 Strategy mode

**config.STRATEGY_MODE** = `"hybrid"` (default). Alternatives: `"momentum"` or `"contrarian"` only.

In **hybrid**, the order of evaluation is:

1. **Late-window** (if in last LATE_WINDOW_SECS and LATE_ENTRY_ENABLED)  
   - BUY_YES if BTC move from window start ≥ LATE_MIN_MOVE_PCT and YES price ≤ LATE_MAX_PRICE.  
   - BUY_NO if move ≤ −LATE_MIN_MOVE_PCT and NO price ≤ LATE_MAX_PRICE.

2. **Contrarian**  
   - ATR and “spike” filters (skip if ATR% too high).  
   - BUY_YES if YES is “cheap” (CONTRARIAN_MIN_PRICE–CONTRARIAN_MAX_PRICE) and IBS &lt; CONTRARIAN_IBS_BOUNCE (oversold).  
   - BUY_NO if NO is cheap and IBS &gt; CONTRARIAN_IBS_FADE (overbought).  
   - Optional: when CONTRARIAN_USE_MODEL_FAIR_VALUE is True, require model_implied_p_up (or 1−it for NO) to exceed market price by CONTRARIAN_MIN_EDGE.

3. **Momentum**  
   - EMA trend + breakout (close &gt; prev high / rolling high for UP; close &lt; prev low for DOWN).  
   - Price in band MIN_ENTRY_PRICE–MAX_ENTRY_PRICE; IBS confirms bar closed in our direction.  
   - Min EMA spread (MIN_EMA_SPREAD_USD) and ATR/ATR-spike filters.

4. **Fallback** (if FALLBACK_ENABLED)  
   - IBS-only: BUY_YES if IBS &lt; FALLBACK_IBS_LOW and YES in FALLBACK_PRICE_MIN–FALLBACK_PRICE_MAX; BUY_NO if IBS &gt; FALLBACK_IBS_HIGH and NO in band.  
   - Weak trend: EMA direction with relaxed spread (FALLBACK_MIN_EMA_SPREAD_USD), same price band.

First non-SKIP tier wins; we take **at most one** signal per evaluation.

### 3.2 Entry and exit rules

- **One trade per window**: When ONE_TRADE_PER_WINDOW is True, after we open (or close) a trade in a given 5-min market we do not open again in that same market (risk tracks last_traded_condition_id).
- **Take profit**: If TAKE_PROFIT_ENABLED and best bid ≥ TAKE_PROFIT_PRICE (0.85), we close and log outcome as TP. **Exception:** if entry_price ≤ CHEAP_ENTRY_HOLD_TO_RESOLUTION_THRESHOLD (0.10), we do **not** apply TP (hold to resolution).
- **Stop loss**: If STOP_LOSS_ENABLED and best bid ≤ STOP_LOSS_PRICE (0.25), we close and log as SL. **Exceptions:** no SL if entry_price &lt; CHEAP_ENTRY_NO_SL_THRESHOLD (0.15); no TP/SL at all if entry_price ≤ 0.10 (hold to resolution only).
- **Sizing**: When COMPOUNDING_ENABLED, size = (BANKROLL_START_USDC + cumulative_pnl_usdc) × RISK_PCT_PER_TRADE, clamped to [MIN_TRADE_USDC, RISK_PER_TRADE_USDC]. Otherwise fixed RISK_PER_TRADE_USDC.

### 3.3 Risk and safety

- **Daily**: daily_trades ≤ MAX_TRADES_PER_DAY; daily_pnl_usdc ≥ −MAX_DAILY_LOSS_USDC (stop for the day if loss limit hit).
- **Position**: At most one open position; can_trade(market) also enforces one-trade-per-window when enabled.
- **Real trading**: Only if REAL_TRADING is True in config; paper mode never sends orders.

---

## 4. What We Are Not Doing (and Why)

| Not doing | Why |
|-----------|-----|
| **Real order placement by default** | REAL_TRADING = False. Avoids accidental live trading; paper first. |
| **Using the arbitrage module for BTC 5-min** | The arbitrage/ module is for **stock price-target** Polymarket markets vs **option-implied** fair value. There are no listed options that expire in 5 minutes, so we use a **vol-based** 5-min model (btc_5m_fair_value) only when CONTRARIAN_USE_MODEL_FAIR_VALUE is True. |
| **“Oracle” arbitrage (knowing the future)** | Resolution is on-chain; nobody gets the result before the window closes. We only use a **model-as-oracle** (e.g. B-S digital P(UP)) to filter contrarian when enabled. |
| **Multiple positions per window** | ONE_TRADE_PER_WINDOW = True to reduce overtrading and make one decision per 5-min window. |
| **SL on very cheap entries** | entry &lt; 0.15: no SL (avoid cutting 5–15¢ bounces at 25¢). entry ≤ 0.10: no TP/SL (hold to resolution for max payoff when right). |
| **Trading in extreme volatility** | ATR_THRESHOLD (and ATR_SPIKE_MULTIPLIER) skip signals when ATR% is too high. |
| **Momentum without confirmation** | Momentum requires price in band and IBS confirming bar closed in our direction; fallback uses a weaker but still defined band and spread. |
| **Resetting cumulative PnL at midnight** | cumulative_pnl_usdc is all-time for compounding; daily_pnl_usdc resets at UTC midnight. |

---

## 5. Calibrations (Config Summary)

### 5.1 Mode and market

- REAL_TRADING: False  
- ENTRY_WINDOW_SECS: 90  
- LATE_ENTRY_ENABLED: True, LATE_WINDOW_SECS: 90, LATE_MIN_MOVE_PCT: 0.003, LATE_MAX_PRICE: 0.90  
- STRATEGY_MODE: "hybrid"

### 5.2 TP/SL and cheap-entry rules

- TAKE_PROFIT_ENABLED: True, TAKE_PROFIT_PRICE: 0.85  
- STOP_LOSS_ENABLED: True, STOP_LOSS_PRICE: 0.25  
- CHEAP_ENTRY_NO_SL_THRESHOLD: 0.15  
- CHEAP_ENTRY_HOLD_TO_RESOLUTION_THRESHOLD: 0.10  
- ONE_TRADE_PER_WINDOW: True  

### 5.3 Risk and compounding

- RISK_PER_TRADE_USDC: 10.0, MAX_DAILY_LOSS_USDC: 25.0, MAX_TRADES_PER_DAY: 1000  
- BANKROLL_START_USDC: 50.0, COMPOUNDING_ENABLED: True, RISK_PCT_PER_TRADE: 0.10, MIN_TRADE_USDC: 1.0  

### 5.4 Momentum

- EMA_FAST: 10, EMA_SLOW: 30  
- ATR_THRESHOLD: 0.030, ATR_SPIKE_MULTIPLIER: 2.0  
- MIN_ENTRY_PRICE: 0.35, MAX_ENTRY_PRICE: 0.65  
- MIN_EMA_SPREAD_USD: 25.0  
- IBS_MIN_FOR_UP: 0.5, IBS_MAX_FOR_DOWN: 0.5  

### 5.5 Contrarian

- CONTRARIAN_MAX_PRICE: 0.25, CONTRARIAN_MIN_PRICE: 0.03  
- CONTRARIAN_USE_MODEL_FAIR_VALUE: False, CONTRARIAN_MIN_EDGE: 0.05  
- CONTRARIAN_IBS_BOUNCE: 0.25, CONTRARIAN_IBS_FADE: 0.75  

### 5.6 Fallback

- FALLBACK_ENABLED: True  
- FALLBACK_IBS_LOW: 0.15, FALLBACK_IBS_HIGH: 0.85  
- FALLBACK_PRICE_MIN: 0.30, FALLBACK_PRICE_MAX: 0.45  
- FALLBACK_MIN_EMA_SPREAD_USD: 10.0  

### 5.7 5-min model (optional)

- BTC5M_VOL_ANNUALIZATION: 50.0 (used only when CONTRARIAN_USE_MODEL_FAIR_VALUE is True)  

### 5.8 Loop and data

- LOOP_INTERVAL_SECONDS: 10, REQUEST_TIMEOUT: 10  
- SPOT_TICKER: BTC-USDT, CANDLE_BAR: 1m, CANDLE_LIMIT: 100  

---

## 6. Results — What We Are Seeing

### 6.1 12-hour hybrid run (earlier analysis)

From `docs/IMPROVEMENTS_FROM_LOGS.md`, based on `logs/trades_12hr_hybrid.csv` and `logs/bot.log`:

- **Win rate**: ~15% of closed trades were TP; ~85% SL (almost all exits were early TP/SL, not resolution).
- **Profitability**: Positive overall because **average win (+$6.48) &gt; average loss (−$2.55)** (asymmetric payoff).
- **By entry price**: 5¢ entries were best (+$90); 10¢–35¢ and 50¢ buckets were negative; 20¢, 25¢, 40¢, 55¢ positive.
- **Overtrading**: Multiple entries in the same 5-min window were observed before one-trade-per-window was added.

Those findings drove: CONTRARIAN_MAX_PRICE 0.25, tighter fallback (IBS/price), ATR 0.03, one trade per window, cheap-entry TP/SL rules, and strategy_tier in CSV.

### 6.2 2-hour hybrid run (trades_2hr_hybrid.csv)

From the 2-hour paper run with the above improvements:

- **Sample**: 27+ closed trades (mix of OPEN/CLOSE rows in CSV).
- **Outcomes**: Many SLs; a few TP (e.g. NO @ 0.595 TP +$1.51; NO @ 0.635 TP +$1.01). One full LOSS at resolution (contrarian NO @ 0.075, −$5.00).
- **By tier**: Contrarian (e.g. NO 0.075, YES 0.24), fallback (many YES/NO in 0.34–0.45), momentum (YES/NO in 0.50–0.64). Fallback and momentum often hit SL; contrarian had one full loss and one near-flat SL.
- **Cumulative PnL**: In the sampled segment, cumulative_pnl_usdc goes negative (e.g. −$18 to −$22), with starting balance $50 and current balance in the high $20s–low $30s.
- **Strategy_tier and balance**: Every OPEN/CLOSE row includes strategy_tier, starting_balance_usdc, trade_pnl_usdc, cumulative_pnl_usdc, current_balance_usdc.

So in this 2-hour window we see: **low win rate**, **asymmetric wins when they occur**, and **cumulative drawdown** in paper; the system is behaving as designed (one trade per window, cheap-entry rules, tier logging) while we observe that the current calibrations in that period produced more losing than winning trades.

### 6.3 Summary of observations

- **Asymmetric payoff**: Wins (TP or resolution WIN) are large relative to typical SL size; a minority of wins can offset many small losses.
- **Stop-loss frequency**: Many positions exit at SL (25¢); cheap-entry rules aim to reduce SL on very cheap tickets and hold ≤10¢ to resolution.
- **Tier mix**: Contrarian, momentum, and fallback all fire; fallback and momentum often contribute to SL-heavy runs; contrarian can deliver large loss when wrong at resolution (e.g. 7.5¢ NO).
- **One trade per window**: Enforced; no re-entry in the same 5-min market after TP/SL.
- **Tracking**: CSV and state give full PnL and balance picture for tuning and analysis.

---

## 7. Optional Enhancements (Implemented but Off or Separate)

### 7.1 Model-as-oracle for contrarian

- **What**: `btc_5m_fair_value.model_implied_p_up()` uses Black-Scholes digital P(close ≥ window open) with ATR-derived vol. When CONTRARIAN_USE_MODEL_FAIR_VALUE is True, we only take contrarian YES if model P(YES) ≥ yes_price + CONTRARIAN_MIN_EDGE, and similarly for NO.
- **Status**: Implemented; **off by default** (CONTRARIAN_USE_MODEL_FAIR_VALUE = False). See `docs/CONTRARIAN_ORACLE_AND_FORECASTING.md`.

### 7.2 Arbitrage module (stock markets)

- **What**: `arbitrage/` discovers Polymarket price-target markets (e.g. “Google hit $X”), computes option-implied fair value, and can paper/live trade when Polymarket is cheap vs that fair value.
- **Status**: Separate from the BTC 5-min bot; not used for BTC 5-min. See `docs/ARBITRAGE_STRATEGY.md` and `arbitrage/README.md`.

### 7.3 Run scripts

- **run_2hr_paper.py** / **run_12hr_paper.py**: Reset state, set trades CSV (e.g. trades_2hr_hybrid.csv), set STRATEGY_MODE and ENTRY_WINDOW_SECS, then run the bot for 2 or 12 hours and create STOP_BOT.txt at the end.

---

## 8. File and State Reference

| Item | Location / purpose |
|------|--------------------|
| State | state.json: open_position, daily_trades, daily_pnl_usdc, cumulative_pnl_usdc, starting_balance_usdc, last_traded_condition_id, etc. |
| Trades CSV | logs/trades.csv (default); run scripts may set logs/trades_2hr_hybrid.csv or logs/trades_12hr_hybrid.csv. Columns include strategy_tier, starting_balance_usdc, trade_pnl_usdc, cumulative_pnl_usdc, current_balance_usdc. |
| Bot log | logs/bot.log (rotating, 5 MB × 3). |
| Kill switch | STOP_BOT.txt in project root. |
| Config | config.py (all calibrations). |
| Docs | docs/IMPROVEMENTS_FROM_LOGS.md, docs/CONTRARIAN_ORACLE_AND_FORECASTING.md, docs/ARBITRAGE_STRATEGY.md, docs/THEORY_AND_IMPLEMENTATION.md, docs/CLAIM_REDEEM.md. |

---

## 9. One-paragraph summary

We run a **hybrid** (late-window → contrarian → momentum → fallback) bot on Polymarket’s **BTC 5-minute UP/DOWN** binaries, using **OKX 1m candles** for EMA/ATR/IBS, **one trade per window**, **TP at 85¢ and SL at 25¢** with **softer rules for cheap entries** (no SL &lt; 15¢, hold to resolution ≤ 10¢), and **compounding** with daily and loss limits. We **do not** use the stock arbitrage module for this product; we **do not** have access to future resolution (no true oracle); we **optionally** filter contrarian by a **model-implied P(UP)** when enabled. Calibrations are centralized in **config.py**. Paper runs show **asymmetric payoff** (big wins, smaller losses), **low win rate**, and **cumulative drawdown** in the sampled 2-hour run; strategy_tier and balance tracking are in place for further tuning and analysis.
