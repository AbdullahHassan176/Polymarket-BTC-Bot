#!/usr/bin/env python3
"""
Drawdown and streak report from trades CSV (live and/or paper).
Run from repo root: python scripts/drawdown_report.py [--live] [--paper] [--last N]
"""
import argparse
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_CSV = os.path.join(ROOT, "logs", "trades.csv")
PAPER_CSV = os.path.join(ROOT, "logs", "paper_trades.csv")


def parse_float(s, default=0.0):
    if s is None or s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def load_closes(path: str, mode_filter: str) -> list:
    """Load CLOSE rows with trade_pnl_usdc; filter by mode (REAL or PAPER)."""
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("action") != "CLOSE" or r.get("mode") != mode_filter:
                continue
            pnl = parse_float(r.get("trade_pnl_usdc"))
            # Skip CLEARED_STALE with 0 for streak (they don't indicate win/loss)
            outcome = (r.get("outcome") or "").strip()
            rows.append({"pnl": pnl, "outcome": outcome, "timestamp": r.get("timestamp", "")})
    return rows


def run_report(rows: list, label: str, last_n: int = 30):
    if not rows:
        print(f"  {label}: no close events.")
        return
    pnls = [r["pnl"] for r in rows]
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    # Recent streak (last N with actual win/loss, skip CLEARED_STALE 0)
    recent = [r for r in rows[-last_n:] if r["outcome"] != "CLEARED_STALE" or r["pnl"] != 0]
    wins = sum(1 for r in recent if r["pnl"] > 0)
    losses = sum(1 for r in recent if r["pnl"] < 0)
    flat = len(recent) - wins - losses
    print(f"  {label}:")
    print(f"    Total closes:    {len(rows)}")
    print(f"    Cumulative PnL:  ${cumulative:+.2f}")
    print(f"    Max drawdown:    ${max_dd:.2f}")
    print(f"    Last {len(recent)} (non-stale): wins={wins} losses={losses} flat/other={flat}")


def main():
    ap = argparse.ArgumentParser(description="Drawdown and streak report from trades CSVs")
    ap.add_argument("--live", action="store_true", help="Include live trades (trades.csv)")
    ap.add_argument("--paper", action="store_true", help="Include paper trades (paper_trades.csv)")
    ap.add_argument("--last", type=int, default=30, help="Last N closes for streak (default 30)")
    args = ap.parse_args()
    if not args.live and not args.paper:
        args.live = True
        args.paper = True
    print("=" * 60)
    print("DRAWDOWN & STREAK REPORT")
    print("=" * 60)
    if args.live:
        rows = load_closes(TRADES_CSV, "REAL")
        run_report(rows, "Live (trades.csv)", args.last)
    if args.paper:
        rows = load_closes(PAPER_CSV, "PAPER")
        run_report(rows, "Paper (paper_trades.csv)", args.last)
    print("=" * 60)
    print("Run weekly with: python scripts/analyze_performance.py && python scripts/drawdown_report.py --live")


if __name__ == "__main__":
    main()
