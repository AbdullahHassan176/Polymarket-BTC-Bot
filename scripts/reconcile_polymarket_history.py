#!/usr/bin/env python3
"""
Reconcile bot trades against Polymarket's actual redemption history.

When the bot force-clears positions (CLEARED_STALE), it records PnL=0 because
it couldn't fetch resolution from Gamma in time. Polymarket History shows the
actual Redeem amounts. This script corrects PnL for those positions.

Usage:
  python scripts/reconcile_polymarket_history.py polymarket_history.csv
  python scripts/reconcile_polymarket_history.py polymarket_history.csv --apply
  python scripts/reconcile_polymarket_history.py polymarket_history.csv --trades logs/trades.csv

Exports Polymarket History: Profile -> Trading History -> Export CSV

Note: Stop the bot before running with --apply, then restart it. Otherwise the running
bot may overwrite state.json and undo the cumulative_pnl_usdc correction.
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Resolve project root
_script_dir = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
sys.path.insert(0, _ROOT)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _normalize_market_name(name: str) -> str:
    """Normalize market name for matching (strip whitespace)."""
    return (name or "").strip()


def load_polymarket_history(path: str) -> Dict[str, float]:
    """
    Load Polymarket History CSV and return {marketName: redeem_usdc}.
    Redeem 0 = lost; Redeem > 0 = won, amount received.
    """
    redeems: Dict[str, float] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = (row.get("action") or "").strip()
            if action != "Redeem":
                continue
            name = _normalize_market_name(row.get("marketName", ""))
            keywords = [c["keyword"] for c in (getattr(__import__("config", fromlist=["ASSETS_CONFIG"]), "ASSETS_CONFIG", {"BTC": {"keyword": "Bitcoin Up or Down"}}).values())]
            if not name or not any(name.startswith(kw) for kw in keywords):
                continue
            try:
                amt = float(row.get("usdcAmount", 0) or 0)
            except (ValueError, TypeError):
                amt = 0
            # Multiple redeems for same market (rare) - sum
            redeems[name] = redeems.get(name, 0) + amt
    return redeems


def load_bot_trades(path: str) -> List[dict]:
    """Load bot trades.csv and return CLOSE rows (REAL mode)."""
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("action") == "CLOSE" and row.get("mode") == "REAL"):
                rows.append(row)
    return rows


def reconcile(
    polymarket_csv: str,
    trades_csv: str,
) -> Tuple[List[dict], float]:
    """
    Match bot CLEARED_STALE (and other closes) to Polymarket Redeems.
    Returns (corrections, total_pnl_diff).
    """
    redeems = load_polymarket_history(polymarket_csv)
    trades = load_bot_trades(trades_csv)
    corrections = []
    total_diff = 0.0

    for row in trades:
        outcome = (row.get("outcome") or "").strip()
        question = _normalize_market_name(row.get("question", ""))
        if not question:
            continue
        try:
            size = float(row.get("size_usdc", 0) or 0)
            num_tokens = float(row.get("num_tokens", 0) or 0)
            entry_price = float(row.get("entry_price", 0) or 0)
        except (ValueError, TypeError):
            continue

        redeem = redeems.get(question)
        if redeem is None:
            continue

        # Actual PnL: Redeem = $1 per winning token (payout). Cost of those tokens = num_tokens * entry_price.
        if redeem > 0:
            cost_basis = num_tokens * entry_price if entry_price > 0 else size
            actual_pnl = round(redeem - cost_basis, 4)
            actual_outcome = "WIN"
        else:
            actual_pnl = round(-size, 4)
            actual_outcome = "LOSS"

        try:
            recorded_pnl = float(row.get("pnl_usdc", 0) or 0)
        except (ValueError, TypeError):
            recorded_pnl = 0
        diff = actual_pnl - recorded_pnl

        # Only correct CLEARED_STALE (recorded 0) - other outcomes are already correct
        if outcome != "CLEARED_STALE":
            continue
        if abs(diff) > 0.001:  # meaningful correction
            corrections.append({
                "question": question[:60],
                "condition_id": row.get("condition_id", "")[:20],
                "outcome": outcome,
                "actual_outcome": actual_outcome,
                "recorded_pnl": recorded_pnl,
                "actual_pnl": actual_pnl,
                "redeem_usdc": redeem,
                "size_usdc": size,
                "num_tokens": num_tokens,
                "entry_price": entry_price,
                "diff": diff,
            })
            total_diff += diff

    # Dedupe by question: same market can appear twice in trades.csv; only count one correction per market
    seen_questions = set()
    deduped_total = 0.0
    for c in corrections:
        q = c["question"]
        if q not in seen_questions:
            seen_questions.add(q)
            deduped_total += c["diff"]

    return corrections, deduped_total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile bot trades against Polymarket History redeems"
    )
    parser.add_argument(
        "polymarket_csv",
        help="Path to Polymarket History export CSV",
    )
    parser.add_argument(
        "--trades",
        default=os.path.join(_ROOT, "logs", "trades.csv"),
        help="Path to bot trades.csv (default: logs/trades.csv)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply corrections to state.json cumulative_pnl_usdc",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Write reconciliation report to this CSV path",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.polymarket_csv):
        logger.error("Polymarket CSV not found: %s", args.polymarket_csv)
        sys.exit(1)

    corrections, total_diff = reconcile(args.polymarket_csv, args.trades)

    if not corrections:
        logger.info("No PnL corrections needed. All recorded outcomes match Polymarket.")
        return

    logger.info("=== RECONCILIATION REPORT ===\n")
    logger.info("Found %d position(s) with PnL corrections:\n", len(corrections))
    for c in corrections:
        logger.info(
            "  %s\n    Bot: %s ($%.2f) -> Actual: %s ($%.2f) | Redeem: $%.2f | Diff: $%.2f",
            c["question"],
            c["outcome"],
            c["recorded_pnl"],
            c["actual_outcome"],
            c["actual_pnl"],
            c["redeem_usdc"],
            c["diff"],
        )
    logger.info("\nTotal PnL correction: $%.2f", total_diff)

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(corrections[0].keys()))
            w.writeheader()
            w.writerows(corrections)
        logger.info("Report written to %s", args.report)

    if args.apply and abs(total_diff) > 0.001:
        state_file = os.path.join(_ROOT, "state.json")
        if not os.path.isfile(state_file):
            logger.warning("state.json not found. Cannot apply.")
            sys.exit(1)
        import json
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        old = state.get("cumulative_pnl_usdc", 0) or 0
        state["cumulative_pnl_usdc"] = round(old + total_diff, 4)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        logger.info(
            "Applied: cumulative_pnl_usdc $%.2f -> $%.2f",
            old,
            state["cumulative_pnl_usdc"],
        )


if __name__ == "__main__":
    main()
