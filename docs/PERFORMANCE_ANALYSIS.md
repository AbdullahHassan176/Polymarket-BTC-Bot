# Performance Analysis: BTC Live vs Paper (ETH, XRP, SOL, DOGE)

**Analysis date:** March 2025 (from `logs/trades.csv` and `logs/paper_trades.csv`)  
**Bot config:** Reversal strategy, single/pyramid entry; 5-min Up/Down markets.

---

## 1. BTC live trading (real money)

| Metric | Value |
|--------|--------|
| **Total PnL (USDC)** | **+158.01** |
| Unique markets traded | 77 |
| Open events | 123 |
| Close events (all exits) | 208 |

### Outcome breakdown (close events)

- **TP_TIER_*** (partial take-profit): 122 events — pyramid exits at 15¢–50¢.
- **SL** (stop-loss / partial exit): 31 — often small profit or small loss.
- **CLEARED_STALE**: 68 — position cleared without full resolution (e.g. after market end).
- **LOSS**: 3 — full loss.
- **WIN**: 3 — full win.
- **TP_STUCK_BID**: 4 — exit at stuck bid.

**Takeaways (BTC live):**

- Strategy is **profitable** over this sample: +158 USDC.
- Most risk is managed via **partial exits** (TP tiers + SL), not full WIN/LOSS.
- Many **CLEARED_STALE** — worth checking reconciliation/timing so more positions resolve as WIN/LOSS or TP/SL.
- Only 6 full WIN/LOSS; the rest are tiered or SL exits, which fits the reversal pyramid design.

---

## 2. Paper trading (ETH, XRP, SOL, DOGE)

| Asset | Unique markets | Open events | Close events | **Total PnL (USDC)** |
|-------|----------------|-------------|--------------|----------------------|
| **ETH** | 66 | 109 | 141 | **+96.10** |
| **XRP** | 68 | 117 | 142 | **+54.53** |
| **SOL** | 64 | 112 | 178 | **+179.90** |
| **DOGE** | 4 | 8 | 23 | **+9.88** |
| **Total paper** | — | — | — | **+340.41** |

### Per-asset notes

- **SOL (paper):** Highest paper PnL (+179.90), most TP_TIER and TP_STUCK_BID activity; similar structure to BTC.
- **ETH (paper):** +96.10; many SL and TP_TIER; 19 CLEARED_STALE.
- **XRP (paper):** +54.53; SL and TP_STUCK_BID frequent; 20 CLEARED_STALE.
- **DOGE (paper):** Only **4 unique markets**; +9.88. Likely fewer 5-min DOGE markets or lower coverage; sample too small to judge strategy.

### Outcome mix (paper, all four assets)

- **SL** and **TP_TIER_*** dominate — same pyramid/partial-exit behavior as BTC.
- **TP_STUCK_BID** and **CLEARED_STALE** are material; same reconciliation/timing caveats as BTC.

---

## 3. Summary and feasibility

| Segment | PnL (USDC) | Comment |
|---------|------------|--------|
| **BTC (live)** | +158.01 | Real money; profitable over 77 markets. |
| **Paper (ETH+XRP+SOL+DOGE)** | +340.41 | Simulated; all four assets positive. |

**What this suggests:**

1. **BTC live** is making money with the current reversal logic and sizing in this period.
2. **Paper** results for ETH, XRP, and SOL are positive and consistent with the same style (reversal, tiers, SL). They are **feasibility-supportive** for extending the same strategy to those assets in live trading, with the usual caveats (slippage, fill quality, resolution behavior).
3. **DOGE** has very little data (4 markets); run longer paper (or more DOGE markets) before considering live DOGE.
4. **CLEARED_STALE** is high on both live and paper; reducing it (e.g. resolution/reconciliation and timing) could improve clarity and possibly realized PnL.

**Recommendation:**  
- Keep **BTC live** as is, monitor drawdown and CLEARED_STALE.  
- Treat **ETH, XRP, SOL** as **candidates for live** after more paper time and/or stress periods; **DOGE** only after more paper data.

**Weekly checks:**  
- `python scripts/analyze_performance.py` — PnL and outcomes by asset  
- `python scripts/drawdown_report.py --live` — cumulative PnL, max drawdown, recent win/loss streak  

**True PnL from wallet:**  
- Export Polymarket History (Profile → Trading History → Export CSV).  
- `python scripts/compute_pnl_from_polymarket_history.py "path/to/Polymarket-History.csv" --trades logs/trades.csv` — cash-flow PnL (Buys vs Sells+Redeems) and comparison to bot’s sum of recorded PnL. Use this to check if the bot’s numbers match reality.  

**Current run (post-analysis):**  
- **Live:** BTC + SOL (same script: `scripts/launch/run_btc_live_others_paper.ps1`).  
- **Paper:** ETH, XRP, DOGE.  
- Optional smaller size for SOL: set `RISK_PER_TRADE_ALT_USDC` in config (e.g. 1.5 for half of 3.0).  
- Resolution: quicker retries and longer retries before CLEARED_STALE to reduce stale count.

---

## 4. Improvements based on recent performance (March 2026)

**Latest stats (from `analyze_performance.py` + `drawdown_report.py --live`):**
- BTC live: +164 USDC, 83 markets, **72 CLEARED_STALE** out of 225 close events (~32%).
- Paper: ETH +94, XRP +58, SOL +186, DOGE +10.
- Drawdown: max $13.98; last 18 non-stale closes: 15 wins, 3 losses.

**Recommended improvements:**

1. **Reduce CLEARED_STALE**  
   - **Exit at market in last 30s** is already in config (`REVERSAL_EXIT_AT_MARKET_LAST_SECS = 30`). Ensure the bot has been **restarted** so this (and lock_50/lock_100) runs.  
   - Optionally increase to **45–60 seconds**: set `REVERSAL_EXIT_AT_MARKET_LAST_SECS = 45` so we exit at current bid earlier and avoid holding into resolution.  
   - Consider **`REVERSAL_EXIT_MODE = "lock_50"`**: close the full position as soon as bid ≥ entry×1.5, so we take profit earlier and fewer positions are still open when the window ends.

2. **Stuck-bid timeout**  
   - `REVERSAL_STUCK_BID_TIMEOUT_SECS` is 15. Try **10** so we exit at bid sooner when we’re stuck below the next TP tier.

3. **Reconciliation**  
   - Run `python scripts/reconcile_polymarket_history.py <Polymarket-History.csv> [--apply]` periodically. Some CLEARED_STALE may be resolvable as WIN/LOSS after Gamma updates.

4. **Entry level**  
   - Entries at 12–19¢ are from dynamic lowest (last 3 windows). If you want to wait for deeper dips only, lower `REVERSAL_DYNAMIC_LOWEST_TIERS` or add a max tier (e.g. only use 0.10 and below) in strategy.

5. **Duplicate closes**  
   - If BTC and SOL both log to `trades.csv`, the same market can appear twice. Ensure each process only manages its own asset’s positions (no cross-asset duplicate opens).
