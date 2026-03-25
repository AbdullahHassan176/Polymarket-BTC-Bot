# Pure reversal strategy (e.g. “bet $5 when UP hits 10¢”)

## When the strategy works (thesis)

The edge depends on a **meaningful chance of (at least partial) full reversal**. We buy when one side is cheap (e.g. 15¢); profit comes from the **move from that level back toward 50¢ or 100¢**. If the market rarely reverses and usually resolves against us or flat, the strategy loses. So it works when there is enough probability of a complete (or near-complete) reversal that the gain between entry (e.g. 15¢) and exit (50¢, 100¢, or resolution) is +EV. Lock_50 / tiered TP capture part of that move; the underlying assumption is that the move exists often enough to justify buying the dip.

## Two ways to profit

### 1. Hold to resolution (bet on outcome)

You need the **true** win rate when price = 10¢ to exceed 10% to be +EV. The bot can test that empirically.

### 2. Trade the spread (sell into mean reversion) — **recommended**

**Idea:** We don’t need to be right about UP vs DOWN. We buy when the market has pushed one side to 10¢, then **sell when the market doubts and regresses back toward 50/50**. Profit comes from the **price move** (10¢ → 50¢), not from the final resolution.

- **Buy** when YES or NO ≤ 10¢.
- **Sell** when the best bid for our side reaches **50¢** (or `REVERSAL_TAKE_PROFIT_PRICE`). That’s when “people suspect a reversal” and odds move back toward 50/50.
- If the market never regresses, we can hold to resolution (and maybe lose) or use a stop; the main edge is capturing the frequent mean reversion in **price**.

So “surely there are many times where the market doubts and regresses back to the mean” is exactly what we’re trading: we take profit when that regression happens (bid → 50¢), and we don’t rely on being right about the final outcome.

## Adjusting to the data (reversal frequency)

From `analyze_reversal_frequency.py` on recent trades (entry ≤ 15¢):

- **~31%** of trades “capture” a reversal (WIN, TP, TP_TIER ≥ 35¢, or TP_STUCK_BID).
- **~4%** get a strong reversal (WIN, TP, or TP_TIER ≥ 50¢).

So full reversal to 50¢+ is rare; most edge comes from **partial** moves (e.g. 15¢ → 25–35¢). Adjust the strat to match:

1. **Lock gains sooner** — Stay on **lock_50** (exit full position at 50% profit, e.g. 15¢ → 22.5¢). Don’t rely on 50¢; bank the move you usually get.
2. **Tiered TP: take more at 25–30¢** — If you use tiered mode, put more % at 25¢ and 30¢, less at 45¢/50¢ (see `REVERSAL_TP_BY_ENTRY` for 0.15 / 0.18). That way you don’t depend on the rare 50¢.
3. **Stuck-bid and last seconds** — Keep `REVERSAL_STUCK_BID_TIMEOUT_SECS` at 10 (or lower to 5–7) so you exit at bid when price isn’t reaching the next tier. Keep `REVERSAL_EXIT_AT_MARKET_LAST_SECS` at 45–60 so you always exit at bid in the last minute instead of holding to resolution.
4. **Size** — With ~69% of trades not capturing a good reversal, keep position size modest; avoid increasing size until you see sustained +EV.
5. **Optional: be more selective** — Add filters (e.g. only enter when recent windows showed a reversal, or when volatility is high) to trade less often but with better odds; implement via strategy or model gate if desired.

Run `python scripts/analyze_reversal_frequency.py` (and `--paths` once you have window-path data) regularly to refresh these rates and re-tune.

## Config

- `STRATEGY_MODE = "reversal"` — run only this strategy.
- Or `STRATEGY_MODE = "hybrid"` and `REVERSAL_ENABLED = True` — try reversal first, then contrarian/momentum/fallback.
- `REVERSAL_PRICE_THRESHOLD` — trigger when YES or NO ≤ this (default 0.10).
- `REVERSAL_BET_USDC` — stake per trade (default 5.0).
- **`REVERSAL_TAKE_PROFIT_PRICE`** (default **0.50**) — for reversal-tier positions, **sell when best bid ≥ this**. So we buy at 10¢ and take profit when the market regresses toward 50/50. Profit = (0.50 − 0.10) × num_tokens (e.g. 5× price gain per share).

## Testing

Paper-run with reversal only, then use the tier analyzer on the log:

```powershell
# In config: STRATEGY_MODE = "reversal", REVERSAL_PRICE_THRESHOLD = 0.10, REVERSAL_BET_USDC = 5
python bot.py --paper
# After run:
python analyze_tier_performance.py --csv logs/trades.csv --min-sample 10
```

If the **reversal** tier’s win rate is above the threshold (e.g. > 10% when threshold = 0.10) and expectancy is positive, the strategy is +EV in your sample; if not, the market is not mispriced enough at that level.
