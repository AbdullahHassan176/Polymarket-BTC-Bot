"""
paper_report.py — Short summary of a trades CSV (post paper session).

  python paper_report.py --csv logs/trades.csv
"""

from __future__ import annotations

import argparse
import os

import pandas as pd


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=os.path.join("logs", "trades.csv"))
    p.add_argument("--out", default=os.path.join("logs", "paper_session_summary.txt"))
    args = p.parse_args()

    if not os.path.isfile(args.csv):
        print("No file:", args.csv)
        return 1

    df = pd.read_csv(args.csv)
    closes = df[df.get("action") == "CLOSE"].copy()
    opens = df[df.get("action") == "OPEN"].copy()

    lines = [
        f"Paper / trades summary - {args.csv}",
        f"OPEN rows: {len(opens)} | CLOSE rows: {len(closes)}",
    ]

    if not closes.empty and "pnl_usdc" in closes.columns:
        closes["pnl_usdc"] = pd.to_numeric(closes["pnl_usdc"], errors="coerce").fillna(0.0)
        lines.append(f"Total PnL (closes): ${closes['pnl_usdc'].sum():.4f}")
        if "outcome" in closes.columns:
            vc = closes["outcome"].value_counts()
            lines.append("Outcomes: " + ", ".join(f"{k}={v}" for k, v in vc.items()))

    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
