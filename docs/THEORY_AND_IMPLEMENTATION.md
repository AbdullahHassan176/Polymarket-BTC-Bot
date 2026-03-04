# Polymarket BTC 5-Minute Direction Bot
## Theory, Research & Implementation Guide

This document explains the theory behind the strategy, the research it draws from, and how to replicate the system step-by-step. It is intended for humans and AI assistants who need to understand, modify, or extend the bot.

---

## 1. Overview

### What This Bot Does

The bot trades Polymarket’s rolling **“Bitcoin Up or Down - 5 Minutes”** binary markets. Each market asks: will BTC go up or down in the next 5 minutes? Outcomes are YES (UP) or NO (DOWN). The bot:

1. Finds the active 5-minute window on Polymarket  
2. Uses BTC price data from OKX to compute technical indicators  
3. Combines indicators with Polymarket YES/NO prices to decide: BUY_YES, BUY_NO, or SKIP  
4. Evaluates signals **throughout the full 5-minute window** (checks every 10 seconds)  
5. Enters when any tier fires: late-window (last 90s), contrarian, momentum, or fallback  
6. Holds until the market resolves (no manual exit)

### Why 5-Minute Direction?

- **Short horizon** reduces exposure to macro noise  
- **Binary outcome** makes PnL clear: win = token pays $1, lose = $0  
- **Frequent windows** allow many observations and iteration  
- **Polymarket liquidity** on these markets is sufficient for small–medium size

---

## 2. Research & Theoretical Basis

### 2.1 Momentum / Trend Following

**Concept:** Short-term price moves often persist. When price breaks above recent highs with the trend (EMA fast > EMA slow), upside continuation is more likely than random.

**Academic / practitioner background:**
- Momentum effects in equities and crypto (e.g., Jegadeesh & Titman, 1993; later work on crypto)
- EMA crossover as a simple trend filter (widely used in systematic trading)
- Breakouts (close > prev_high or rolling high) as a classic momentum entry

**Implementation:** EMA10/30 on 1m candles, breakout above/below levels, IBS confirmation that the bar closed in the direction of the trade.

### 2.2 Mean Reversion / Contrarian (Mispricing)

**Concept:** When the crowd overreacts, the “cheap” side can be mispriced. Buying the cheap outcome when mean reversion is likely improves expected value.

**Observable on Polymarket:**
- Successful traders (e.g., nervem13, pschosuit) often buy the **cheap side** (e.g. 5–35¢) when the market overprices the other outcome
- When YES is at 10¢ and NO at 90¢, a small shift in probability can make YES profitable
- Asymmetric payoff: risking $5 to win $45+ when YES goes to 1.00

**Internal Bar Strength (IBS)**  
IBS = (close − low) / (high − low). Range [0, 1]:

- **IBS < 0.25:** bar closed weak (near low) → oversold, potential bounce  
- **IBS > 0.75:** bar closed strong (near high) → overbought, potential pullback  

Used as a mean-reversion signal: buy YES when IBS is low and YES is cheap; buy NO when IBS is high and NO is cheap.

### 2.3 Volatility Filter (ATR)

**Concept:** In very volatile conditions, short-term direction is noisy. ATR% (average true range as % of price) filters out extreme volatility.

- Skip if ATR% > threshold (default 3.5%)  
- Hard skip if ATR% > threshold × spike multiplier (e.g. 7%)  

Reduces drawdowns during chaotic moves.

### 2.4 Combined Hybrid Approach

**Idea:** Contrarian setups are rare but high payoff. Momentum setups are more frequent. A hybrid uses both:

1. Try contrarian first (cheap side + IBS extreme)  
2. If no contrarian setup, use momentum (EMA breakout + price band)

### 2.5 Late-Window (Strong Move + Mispricing)

**Concept:** With ~4 minutes of price data, we can be highly confident. If BTC is strongly up or down from window start, and the market has not yet priced it (YES/NO still below 90¢), we may have edge.

**Rules:**
- Enter only in the **last 90 seconds** of the window
- BTC move from window start must exceed ±0.3%
- Buy the likely-winning side only if it’s priced ≤ 90¢ (mispricing)
- Skip if already priced ≥ 90¢ (no edge at 99¢)

**EV rationale:** At 90¢ we need >90% confidence for positive EV. Strong move + 4 min of data can justify that; at 99¢ we’d need >99%, which is rarely justified.

---

## 3. Strategy Modes (Detail)

### 3.1 Momentum Mode

**Logic:**
- EMA fast > EMA slow → uptrend  
- EMA fast < EMA slow → downtrend  
- Breakout up: close > prev_high or close > rolling_high_20  
- Breakdown down: close < prev_low  
- **BUY_YES** when uptrend + breakout up + YES in 35–65¢ + IBS > 0.5  
- **BUY_NO** when downtrend + breakdown down + NO in 35–65¢ + IBS < 0.5  

**Why 35–65¢?**  
Outside this range the move is often already priced in (too expensive) or the crowd disagrees strongly (too risky).

### 3.2 Contrarian Mode

**Logic:**
- **BUY_YES** when: YES in 3–35¢ (cheap) and IBS < 0.25 (oversold)  
- **BUY_NO** when: NO in 3–35¢ (cheap) and IBS > 0.75 (overbought)  
- ATR filter still applies  

**Why 3–35¢?**  
Asymmetric payoff; avoid illiquid sub-3¢; avoid mid-range (no clear mispricing).

### 3.3 Hybrid Mode (Default)

1. Contrarian → momentum → fallback (when FALLBACK_ENABLED)  
2. Returns first non-SKIP  

### 3.4 Late-Window Mode

When in the **last 90 seconds** (`LATE_ENTRY_ENABLED=True`):

- Try late-window first (strong move + mispricing)  
- If SKIP, fall through to hybrid (contrarian → momentum → fallback)  

### 3.5 Fallback Tier (when hybrid SKIP)

When contrarian and momentum both SKIP:

- **IBS-only**: IBS < 0.2 and YES in 30–55¢ → BUY_YES; IBS > 0.8 and NO in 30–55¢ → BUY_NO  
- **Weak trend**: EMA spread ≥ $10, trend direction, price in 30–55¢ → BUY_YES/BUY_NO (no breakout required)

---

## 4. Technical Implementation

### 4.1 Data Flow

```
OKX API (1m BTC candles) → data.py
  - fetch_candles()
  - compute_indicators() → EMA, ATR%, IBS, rolling_high_20, prev_high, prev_low
  - get_latest_indicators()

Polymarket Gamma API → polymarket_client.py
  - find_active_btc_market()

Polymarket CLOB API → polymarket_client.py
  - get_mid_price(yes_token_id), get_mid_price(no_token_id)

strategy.check_signal(indicators, yes_price, no_price, context=None)
  → (BUY_YES | BUY_NO | SKIP, debug_info)
  context: {in_late_window, window_start_btc, current_btc} for late-window logic
```

### 4.2 Indicators

| Indicator       | Formula / Source              | Use                             |
|----------------|-------------------------------|----------------------------------|
| ema_fast       | EMA(close, 10)                | Trend direction                  |
| ema_slow       | EMA(close, 30)                | Trend direction                  |
| atr14          | Mean(high − low, 14)          | Volatility                       |
| atr_pct        | atr14 / close                 | Volatility filter                |
| ibs            | (close − low) / (high − low)  | Mean reversion, momentum confirm |
| rolling_high_20| max(high, 20)                 | Breakout level                   |
| prev_high      | high of previous candle       | Breakout level                   |
| prev_low       | low of previous candle        | Breakdown level                  |

### 4.3 Configuration (config.py)

| Parameter             | Default | Meaning                                      |
|-----------------------|---------|----------------------------------------------|
| STRATEGY_MODE         | hybrid  | momentum, contrarian, or hybrid              |
| ENTRY_WINDOW_SECS     | 90      | Seconds after window open to allow entry     |
| ATR_THRESHOLD         | 0.035   | Max ATR% (3.5%)                              |
| MIN_EMA_SPREAD_USD    | 25      | Min EMA spread to trust trend                |
| MIN_ENTRY_PRICE       | 0.35    | Momentum: min YES/NO price                    |
| MAX_ENTRY_PRICE       | 0.65    | Momentum: max YES/NO price                    |
| CONTRARIAN_MIN_PRICE  | 0.03    | Contrarian: min cheap price                   |
| CONTRARIAN_MAX_PRICE  | 0.35    | Contrarian: max cheap price                   |
| CONTRARIAN_IBS_BOUNCE | 0.25    | Contrarian: IBS < this → consider BUY_YES    |
| CONTRARIAN_IBS_FADE   | 0.75    | Contrarian: IBS > this → consider BUY_NO     |
| LATE_ENTRY_ENABLED    | True    | Enable late-window (last 90s) logic           |
| LATE_WINDOW_SECS      | 90      | Seconds before window end to allow entry      |
| LATE_MIN_MOVE_PCT     | 0.003   | BTC move from window start required (0.3%)    |
| LATE_MAX_PRICE        | 0.90    | Max price for late entry (avoid 99¢ trap)     |
| FALLBACK_ENABLED      | True    | Enable IBS-only and weak-trend fallback       |
| FALLBACK_IBS_LOW/HIGH | 0.20/0.80 | IBS thresholds for fallback                   |
| FALLBACK_PRICE_MIN/MAX| 0.30/0.55 | Price band for fallback                       |
| FALLBACK_MIN_EMA_SPREAD_USD | 10  | Min EMA spread for weak-trend fallback   |
| LOOP_INTERVAL_SECONDS | 10      | Seconds between checks (more = more chances)  |
| RISK_PER_TRADE_USDC   | 5.0     | Max USDC per bet                              |
| MAX_DAILY_LOSS_USDC   | 25.0    | Stop trading for the day after this loss     |
| MAX_TRADES_PER_DAY    | 12      | Max bets per UTC day                          |

---

## 5. How to Replicate

### 5.1 Prerequisites

- Python 3.9+  
- Polymarket wallet (MetaMask) with USDC.e and POL on Polygon  
- `.env` with `POLY_PRIVATE_KEY` and `POLY_WALLET_ADDRESS`

### 5.2 Setup

```powershell
# Clone or navigate to project
cd Polymarket-BTC-Bot

# Virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows
# source venv/bin/activate    # Linux/macOS

# Dependencies
pip install -r requirements.txt

# Environment
copy .env.example .env
# Edit .env: add POLY_PRIVATE_KEY and POLY_WALLET_ADDRESS
```

### 5.3 Run Scripts

| Script                  | Purpose                                   | Duration |
|-------------------------|-------------------------------------------|----------|
| `python bot.py --paper` | Paper trading, runs indefinitely          | Until STOP_BOT.txt |
| `python bot.py --paper --once` | Single loop iteration (debug)      | 1 iteration |
| `python run_30min_paper.py` | 30 min paper, hybrid + 180s entry  | 30 min |
| `python run_2hr_paper.py`   | 2 hr paper test                         | 2 hr |
| `python run_3hr_paper.py`   | 3 hr paper test                         | 3 hr |
| `python bot.py --real`  | Real trading (config.REAL_TRADING=True)   | Until stopped |

### 5.4 Paper Test Configuration

`run_30min_paper.py` overrides:

- `STRATEGY_MODE = "hybrid"`
- `ENTRY_WINDOW_SECS = 180`
- Trades log: `logs/trades_30min_contrarian.csv`
- `state.json` reset at start

### 5.5 Outputs

- **logs/bot.log** – Main bot log  
- **logs/trades.csv** – Default trade log (or path set by `execution.set_trades_csv`)  
- **state.json** – Open position, daily PnL, daily trade count

### 5.6 Kill Switch

Create `STOP_BOT.txt` in the project root to stop the bot cleanly.

---

## 6. File Reference

| File               | Role                                      |
|--------------------|-------------------------------------------|
| bot.py             | Main loop, kill switch, iteration flow    |
| strategy.py        | Signal logic (momentum, contrarian, hybrid) |
| data.py            | OKX candles, indicator calculation        |
| polymarket_client.py | Market discovery, prices, order placement |
| execution.py       | Paper/real entry, outcome recording       |
| risk.py            | State, daily limits, trade gate           |
| config.py          | All configuration                         |
| run_30min_paper.py | 30 min hybrid paper test                  |
| run_2hr_paper.py   | 2 hr paper test                           |
| run_3hr_paper.py   | 3 hr paper test                           |

---

## 7. Tuning for More Trades

To trade more frequently:

1. Use `STRATEGY_MODE = "hybrid"` (momentum fallback when no contrarian setup)  
2. Increase `ENTRY_WINDOW_SECS` (e.g. 180)  
3. Lower `MIN_EMA_SPREAD_USD` (e.g. 25)  
4. Optionally relax price bands (e.g. 0.25–0.75 for momentum)

Contrarian-only remains selective by design; hybrid + relaxed filters yields more signals.

---

## 8. References & Further Reading

- **EMA crossover:** Common trend-following filter in technical analysis  
- **IBS (Internal Bar Strength):** Used in discretionary and systematic trading for mean reversion  
- **ATR:** J. Welles Wilder, *New Concepts in Technical Trading Systems*  
- **Polymarket:** [polymarket.com](https://polymarket.com), [docs.polymarket.com](https://docs.polymarket.com)  
- **OKX API:** [okx.com/docs](https://www.okx.com/docs) – public candles, no account needed

---

*Last updated: March 2025*
