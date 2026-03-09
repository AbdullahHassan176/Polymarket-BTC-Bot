# Improvements from Paper-Trading Logs

Based on analysis of `logs/trades_12hr_hybrid.csv` and `logs/bot.log`.

## What the logs showed

- **Win rate**: ~15% (TP) vs ~85% (SL) — almost all exits are early (TP or SL); none held to resolution.
- **Profitability**: Still positive because **avg win (+$6.48) > avg loss (-$2.55)** (asymmetric payoff).
- **Entry price vs PnL** (by entry price bucket):
  - **5¢**: +$90 (best) — very cheap entries work.
  - **10¢, 15¢, 30¢, 35¢, 50¢**: negative PnL — many SLs.
  - **20¢, 25¢, 40¢, 55¢**: positive PnL.
- **Overtrading**: Multiple entries in the **same** 5‑min window (re‑entry after each SL/TP), which can add noise and spread cost.

---

## Recommended changes to improve profits and win rate

### 1. Favor cheaper contrarian entries

- **CONTRARIAN_MAX_PRICE**: `0.35` → **`0.25`** (or `0.20`).
- Only buy the “cheap” side when it’s very cheap; 30–35¢ entries are losing on average.

### 2. Softer or dynamic stop-loss for cheap entries

- **Current**: SL = 0.25 for all trades → many 10–20¢ entries get stopped out before a bounce.
- **Options** (implement one):
  - **Dynamic SL**: e.g. `SL = max(0.25, 0.5 * entry_price)` so we don’t cut 8¢ entries at 25¢.
  - **No SL for very cheap**: If `entry_price < 0.15`, disable SL and hold to TP or resolution.
  - **Tighter SL for cheap**: e.g. SL = 0.15 when `entry_price < 0.15` so we only exit on a real washout.

### 3. Tighten fallback tier (fewer marginal trades)

- **FALLBACK_PRICE_MAX**: `0.55` → **`0.45`**.
- **FALLBACK_IBS_LOW**: `0.20` → **`0.15`**; **FALLBACK_IBS_HIGH**: `0.80` → **`0.85`**.
- Fallback was firing often; narrowing price and IBS bands should cut weak setups and improve win rate.

### 4. Fewer trades in high volatility

- **ATR_THRESHOLD**: `0.035` → **`0.030`** (skip when ATR% &gt; 3%).
- Reduces trading in choppy markets where direction is noisy.

### 5. (Optional) One trade per 5‑min window

- Only allow **one** position per market window; no re‑entry in the same window after TP/SL.
- Reduces overtrading the same window and makes results easier to interpret (one decision per window).

### 6. (Optional) Hold very cheap entries to resolution

- If `entry_price <= 0.10`, consider **disabling TP/SL** and holding to resolution ($1 or $0).
- Maximizes payoff when we’re right; 5¢ entries in the logs were very profitable.

### 7. Track which strategy tier fired (for tuning)

- Add a column or log field: **strategy_tier** = `contrarian` | `momentum` | `fallback` | `late_window`.
- Enables per-tier PnL and win-rate analysis in future runs.

---

## Summary table

| Change                         | Config / code              | Goal                    |
|--------------------------------|----------------------------|-------------------------|
| Cheaper contrarian only        | CONTRARIAN_MAX_PRICE 0.25  | Better payoff, fewer SLs |
| Softer/dynamic SL for cheap    | execution.py + config      | Fewer SLs on 5–15¢      |
| Tighter fallback               | FALLBACK_* in config       | Fewer weak fallback SLs |
| Stricter volatility filter     | ATR_THRESHOLD 0.03         | Fewer bad windows       |
| One trade per window           | risk.py / bot.py           | Less overtrading        |
| Hold cheap to resolution       | execution / config         | Bigger wins on 5–10¢    |
| Log strategy tier              | execution + CSV            | Better tuning data       |

Start with **1, 2, and 3**; then add **4** and **5** if you want fewer, higher-quality trades. **6** and **7** are optional for max payoff and analysis.
