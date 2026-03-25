#!/usr/bin/env python3
"""
Daily reversal frequency: how often do we capture a (full or partial) reversal?

1) From trades: counts CLOSE rows with entry <= threshold (e.g. 15c), buckets outcome
   as "reversal captured" (WIN, TP, TP_TIER 35c+, TP_STUCK_BID) vs not.

2) From reversal_window_paths.csv (optional): when the bot runs it logs each completed
   window's min/max yes/no. Use --paths to report how many windows per day had a "full
   reversal" (one side min <= 15c and that side max >= 50c).

Run from repo root:
  python scripts/analyze_reversal_frequency.py
  python scripts/analyze_reversal_frequency.py --paths
  python scripts/analyze_reversal_frequency.py --max-entry 0.18 --min-low 0.15 --min-high 0.50
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRADES = [ROOT / "logs" / "trades.csv", ROOT / "logs" / "paper_trades.csv"]
PATHS_CSV = ROOT / "logs" / "reversal_window_paths.csv"

# Outcomes we count as "reversal captured" (price came back enough to profit or win)
REVERSAL_CAPTURED = frozenset({"WIN", "TP", "TP_STUCK_BID"})
# TP_TIER_0.xx: treat tier >= this as "full/strong" reversal
TP_TIER_STRONG_THRESHOLD = 0.35


def _safe_float(x, default=0.0):
    if x is None or x == "":
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _date_from_ts(ts: str) -> str:
    """Return YYYY-MM-DD from ISO timestamp."""
    if not ts or not ts.strip():
        return ""
    part = (ts.strip() or "").split("T")[0]
    return part if part else ""


def _is_reversal_captured(outcome: str, entry_price: float) -> bool:
    if outcome in REVERSAL_CAPTURED:
        return True
    if (outcome or "").startswith("TP_TIER_"):
        try:
            tier = float(outcome.replace("TP_TIER_", "").strip())
            return tier >= TP_TIER_STRONG_THRESHOLD
        except ValueError:
            pass
    return False


def _is_strong_tier(outcome: str) -> bool:
    if not (outcome or "").startswith("TP_TIER_"):
        return False
    try:
        tier = float(outcome.replace("TP_TIER_", "").strip())
        return tier >= 0.50  # 50c+ = "full" reversal
    except ValueError:
        return False


def load_closes(path: Path, max_entry: float, strategy_filter: str) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("action") or "").strip().upper() != "CLOSE":
                continue
            ep = _safe_float(r.get("entry_price"), 1.0)
            if ep > max_entry:
                continue
            if strategy_filter and (r.get("strategy_tier") or "").strip() != strategy_filter:
                continue
            r["_entry_price"] = ep
            r["_date"] = _date_from_ts(r.get("timestamp") or "")
            rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily reversal frequency from trades")
    ap.add_argument("--trades", nargs="+", default=[str(p) for p in DEFAULT_TRADES],
                    help="Paths to trades CSV (default: logs/trades.csv logs/paper_trades.csv)")
    ap.add_argument("--max-entry", type=float, default=0.15,
                    help="Max entry price to count as reversal trade (default 0.15)")
    ap.add_argument("--strategy", default="reversal",
                    help="Only count rows with this strategy_tier (default: reversal). Use '' for all.")
    ap.add_argument("--paths", action="store_true",
                    help="Also report market-level reversal frequency from reversal_window_paths.csv (min->max per window)")
    ap.add_argument("--min-low", type=float, default=0.15,
                    help="For --paths: count windows where min yes/no <= this (default 0.15)")
    ap.add_argument("--min-high", type=float, default=0.50,
                    help="For --paths: full reversal = same side reached this (default 0.50)")
    args = ap.parse_args()

    all_rows = []
    for p in args.trades:
        path = Path(p)
        if not path.is_absolute():
            path = ROOT / path
        all_rows.extend(load_closes(path, args.max_entry, args.strategy))

    if not all_rows:
        print("No CLOSE rows with entry_price <= %.2f found in %s" % (args.max_entry, args.trades))
        return

    # By date: total, captured, strong (50c+), not captured
    by_date = defaultdict(lambda: {"total": 0, "captured": 0, "strong": 0, "not_captured": 0})
    for r in all_rows:
        d = r["_date"]
        if not d:
            continue
        by_date[d]["total"] += 1
        outcome = (r.get("outcome") or "").strip()
        if _is_reversal_captured(outcome, r["_entry_price"]):
            by_date[d]["captured"] += 1
            if outcome == "WIN" or outcome == "TP" or _is_strong_tier(outcome):
                by_date[d]["strong"] += 1
        else:
            by_date[d]["not_captured"] += 1

    print("=" * 60)
    print("DAILY REVERSAL FREQUENCY (entry <= %.2f)" % args.max_entry)
    print("  Reversal captured = WIN | TP | TP_TIER >= 35c | TP_STUCK_BID")
    print("  Strong = WIN | TP | TP_TIER >= 50c")
    print("=" * 60)
    print("%-12s %6s %10s %8s %10s" % ("Date", "Total", "Captured", "Strong", "Rate"))
    print("-" * 60)
    for d in sorted(by_date.keys(), reverse=True)[:30]:
        v = by_date[d]
        rate = (v["captured"] / v["total"] * 100) if v["total"] else 0
        print("%-12s %6d %10d %8d %9.1f%%" % (d, v["total"], v["captured"], v["strong"], rate))
    print("-" * 60)
    total_all = sum(v["total"] for v in by_date.values())
    captured_all = sum(v["captured"] for v in by_date.values())
    strong_all = sum(v["strong"] for v in by_date.values())
    if total_all:
        print("%-12s %6d %10d %8d %9.1f%%" % ("(all)", total_all, captured_all, strong_all, captured_all / total_all * 100))
    # Market-level: from reversal_window_paths.csv (filled by bot as windows complete)
    if args.paths and PATHS_CSV.exists():
        path_rows = []
        with PATHS_CSV.open("r", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                path_rows.append(r)
        if path_rows:
            by_date_path = defaultdict(lambda: {"windows": 0, "full_reversal": 0})
            for r in path_rows:
                d = (r.get("date_utc") or "").split()[0]  # YYYY-MM-DD
                if not d:
                    continue
                min_yes = _safe_float(r.get("min_yes"), 1.0)
                min_no = _safe_float(r.get("min_no"), 1.0)
                max_yes = _safe_float(r.get("max_yes"), 0.0)
                max_no = _safe_float(r.get("max_no"), 0.0)
                by_date_path[d]["windows"] += 1
                # Full reversal: either side dipped to <= min_low and that same side reached >= min_high
                if min_yes <= args.min_low and max_yes >= args.min_high:
                    by_date_path[d]["full_reversal"] += 1
                elif min_no <= args.min_low and max_no >= args.min_high:
                    by_date_path[d]["full_reversal"] += 1
            print("\n" + "=" * 60)
            print("MARKET REVERSAL FREQUENCY (reversal_window_paths.csv)")
            print("  Full reversal = one side had min <= %.2f and max >= %.2f in same window" % (args.min_low, args.min_high))
            print("=" * 60)
            print("%-12s %10s %14s %10s" % ("Date", "Windows", "Full reversal", "Rate"))
            print("-" * 60)
            for d in sorted(by_date_path.keys(), reverse=True)[:30]:
                v = by_date_path[d]
                rate = (v["full_reversal"] / v["windows"] * 100) if v["windows"] else 0
                print("%-12s %10d %14d %9.1f%%" % (d, v["windows"], v["full_reversal"], rate))
            tw = sum(v["windows"] for v in by_date_path.values())
            tr = sum(v["full_reversal"] for v in by_date_path.values())
            if tw:
                print("-" * 60)
                print("%-12s %10d %14d %9.1f%%" % ("(all)", tw, tr, tr / tw * 100))
            print("=" * 60)
        else:
            print("\nreversal_window_paths.csv is empty. Run the bot to collect window min/max data.")
    elif args.paths:
        print("\nreversal_window_paths.csv not found. Run the bot to create it (window-path logging is automatic).")
    else:
        print("\nTip: run with --paths to see how often the *market* had a full reversal per day (needs reversal_window_paths.csv from the bot).")


if __name__ == "__main__":
    main()
