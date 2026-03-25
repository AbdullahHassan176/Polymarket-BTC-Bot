#!/usr/bin/env python3
"""
One-off analysis: BTC live (trades.csv) vs paper (paper_trades.csv) for ETH/XRP/SOL/DOGE.
Run from repo root: python scripts/analyze_performance.py
"""
import csv
import os
from collections import defaultdict
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_CSV = os.path.join(ROOT, "logs", "trades.csv")
PAPER_CSV = os.path.join(ROOT, "logs", "paper_trades.csv")


def asset_from_question(q: str) -> str:
    if "Bitcoin" in q:
        return "BTC"
    if "Ethereum" in q:
        return "ETH"
    if "XRP" in q:
        return "XRP"
    if "Solana" in q:
        return "SOL"
    if "Doge" in q or "DOGE" in q:
        return "DOGE"
    return "OTHER"


def parse_float(s, default=0.0):
    if s is None or s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def analyze_file(path: str, mode_filter: Optional[str], asset_filter: Optional[str]):
    """mode_filter: 'REAL' or 'PAPER' or None for all. asset_filter: 'BTC' etc or None."""
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if mode_filter and r.get("mode") != mode_filter:
                continue
            if asset_filter:
                asset = asset_from_question(r.get("question", ""))
                if asset != asset_filter:
                    continue
            rows.append(r)

    # CLOSE rows with outcome
    closes = [r for r in rows if r.get("action") == "CLOSE" and r.get("outcome")]
    # Unique positions (condition_id) that were closed
    position_ids = set(r.get("condition_id") for r in closes)

    total_pnl = sum(parse_float(r.get("trade_pnl_usdc")) for r in closes)
    outcome_counts = defaultdict(int)
    for r in closes:
        outcome_counts[r.get("outcome", "").strip() or "unknown"] += 1

    # Group outcome into buckets
    wins = sum(outcome_counts[k] for k in outcome_counts if k in ("WIN", "TP") or k.startswith("TP_TIER") or k == "TP_STUCK_BID")
    losses = sum(outcome_counts[k] for k in outcome_counts if k in ("LOSS", "SL"))
    # SL can be positive (partial profit)
    stale = outcome_counts.get("CLEARED_STALE", 0)
    # TP_STUCK_BID can be negative
    for k in outcome_counts:
        if k.startswith("TP_TIER"):
            pass  # already in wins
        elif k == "TP_STUCK_BID":
            pass

    return {
        "total_rows": len(rows),
        "close_rows": len(closes),
        "unique_positions": len(position_ids),
        "total_pnl_usdc": round(total_pnl, 2),
        "outcome_counts": dict(outcome_counts),
        "opens": len([r for r in rows if r.get("action") == "OPEN"]),
    }


def main():
    print("=" * 70)
    print("PERFORMANCE ANALYSIS: BTC Live + Paper (ETH, XRP, SOL, DOGE)")
    print("=" * 70)

    # ---- BTC LIVE (trades.csv, REAL only, Bitcoin only) ----
    if not os.path.isfile(TRADES_CSV):
        print("Missing:", TRADES_CSV)
        return
    btc_real = analyze_file(TRADES_CSV, "REAL", "BTC")
    print("\n--- BTC LIVE (real trading, trades.csv) ---")
    print(f"  Open events:     {btc_real['opens']}")
    print(f"  Close events:    {btc_real['close_rows']} (incl. partial TP tiers)")
    print(f"  Unique markets:  {btc_real['unique_positions']}")
    print(f"  Total PnL USDC:  {btc_real['total_pnl_usdc']:+.2f}")
    print("  Outcomes:", btc_real["outcome_counts"])

    # ---- Paper: per asset from paper_trades.csv ----
    if not os.path.isfile(PAPER_CSV):
        print("\nMissing:", PAPER_CSV)
        return
    print("\n--- PAPER TRADING (paper_trades.csv) by asset ---")
    for asset in ("ETH", "XRP", "SOL", "DOGE"):
        stat = analyze_file(PAPER_CSV, "PAPER", asset)
        print(f"\n  {asset}:")
        print(f"    Open events:     {stat['opens']}")
        print(f"    Close events:    {stat['close_rows']}")
        print(f"    Unique markets:  {stat['unique_positions']}")
        print(f"    Total PnL USDC:  {stat['total_pnl_usdc']:+.2f}")
        print("    Outcomes:", stat["outcome_counts"])

    # ---- Summary ----
    eth = analyze_file(PAPER_CSV, "PAPER", "ETH")
    xrp = analyze_file(PAPER_CSV, "PAPER", "XRP")
    sol = analyze_file(PAPER_CSV, "PAPER", "SOL")
    doge = analyze_file(PAPER_CSV, "PAPER", "DOGE")
    paper_total_pnl = eth["total_pnl_usdc"] + xrp["total_pnl_usdc"] + sol["total_pnl_usdc"] + doge["total_pnl_usdc"]
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  BTC (live) total PnL:     {btc_real['total_pnl_usdc']:+.2f} USDC")
    print(f"  Paper (ETH+XRP+SOL+DOGE): {paper_total_pnl:+.2f} USDC")
    print(f"  BTC live positions:       {btc_real['unique_positions']} markets")
    print(f"  Paper positions:         ETH={eth['unique_positions']}  XRP={xrp['unique_positions']}  SOL={sol['unique_positions']}  DOGE={doge['unique_positions']}")


if __name__ == "__main__":
    main()
