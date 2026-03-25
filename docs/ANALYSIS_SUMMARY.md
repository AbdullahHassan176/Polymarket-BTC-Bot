# Analysis Summary (post stop)

**Date:** March 2026 (bot stopped)

---

## 1. What the bot logs say

| Source | Metric | Value |
|--------|--------|--------|
| **trades.csv** (REAL closes) | Sum pnl_usdc | **+173.89** (BTC live) |
| **drawdown_report** | Cumulative PnL | **+198.72** |
| **drawdown_report** | Max drawdown | $13.98 |
| **drawdown_report** | Last 16 non-stale | 10 wins, 6 losses |
| **state.json** (BTC) | cumulative_pnl_usdc | 103.36 |
| **state_SOL.json** | cumulative_pnl_usdc | 13.75 |

**Live outcomes (trades.csv):** 256 close events — **81 CLEARED_STALE** (~32%), 37 SL, 4 LOSS, 3 WIN, rest TP_TIER / TP_STUCK_BID / TP_LOCK_50.

**Paper:** ETH +140, XRP +49, SOL +186, DOGE +10 (simulated; not real cash).

---

## 2. Reality check (Polymarket History)

When we ran **true PnL from Polymarket** on your export (`Polymarket-History-2026-03-15 (1).csv`):

- **True PnL (Buys vs Sells+Redeems):** **-$38.47**
- By asset: BTC **-$13.91**, SOL **-$24.55**
- **Difference vs bot:** Bot showed +$176; Polymarket cash flow was -$38 → **~$214 gap**

So the **wallet was down** over that period; the bot’s recorded PnL was **overstated** (e.g. partial TP logged as profit, then remainder went stale/lost; CLEARED_STALE recorded as $0 instead of actual loss; or double-counting).

---

## 3. Main issues

1. **CLEARED_STALE (81)** — Many positions closed without resolution; bot logged $0. True outcome (win/loss) often unknown or worse; contributes to overstatement.
2. **Partial TP then remainder** — Taking TP on part of a position then leaving the rest to resolve/stale can show profit in logs while the full position lost.
3. **Entry timing** — Late entries (before we tightened last-60s and first-120s) increased exposure to bad resolutions and stale.
4. **Multi-asset in one file** — BTC + SOL (and paper) in same `trades.csv` complicates reconciliation; state is per-asset (BTC, SOL) but one export is mixed.

---

## 4. Recommendations (when/if you restart)

- **Trust wallet, not state** — Use `compute_pnl_from_polymarket_history.py` on fresh exports to see real PnL.
- **Reduce CLEARED_STALE** — Exit at market in last 45–60s, lock_50 mode, so fewer positions rely on resolution.
- **Stricter entry** — Only first 120s, no last 60s (already in config); consider smaller size or fewer assets until PnL matches Polymarket.
- **Reconcile often** — Run `reconcile_polymarket_history.py` after each export to fix CLEARED_STALE → actual outcome where possible.

---

## 5. Commands

```bash
# Bot-recorded view
python scripts/analyze_performance.py
python scripts/drawdown_report.py --live

# True PnL from wallet (export from Polymarket first)
python scripts/compute_pnl_from_polymarket_history.py "path/to/Polymarket-History.csv" --trades logs/trades.csv

# Fix CLEARED_STALE vs Polymarket redeems
python scripts/reconcile_polymarket_history.py "path/to/Polymarket-History.csv" [--apply]
```
