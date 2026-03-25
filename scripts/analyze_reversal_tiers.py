"""
Analyze reversal bid trace to simulate tiered take-profit outcomes.

Use after gathering data: the bot logs bid samples to logs/reversal_bid_trace.csv
while monitoring reversal positions. This script:
  - Aggregates max bid per condition_id from the trace
  - Joins with closed trades (logs/trades.csv)
  - Simulates tiered TP: e.g. 25% at 20c, 25% at 30c, 25% at 40c, 25% at 50c
  - Reports how much profit tiered TP would have captured vs hold-to-resolution

Usage:
  python scripts/analyze_reversal_tiers.py
  python scripts/analyze_reversal_tiers.py --trace logs/reversal_bid_trace.csv --trades logs/trades.csv
  python scripts/analyze_reversal_tiers.py --tiers 0.2,0.3,0.4,0.5 --pcts 25,25,25,25
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE_CSV = ROOT / "logs" / "reversal_bid_trace.csv"
TRADES_CSV = ROOT / "logs" / "trades.csv"


def _safe_float(x, default=0.0):
    try:
        return float(x) if x != "" else default
    except (TypeError, ValueError):
        return default


def load_max_bid_by_condition(trace_path: Path) -> dict[str, float]:
    """Compute max bid per condition_id from bid trace."""
    max_bid: dict[str, float] = {}
    if not trace_path.exists():
        return max_bid
    with trace_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = (row.get("condition_id", "") or "").strip()
            bid = _safe_float(row.get("bid"))
            if cid:
                max_bid[cid] = max(max_bid.get(cid, 0), bid)
    return max_bid


def load_closed_reversal_trades(trades_path: Path) -> list[dict]:
    """Load closed trades with strategy_tier=reversal."""
    rows = []
    if not trades_path.exists():
        return rows
    with trades_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("action", "") or "").upper() != "CLOSE":
                continue
            tier = (row.get("strategy_tier", "") or "").strip()
            if tier != "reversal":
                continue
            rows.append(row)
    return rows


def simulate_tiered_pnl(
    entry_price: float,
    num_tokens: float,
    size_usdc: float,
    max_bid: float,
    tiers: list[float],
    pcts: list[float],
) -> tuple[float, dict]:
    """
    Simulate PnL if we scaled out at each tier.
    tiers: e.g. [0.20, 0.30, 0.40, 0.50]
    pcts: e.g. [25, 25, 25, 25] (percent of position sold at each tier)
    Returns: (total_pnl, {tier: tokens_sold})
    """
    assert abs(sum(pcts) - 100) < 0.01
    pct_frac = [p / 100.0 for p in pcts]
    sold = 0.0
    pnl = 0.0
    sold_by_tier = {}
    for t, p in zip(tiers, pct_frac):
        if max_bid >= t:
            tok = num_tokens * p
            sold += tok
            pnl += tok * t - (size_usdc * p)
            sold_by_tier[t] = tok
        else:
            sold_by_tier[t] = 0.0
    # Remaining (if we didn't sell all) holds to resolution - we don't model that here,
    # we just report what we'd have locked in by scaling at tiers.
    return round(pnl, 4), sold_by_tier


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze tiered TP for reversal trades")
    ap.add_argument("--trace", default=str(TRACE_CSV), help="Bid trace CSV path")
    ap.add_argument("--trades", default=str(TRADES_CSV), help="Trades CSV path")
    ap.add_argument("--tiers", default="0.20,0.30,0.40,0.50", help="Comma-separated tier prices")
    ap.add_argument("--pcts", default="25,25,25,25", help="Comma-separated pct at each tier")
    args = ap.parse_args()

    trace_path = Path(args.trace)
    trades_path = Path(args.trades)
    tiers = [float(x.strip()) for x in args.tiers.split(",")]
    pcts = [float(x.strip()) for x in args.pcts.split(",")]
    if len(tiers) != len(pcts):
        print("Error: --tiers and --pcts must have same length")
        return

    max_bid = load_max_bid_by_condition(trace_path)
    closed = load_closed_reversal_trades(trades_path)

    print("=" * 60)
    print("Reversal Tiered TP Analysis")
    print("=" * 60)
    print(f"Bid trace: {trace_path} ({len(max_bid)} condition_ids with samples)")
    print(f"Closed reversal trades: {len(closed)}")
    print(f"Tiers: {tiers} | Pcts: {pcts}")
    print()

    with_trace = 0
    without_trace = 0
    actual_pnl = 0.0
    tiered_pnl = 0.0
    # For LOSS trades: how many had max_bid >= each tier?
    loss_with_bid_at: dict[float, int] = {t: 0 for t in tiers}
    loss_count = 0

    for r in closed:
        cid = (r.get("condition_id", "") or "").strip()
        entry = _safe_float(r.get("entry_price"))
        tokens = _safe_float(r.get("num_tokens"))
        size = _safe_float(r.get("size_usdc"))
        outcome = (r.get("outcome", "") or "").strip().upper()
        pnl = _safe_float(r.get("pnl_usdc"))

        actual_pnl += pnl

        if cid in max_bid:
            with_trace += 1
            mb = max_bid[cid]
            tp, _ = simulate_tiered_pnl(entry, tokens, size, mb, tiers, pcts)
            tiered_pnl += tp
            if outcome == "LOSS":
                loss_count += 1
                for t in tiers:
                    if mb >= t:
                        loss_with_bid_at[t] += 1
        else:
            without_trace += 1

    print("--- Results ---")
    print(f"Trades with bid trace: {with_trace}")
    print(f"Trades without bid trace: {without_trace}")
    print()
    print(f"Actual PnL (from trades.csv): ${actual_pnl:.2f}")
    print(f"Simulated tiered TP PnL:     ${tiered_pnl:.2f}")
    print(f"  (only for trades with bid trace)")
    print()
    if loss_count > 0:
        print("LOSS trades that had max_bid >= tier (could have locked profit):")
        for t in tiers:
            n = loss_with_bid_at.get(t, 0)
            pct = 100 * n / loss_count
            print(f"  >= {t:.2f}: {n}/{loss_count} ({pct:.0f}%)")
    print()
    # Bucket: how often reversed to X but not the next tier
    bucket_labels = [f"< {tiers[0]:.2f}"]
    for i in range(len(tiers) - 1):
        bucket_labels.append(f"{tiers[i]:.2f}-{tiers[i+1]:.2f}")
    bucket_labels.append(f">= {tiers[-1]:.2f}")
    bucket_labels.append("(no trace)")
    buckets: dict[str, int] = {lbl: 0 for lbl in bucket_labels}
    for r in closed:
        cid = (r.get("condition_id", "") or "").strip()
        if cid not in max_bid:
            buckets["(no trace)"] += 1
            continue
        mb = max_bid[cid]
        if mb < tiers[0]:
            buckets[f"< {tiers[0]:.2f}"] += 1
        elif mb >= tiers[-1]:
            buckets[f">= {tiers[-1]:.2f}"] += 1
        else:
            for i in range(len(tiers) - 1):
                if tiers[i] <= mb < tiers[i + 1]:
                    buckets[f"{tiers[i]:.2f}-{tiers[i+1]:.2f}"] += 1
                    break
    print("Max bid reached (reversal price path buckets):")
    for lbl in bucket_labels:
        n = buckets[lbl]
        pct = 100 * n / len(closed) if closed else 0
        print(f"  {lbl}: {n} ({pct:.0f}%)")
    print()
    print("Run the bot to gather more bid trace data (LOG_REVERSAL_BID_TRACE=True).")
    print("Re-run this script after more reversal positions are monitored.")


if __name__ == "__main__":
    main()
