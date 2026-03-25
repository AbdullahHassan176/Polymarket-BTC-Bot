# Ideas to Maximize Profits

Quick reference: levers you can tune (config, process, analysis) to improve PnL. Test on paper or small size first where noted.

---

## 1. Use the analysis scripts (data-driven)

- **Tier performance**  
  `python scripts/analyze_tier_performance.py --csv logs/trades.csv`  
  Identifies which entry/TP tiers are +EV. Drop or downweight bad tiers in `REVERSAL_TP_BY_ENTRY` / `REVERSAL_TP_TIERS` in config.

- **Entry thresholds**  
  `python scripts/analyze_reversal_entry_thresholds.py --trades logs/trades.csv --signals logs/signals_evaluated.csv`  
  Shows which entry prices (10¢, 15¢, 18¢, etc.) perform best. Restrict `REVERSAL_ENTRY_TIERS` to the best ones.

- **Entry mode (single vs pyramid)**  
  `python scripts/analyze_reversal_entry_modes.py`  
  Recommends single vs pyramid; can improve expectancy and reduce noise.

Run these periodically (e.g. weekly) and adjust config from the results.

---

## 2. Resolution and stale (capture real PnL)

- **Reconcile CLEARED_STALE**  
  Export Polymarket Trading History (Profile → Export), then:  
  `python scripts/reconcile_polymarket_history.py path/to/Polymarket-History.csv [--apply]`  
  Corrects PnL for positions that were force-cleared; gives true win rate and lets you see which markets would have been WIN/LOSS. Reduces “unknown” PnL and improves reporting.

---

## 3. Strategy and exit tuning (config)

- **Late-window entries**  
  `LATE_ENTRY_ENABLED`, `LATE_MIN_MOVE_PCT`, `LATE_MAX_PRICE`  
  Strong moves in the last ~90s often misprice. Slightly lower `LATE_MIN_MOVE_PCT` (e.g. 0.002) = more late entries; higher `LATE_MAX_PRICE` (e.g. 0.92) = allow slightly more expensive late entries. Test on paper first.

- **Stuck-bid exit**  
  `REVERSAL_STUCK_BID_TIMEOUT_SECS` (default 15), `REVERSAL_STUCK_FALLBACK_MIN_BID` (default 0.10)  
  Shorter timeout = lock profit sooner when bid is stuck below next TP. Lower min_bid = exit at worse price but unblock capital. Tune if you see many “almost TP then reversal”.

- **Cheap entries**  
  `REVERSAL_MIN_ENTRY_PRICE` (default 0.08)  
  Try 0.10 to skip 8–10¢ (often illiquid); can improve fill quality and reduce noise.

- **Model EV gate (optional)**  
  `MODEL_EV_GATE_ENABLED = True`, `MODEL_EV_MIN_EDGE`, `MODEL_EV_COST_BUFFER`  
  Only take trades where model edge clears a cost buffer. Reduces low-edge trades; validate on paper first.

---

## 4. Size and capacity

- **Compounding**  
  `RISK_PCT_PER_TRADE` (default 0.10)  
  Slightly higher (e.g. 0.12) when bankroll is growing and drawdown is acceptable; keep 0.10 if you prefer stability.

- **Max concurrent positions**  
  `MAX_CONCURRENT_POSITIONS` (default 3)  
  Increasing to 4–5 captures more windows at once but increases drawdown; only if you’re comfortable with the risk.

- **Alt size**  
  `RISK_PER_TRADE_ALT_USDC`  
  Use for SOL/ETH (e.g. 1.5 or 2.0) to scale alts up once you’re happy with their live results.

---

## 5. More live assets

- Paper: SOL +180, ETH +96, XRP +54. You’re already live on BTC + SOL.
- **Add ETH live** when comfortable (same or `RISK_PER_TRADE_ALT_USDC`). More live books = more opportunities; keep an eye on total exposure and drawdown.

---

## 6. Operational

- **Drawdown and streak**  
  Run weekly:  
  `python scripts/drawdown_report.py --live`  
  and  
  `python scripts/analyze_performance.py`  
  to spot bad streaks and asset-level issues early.

- **Reconciliation**  
  Run Polymarket history reconcile after any period with many CLEARED_STALE so your metrics reflect actual outcomes.

---

**Summary:** Use the analyzers to tune tiers and entry rules, reconcile stales, then tweak late-window/stuck-bid/cheap-entry and size levers in small steps while monitoring drawdown and performance scripts.
