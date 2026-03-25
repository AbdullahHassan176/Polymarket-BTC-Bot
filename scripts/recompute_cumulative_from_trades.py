#!/usr/bin/env python3
"""
Recompute cumulative_pnl_usdc from logs/trades.csv (REAL mode only).

Use this to fix state.json after PAPER trades incorrectly inflated cumulative.
Only sums pnl_usdc from CLOSE rows where mode=REAL.

Usage:
  python scripts/recompute_cumulative_from_trades.py
  python scripts/recompute_cumulative_from_trades.py --apply
  python scripts/recompute_cumulative_from_trades.py --trades logs/trades.csv
"""

import argparse
import csv
import json
import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()


def main() -> None:
    ap = argparse.ArgumentParser(description="Recompute cumulative PnL from REAL trades only")
    ap.add_argument("--trades", default=os.path.join(_ROOT, "logs", "trades.csv"), help="Trades CSV path")
    ap.add_argument("--apply", action="store_true", help="Write to state.json")
    args = ap.parse_args()

    if not os.path.isfile(args.trades):
        print(f"Trades file not found: {args.trades}")
        sys.exit(1)

    total = 0.0
    count = 0
    with open(args.trades, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("action") or "").strip() != "CLOSE":
                continue
            if (row.get("mode") or "").strip().upper() != "REAL":
                continue
            try:
                pnl = float(row.get("pnl_usdc") or 0)
            except (ValueError, TypeError):
                continue
            total += pnl
            count += 1

    print(f"REAL CLOSE trades: {count}")
    print(f"Sum of pnl_usdc: ${total:.2f}")
    if args.apply:
        state_path = os.path.join(_ROOT, "state.json")
        if not os.path.isfile(state_path):
            print(f"state.json not found: {state_path}")
            sys.exit(1)
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        old = state.get("cumulative_pnl_usdc", 0)
        state["cumulative_pnl_usdc"] = round(total, 4)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        print(f"Applied: cumulative_pnl_usdc ${old:.2f} -> ${total:.2f}")
        print("Stop the bot before running --apply, then restart.")


if __name__ == "__main__":
    main()
