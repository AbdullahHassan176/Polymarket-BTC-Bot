# Enhancing Contrarian with Forecasting and “Oracle” Arbitrage

## Can We “Know What Will Happen”?

- **Resolution is on-chain**: The 5-min market resolves YES/NO via Chainlink (or UMA). No one gets the result before the window closes, so there is **no true oracle arbitrage** (no way to trade on “known” future outcomes).
- **Model-as-oracle**: We can treat a **forecasting model** as our “oracle.” If the model implies P(UP) = 65% and Polymarket prices YES at 40¢, we buy YES (market is cheap vs our model). That’s “arbitrage” only in the sense of **model vs market**, not future knowledge.

## Ways to Enhance Contrarian

### 1. Model-implied fair value (implemented)

Use a **short-horizon digital option** view of the 5-min window:

- **Event**: BTC close ≥ window open → YES wins.
- **Inputs**: window open (strike), current spot, time to expiry (secs left), volatility from ATR.
- **Formula**: Black–Scholes digital probability P(close ≥ strike) with strike = window open, time = remaining seconds (in years), sigma from ATR (e.g. annualized via `atr_pct * VOL_ANNUALIZATION`).

Then:

- **Contrarian + model**: Only buy YES when `model_P_yes > yes_price + MIN_EDGE` (e.g. 5¢). Only buy NO when `model_P_no > no_price + MIN_EDGE`. So we still act contrarian (buy the cheap side) but only when the **model agrees** the side is cheap.

Config: `CONTRARIAN_USE_MODEL_FAIR_VALUE`, `CONTRARIAN_MIN_EDGE`, `BTC5M_VOL_ANNUALIZATION` (see `btc_5m_fair_value.py`).

### 2. Better forecasting models (options)

| Approach | Description | Pros / Cons |
|----------|-------------|-------------|
| **B-S digital (above)** | Vol + optional drift from momentum | No training; uses existing ATR; interpretable. |
| **Momentum as drift** | Use EMA trend to add a small drift term in the B-S formula (or separate P(up) tilt). | Captures short-term trend; still no ML. |
| **ML classifier** | Train a model (e.g. sklearn, lightgbm) on historical 5-min OHLCV → UP/DOWN. Use predicted P(UP) as fair value. | Can capture non-linear structure; needs labels and retraining. |
| **Order book / flow** | If we had CLOB bid/ask depth or flow, use imbalance as signal. | Polymarket 5-min is thin; CLOB gives mid/best bid/ask but not full book. |
| **External “oracles”** | No public API gives “P(BTC up in next 5 min).” Derivatives (funding, term structure) are longer-dated. | Not directly usable for 5-min. |

### 3. Relation to existing arbitrage module

- **`arbitrage/`** compares **Polymarket price-target markets** (e.g. “Google hit $X by 2026”) to **option-implied** fair value. That uses listed options and Black–Scholes for those events.
- **BTC 5-min**: No listed options expire in 5 minutes. So we **cannot** use the same option-implied fair value. Instead we use a **volatility-based model** (ATR → sigma → B-S digital) as our “fair value” for the 5-min binary. That is implemented in `btc_5m_fair_value.py` and wired into contrarian in `strategy.py`.

## Summary

- **No literal oracle**: We don’t know the outcome in advance.
- **Model-as-oracle**: Use B-S 5-min digital (and optionally momentum-as-drift or ML) to get model P(UP). Trade when Polymarket price is cheap vs that model.
- **Contrarian enhancement**: Keep contrarian logic (IBS + cheap side), but add a filter: only take the trade if the model-implied probability is sufficiently above the market price (minimum edge). This reduces bad contrarian bets when the market is efficiently pricing the move.

See `btc_5m_fair_value.py` for the implementation and `config.py` for `CONTRARIAN_USE_MODEL_FAIR_VALUE` and related knobs.
