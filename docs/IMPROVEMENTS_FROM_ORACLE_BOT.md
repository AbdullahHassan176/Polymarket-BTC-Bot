# Performance Improvements & Ideas from Oracle Latency Arbitrage Bot

Ways to improve the reversal bot, including patterns from **Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot** (Oracle bot).

---

## 1. From our reversal bot (already available)

- **Analysis-driven tuning:** Run `analyze_tier_performance.py`, `analyze_reversal_entry_thresholds.py`, `analyze_reversal_entry_modes.py`, `analyze_reversal_frequency.py` and tune tiers, entry thresholds, and entry mode from results.
- **Reconciliation:** Use `reconcile_polymarket_history.py` and `compute_pnl_from_polymarket_history.py` so PnL and win rates reflect real outcomes (especially after CLEARED_STALE).
- **Exit timing:** Tiered TP 25–35¢, stuck-bid timeout 10s, exit-at-market last 60s (already configured). Keep position size modest while capture rate is ~31%.
- **Model EV gate:** Optional `MODEL_EV_GATE_ENABLED` to only take trades where model edge clears a cost buffer.

---

## 2. Ideas we can take from the Oracle bot

### A. Exit and time discipline

| Idea | Oracle bot | Reversal bot adoption |
|------|------------|------------------------|
| **Max position duration** | `MAX_POSITION_DURATION_SECONDS` (e.g. 280): force exit at bid after N seconds in the window. | Add `REVERSAL_MAX_POSITION_DURATION_SECS` (e.g. 240). If we’ve been in the position longer than that, exit at current bid (TP/SL). Reduces holding into last seconds when reversal hasn’t come. |
| **Min exit profit %** | `MIN_EXIT_PROFIT_PCT` (e.g. 0.30): don’t exit early unless we lock at least 30% profit. | Optional `REVERSAL_MIN_EXIT_PROFIT_PCT`: when exiting at market (last N sec or stuck bid), only treat as TP if (bid − entry)/entry ≥ this; else treat as SL or skip. Avoids locking tiny profits when we could hold for resolution. |
| **Time-based exit** | Reversal engine uses `REVERSAL_TAKE_PROFIT_PRICE` (e.g. 0.50); stop-loss on 0.10% reversal through window open. | We already have TP tiers and stuck-bid; adding max duration gives a hard “don’t hold past X seconds” rule. |

### B. Sell execution (liquidity)

| Idea | Oracle bot | Reversal bot adoption |
|------|------------|------------------------|
| **Sell at bid − ticks** | `_submit_sell_order`: first try at **bid − 2 ticks**, on failure retry at **bid − 1 tick**. Rounds size down to avoid overselling. | When placing SELL for TP/SL or chunked exit, try **bid** first (current); on failure retry at **bid − 1 tick** (e.g. 0.01) to improve fill in thin books. Option: config `REVERSAL_SELL_AGGRESSION_TICKS` (0 = at bid, 1 = bid−1¢, 2 = bid−2¢). |
| **Chunked taker** | `position_builder`: `TAKER_CHUNK_SIZE_USDC`, `TAKER_INTERVAL_SECONDS`, sweep multiple ticks. | We already have `MAX_SELL_ORDER_USDC` and same-tick retry. Could add a second retry at a slightly lower price (e.g. bid − 0.01) for the remainder. |

### C. Entry filters (quality over quantity)

| Idea | Oracle bot | Reversal bot adoption |
|------|------------|------------------------|
| **Ask / price cap** | `ASK_MAX`, `ASK_MAX_EARLY`, per-asset `*_ASK_MAX`: no entry if ask above cap (price discipline). | We use entry tiers (15¢, 18¢). Could add `REVERSAL_MAX_ENTRY_ASK`: if the *other* side’s ask is above this (e.g. 0.65), skip—market may be too one-sided. |
| **Per-asset thresholds** | `*_MIN_EDGE_THRESHOLD`, `*_ASK_MAX` per asset; calibration script suggests values. | We have per-asset `reversal_entry_tiers`. Add optional per-asset `reversal_min_entry_price` or caps from a small “calibrate” run (e.g. from `analyze_reversal_entry_thresholds` output). |
| **Consecutive loss halt** | `MAX_CONSEC_LOSSES` per asset: stop trading that asset after N losses in a row. | Optional `MAX_CONSEC_LOSSES_PER_ASSET`: after N full LOSS/SL in a row for that asset, skip new entries for that asset until next day or manual reset. |

### D. Resolution and stale

| Idea | Oracle bot | Reversal bot adoption |
|------|------------|------------------------|
| **Resolution timeout** | If resolution not seen within `RESOLUTION_MAX_WAIT_S`, record outcome as `"timeout"` and treat as loss (no hidden losses). | We have CLEARED_STALE. Option: after CLEARED_STALE, run a background or manual “timeout recovery” that re-checks Gamma/API and backfills WIN/LOSS when resolution appears later. |
| **Auto-claim / redeem** | Win: auto-claim via `ctf_redeem` (Gnosis Safe); retry once after 10s. | We have `lib/ctf_redeem.py` and `claim_unclaimed.bat`. Ensure we run claim after sessions; optional: auto-claim on WIN in bot (if using proxy wallet). |
| **Signals + outcome backfill** | Trades/signals keyed by asset, market_id; `_update_signal_outcome` backfills outcome in signals CSV. | We have trades.csv with outcome. Optional: signals_evaluated.csv with outcome column updated when we get resolution, for better calibration. |

### E. Operational and analysis

| Idea | Oracle bot | Reversal bot adoption |
|------|------------|------------------------|
| **Calibrate thresholds** | `calibrate_thresholds.py`: MIN_EDGE and ASK_MAX per asset per window from historical skip/fill data. | Script that reads `logs/trades.csv` + `signals_evaluated.csv` and suggests `REVERSAL_ENTRY_TIERS`, `REVERSAL_MIN_ENTRY_PRICE`, or `REVERSAL_MAX_ENTRY_ASK` per asset (e.g. from PnL by entry bucket). |
| **Run dir + run ID** | Logs under `logs/runs/<RUN_ID>/` for each start; decisions, price_paths, pnl_summary. | Optional: on start, create `logs/runs/<date_time>/` and tee or copy bot.log + trades for that run for easier per-run analysis. |
| **Dashboard** | HTTP API + UI at localhost:8765; trades from CSV; process check. | We already have `dashboard_bot.bat` and dashboard; keep aligning with trades.csv and state. |

---

## 3. Suggested implementation order

1. **High impact, low effort**
   - **Max position duration:** Add `REVERSAL_MAX_POSITION_DURATION_SECS`; in the open-position loop, if `secs_elapsed_since_entry >= this`, exit at bid (TP/SL). Reduces “hold to resolution” when reversal never came.
   - **Sell retry at bid − 1 tick:** In `_sell_tokens_chunked` or `real_close_early`/`real_close_partial`, on first failure retry the same chunk at `price - 0.01` once before giving up.

2. **Medium effort**
   - **Min exit profit %:** When exiting at market (last N sec or stuck bid), only count as TP if `(bid - entry) / entry >= REVERSAL_MIN_EXIT_PROFIT_PCT`; otherwise exit as SL or “at market” with neutral PnL so we don’t lock 5% and then miss a real reversal.
   - **Optional REVERSAL_MAX_ENTRY_ASK:** Skip reversal entry if the opposite side’s ask > threshold (avoid one-sided, illiquid windows).

3. **Calibration and ops**
   - **Calibrate script:** Thin wrapper around `analyze_reversal_entry_thresholds` + `analyze_reversal_frequency` that prints suggested config lines (entry tiers, min entry, max ask).
   - **Max consecutive losses per asset:** Optional halt after N losses in a row per asset (state in state_*.json or a small JSON file).

---

## 4. References

- **Oracle bot:** `D:\Experimentation\Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot`  
  Key files: `scripts/live_test.py` (config, execution, resolution), `lib/position_builder.py` (chunking, sizing), `scripts/calibrate_thresholds.py`, `scripts/claim_unclaimed.py`.
- **Reversal bot:** `docs/MAX_PROFIT_IDEAS.md`, `docs/REVERSAL_STRATEGY.md`, `docs/PERFORMANCE_ANALYSIS.md`, `ai.md`.
