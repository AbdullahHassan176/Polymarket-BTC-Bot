"""
Analyze whether expanding reversal entry to 15c/20c would be worth it.

Uses:
- trades.csv: closed reversal trades (entry_price, outcome, pnl)
- signals_evaluated.csv: all signals - find "near-miss" when cheap side was 10-15c or 15-20c

Reports:
- Performance by entry_price bucket (0-5c, 5-10c)
- Count of missed opportunities at 10-15c, 15-20c (we SKIPped these)
- Suggested thresholds and bet sizes

Usage:
  python scripts/analyze_reversal_entry_thresholds.py
  python scripts/analyze_reversal_entry_thresholds.py --trades logs/trades.csv --signals logs/signals_evaluated.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADES_CSV = ROOT / "logs" / "trades.csv"
SIGNALS_CSV = ROOT / "logs" / "signals_evaluated.csv"


def _safe_float(x, default=0.0):
    try:
        return float(x) if x not in ("", None) else default
    except (TypeError, ValueError):
        return default


def _entry_bucket(entry: float) -> str:
    if entry <= 0.05:
        return "0-5c"
    if entry <= 0.10:
        return "5-10c"
    if entry <= 0.15:
        return "10-15c"
    if entry <= 0.20:
        return "15-20c"
    return "20c+"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default=str(TRADES_CSV))
    ap.add_argument("--signals", default=str(SIGNALS_CSV))
    args = ap.parse_args()

    trades_path = Path(args.trades)
    signals_path = Path(args.signals)

    # --- Closed reversal trades by entry bucket ---
    buckets: dict[str, list[dict]] = defaultdict(list)
    if trades_path.exists():
        with trades_path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("action", "") or "").upper() != "CLOSE":
                    continue
                tier = (row.get("strategy_tier", "") or "").strip()
                if tier != "reversal":
                    continue
                entry = _safe_float(row.get("entry_price"))
                outcome = (row.get("outcome", "") or "").strip().upper()
                pnl = _safe_float(row.get("pnl_usdc"))
                buckets[_entry_bucket(entry)].append({
                    "entry": entry, "outcome": outcome, "pnl": pnl,
                })

    # --- Near-misses from signals_evaluated (cheap side 10-15c or 15-20c) ---
    near_miss_10_15 = 0
    near_miss_15_20 = 0
    near_miss_by_cid_10_15: set[str] = set()
    near_miss_by_cid_15_20: set[str] = set()
    if signals_path.exists():
        with signals_path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                reason = (row.get("reason", "") or "")
                if "reversal" not in reason.lower() or "neither side" not in reason.lower():
                    continue
                yes_p = _safe_float(row.get("yes_price"))
                no_p = _safe_float(row.get("no_price"))
                cheap = min(yes_p, no_p)
                cid = (row.get("condition_id", "") or "").strip()
                if 0.10 < cheap <= 0.15:
                    near_miss_10_15 += 1
                    if cid:
                        near_miss_by_cid_10_15.add(cid)
                elif 0.15 < cheap <= 0.20:
                    near_miss_15_20 += 1
                    if cid:
                        near_miss_by_cid_15_20.add(cid)

    # --- Report ---
    print("=" * 60)
    print("Reversal Entry Threshold Analysis (10c vs 15c vs 20c)")
    print("=" * 60)

    print("\n--- Closed reversal trades by entry bucket ---")
    for bucket in ["0-5c", "5-10c", "10-15c", "15-20c"]:
        rows = buckets.get(bucket, [])
        if not rows:
            print(f"  {bucket}: 0 trades")
            continue
        wins = sum(1 for r in rows if r["outcome"] in ("WIN", "TP"))
        losses = sum(1 for r in rows if r["outcome"] in ("LOSS", "SL"))
        other = len(rows) - wins - losses
        total_pnl = sum(r["pnl"] for r in rows)
        wr = 100 * wins / len(rows) if rows else 0
        print(f"  {bucket}: n={len(rows)} | wins={wins} losses={losses} other={other} | win_rate={wr:.0f}% | total_pnl=${total_pnl:.2f}")

    print("\n--- Near-miss opportunities (we SKIPped, cheap side in range) ---")
    print(f"  Cheap side 10-15c: {near_miss_10_15} signals ({len(near_miss_by_cid_10_15)} unique markets)")
    print(f"  Cheap side 15-20c: {near_miss_15_20} signals ({len(near_miss_by_cid_15_20)} unique markets)")
    print("  (One market can have many signals; unique markets = distinct windows.)")

    print("\n--- Interpretation ---")
    total_trades = sum(len(r) for r in buckets.values())
    if total_trades >= 10:
        win_rate_10c = 0
        n10 = len(buckets.get("0-5c", [])) + len(buckets.get("5-10c", []))
        if n10:
            wins10 = sum(1 for r in buckets.get("0-5c", []) + buckets.get("5-10c", []) 
                        if r["outcome"] in ("WIN", "TP"))
            win_rate_10c = 100 * wins10 / n10
        print(f"  At <=10c: {n10} trades, ~{win_rate_10c:.0f}% win/TP rate.")
        print("  For 15c entries: need win rate > 15% to be +EV (hold to resolution).")
        print("  For 20c entries: need win rate > 20% to be +EV (hold to resolution).")
        if near_miss_10_15 or near_miss_15_20:
            print("\n  Expanding to 15c/20c would add more trades but with less edge.")
            print("  Recommendation: Run paper mode with REVERSAL_PRICE_THRESHOLD=0.15 or 0.20")
            print("  for a few hundred signals, then re-run this script on the new trades.")
    else:
        print("  Need more trades (10+) for reliable stats. Keep gathering data.")

    print("\n--- If you enable 15c/20c ---")
    print("  - Use smaller size for 15c/20c (e.g. 50% of 10c size) - less edge.")
    print("  - Adjust TP: at 15c entry, TP at 35c/45c/55c (not 20c/30c/40c/50c).")
    print("  - At 20c entry, TP at 40c/50c/60c or similar.")


if __name__ == "__main__":
    main()
