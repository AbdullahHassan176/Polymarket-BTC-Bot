# How the “lowest” tier is determined per asset

## Dynamic lowest (last N windows)

When **`REVERSAL_USE_DYNAMIC_LOWEST=True`** (default), the lowest tier is **computed from live data**:

- For each 5‑minute window the bot tracks the **minimum YES price** and **minimum NO price** seen during that window.
- When the active market switches (new window), that window’s `(min_yes, min_no)` is appended to a **per-asset** history.
- The **last N windows** (default N=3, `REVERSAL_DYNAMIC_LOWEST_WINDOWS`) are used: among all those mins, take the **global minimum** (lowest value across YES and NO over the last 3 windows).
- That value is **rounded down** to the nearest allowed tier in `REVERSAL_DYNAMIC_LOWEST_TIERS` (e.g. `[0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]`) and capped by `REVERSAL_MIN_ENTRY_PRICE`.
- The result is **`dynamic_lowest_tier`**: the bot only enters when price is at or below this tier (with `REVERSAL_ENTER_AT_LOWEST_ONLY=True`).

State is stored in **`logs/reversal_window_mins.json`** (per asset). Until at least one full window has been observed, the bot falls back to the config-based lowest (min of entry tiers).

## Config-based lowest (fallback)

When **`REVERSAL_USE_DYNAMIC_LOWEST=False`** or before any window history exists:

- **Entry tiers** (where we are allowed to enter) come from:
  1. **Per-asset:** `config.ASSETS_CONFIG[TRADING_ASSET]["reversal_entry_tiers"]` if present (e.g. `[0.10, 0.15, 0.18]`).
  2. **Global fallback:** `config.REVERSAL_ENTRY_TIERS` if the asset has no `reversal_entry_tiers`.

- **Lowest tier** = `min(reversal_entry_tiers)` for that asset.

## Setting tiers per asset

Edit `scripts/config.py`, `ASSETS_CONFIG`:

- **Same for all assets (current default):** each asset has `"reversal_entry_tiers": [0.10, 0.15, 0.18]` → lowest = 10¢ everywhere.
- **Asset-specific:** e.g. if SOL often dips to 8¢, set SOL only:
  ```python
  "SOL": { ..., "reversal_entry_tiers": [0.08, 0.12, 0.15] },  # lowest = 8¢ for SOL
  ```
- **Omit key** for an asset to use the global `REVERSAL_ENTRY_TIERS` for that asset.

Ensure every tier in `REVERSAL_TP_BY_ENTRY` exists for the entry prices you use (e.g. if you add 0.08 for SOL, add a row for 0.08 in `REVERSAL_TP_BY_ENTRY`).

## Deriving “lowest” from data

To choose a data-driven lowest (and full tiers) per asset:

1. **By asset:** run your analysis on that asset’s trades only (e.g. filter `logs/trades.csv` and `logs/paper_trades.csv` by market/question containing the asset name).
2. **Entry price distribution:** bucket closed trades by `entry_price` (e.g. 0–5¢, 5–10¢, 10–15¢, 15–20¢). See which buckets get the most fills and best PnL.
3. **“Typical low”:** e.g. if most fills for BTC are at 10¢ and rarely at 18¢, using lowest = 10¢ (and optionally only 10¢/15¢) is consistent with the data. If SOL often fills at 8¢, consider `reversal_entry_tiers` for SOL that include 0.08 and set lowest = 0.08 for SOL.
4. **Scripts:** `analyze_reversal_entry_thresholds.py` (entry buckets, near-misses) and `analyze_performance.py` (by asset) help. Run them on asset-specific exports or filter by question/market to get per-asset stats.
5. **Bid traces:** if `LOG_REVERSAL_BID_TRACE` is on, use bid-trace or tier analysis to see how low YES/NO typically go per market; set the lowest tier at or below that level so we don’t buy too early (e.g. 18¢ when it usually hits 10¢).

Summary: **lowest per asset = min of that asset’s `reversal_entry_tiers`** (or global `REVERSAL_ENTRY_TIERS`). You determine the list from config and/or from the data steps above.
