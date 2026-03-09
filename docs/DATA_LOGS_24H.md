# 24-Hour Data Logs — CSV Layout and Joins

When you run `python run_24hr_paper.py`, the bot writes three CSVs for strategy refinement.

## 1. `logs/trades_24h.csv` — Every trade (OPEN + CLOSE)

- **One row per event**: each OPEN and each CLOSE.
- **Columns**: timestamp, action (OPEN/CLOSE), mode, question, condition_id, direction, entry_price, size_usdc, num_tokens, outcome (WIN/LOSS/TP/SL), pnl_usdc, pnl_pct, btc_spot, starting_balance_usdc, trade_pnl_usdc, cumulative_pnl_usdc, current_balance_usdc, strategy_tier.
- **Use**: Full trade lifecycle and PnL. For “every trade that was made,” take OPEN rows (or pair OPEN with CLOSE by condition_id).

## 2. `logs/trade_entries_24h.csv` — One row per trade (full signal at entry)

- **One row per trade**: written when we **enter** (paper or real).
- **Columns**: timestamp, condition_id, question, direction, entry_price, size_usdc, num_tokens, strategy_tier, **signal_reason**, **ema_fast**, **ema_slow**, **atr_pct**, **ibs**, yes_price, no_price, btc_spot, secs_remaining, in_late_window, window_start_btc.
- **Use**: “Every signal that was used to trigger a trade.” Join to `trades_24h.csv` on **condition_id** and use CLOSE rows to get outcome and pnl_usdc so you can judge whether that signal was good.

**Join example (outcome per trade entry):**
- From `trade_entries_24h.csv`: keep condition_id, strategy_tier, signal_reason, entry_price, and indicator columns.
- From `trades_24h.csv`: keep rows with action=CLOSE; keep condition_id, outcome, pnl_usdc.
- Join on condition_id → each trade entry gets its outcome and PnL.

## 3. `logs/signals_evaluated_24h.csv` — Every signal (traded or not)

- **One row per evaluation**: every time the bot runs the strategy (no open position, active market), so roughly every 10s per window.
- **Columns**: timestamp, condition_id, **action** (BUY_YES/BUY_NO/SKIP), **reason**, tier, yes_price, no_price, ema_fast, ema_slow, atr_pct, ibs, secs_remaining, in_late_window, **traded** (Y/N), **risk_block_reason**.
- **Use**: “Every signal recorded to know whether the signal was good or if we need to improve.” Filter `traded=Y` to get signals that led to a trade (then join to trades for outcome). Use `traded=N` and **risk_block_reason** to see why we didn’t trade (e.g. “already traded this window”, “daily limit”, “model edge too small”).

**Refinement ideas:**
- By **tier** + **reason**: compare win rate / PnL for contrarian vs momentum vs fallback vs late_window.
- By **traded**: count how often we had BUY_YES/BUY_NO but didn’t trade (risk_block_reason) vs did trade.
- By **condition_id**: group by window; see how many evaluations were SKIP vs one BUY that we traded.

## Summary

| File                   | Rows              | Join key   | Purpose                                      |
|------------------------|-------------------|------------|----------------------------------------------|
| trades_24h.csv         | OPEN + CLOSE      | condition_id | PnL, outcome, balance                        |
| trade_entries_24h.csv  | One per trade     | condition_id | Signal that triggered each trade             |
| signals_evaluated_24h.csv | Every evaluation | condition_id | All signals; traded Y/N; refine strategy     |
