"""
Analyze single vs pyramid entry modes for reversal strategy.

Uses trades.csv and reversal_bid_trace.csv to:
  - Compare PnL by entry_mode (single vs pyramid)
  - Simulate "what if" for each trade: would the other mode have done better?
  - Derive insights: early bounce vs deep dip, recommend which mode to use

Usage:
  python scripts/analyze_reversal_entry_modes.py
  python scripts/analyze_reversal_entry_modes.py --trades logs/trades.csv --trace logs/reversal_bid_trace.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADES_CSV = ROOT / "logs" / "trades.csv"
TRACE_CSV = ROOT / "logs" / "reversal_bid_trace.csv"


def _safe_float(x, default=0.0):
    try:
        return float(x) if x not in ("", None) else default
    except (TypeError, ValueError):
        return default


def load_max_bid_by_condition(trace_path: Path) -> dict[str, float]:
    """Max bid per condition_id from bid trace."""
    out = {}
    if not trace_path.exists():
        return out
    with trace_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = (row.get("condition_id") or "").strip()
            bid = _safe_float(row.get("bid"))
            if cid:
                out[cid] = max(out.get(cid, 0), bid)
    return out


def load_closed_reversal_trades(trades_path: Path) -> list[dict]:
    """Closed reversal trades (CLOSE, strategy_tier=reversal)."""
    rows = []
    if not trades_path.exists():
        return rows
    with trades_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("action") or "").upper() != "CLOSE":
                continue
            if (row.get("strategy_tier") or "").strip() != "reversal":
                continue
            rows.append(row)
    return rows


def simulate_single_pnl(entry_price: float, size: float, tokens: float, max_bid: float, won: bool) -> float:
    """PnL if we held single entry to resolution or sold at max_bid (simplified: resolution only)."""
    if won:
        return tokens * 1.0 - size
    return -size


def simulate_pyramid_pnl(
    entry_tiers_str: str,
    total_size: float,
    total_tokens: float,
    max_bid: float,
    won: bool,
    allocation: dict = None,
) -> float:
    """
    Simulate pyramid: split by tiers, each slice resolves or hits TP.
    Simplification: assume equal allocation across tiers present.
    """
    if not entry_tiers_str or not allocation:
        return simulate_single_pnl(
            total_size / total_tokens if total_tokens else 0.2,
            total_size, total_tokens, max_bid, won,
        )
    tiers = [_safe_float(x.strip()) for x in str(entry_tiers_str).split(",") if x.strip()]
    if not tiers:
        return simulate_single_pnl(total_size / total_tokens if total_tokens else 0.2, total_size, total_tokens, max_bid, won)
    frac = 1.0 / len(tiers)
    pnl = 0.0
    for t in tiers:
        alloc = allocation.get(t, frac)
        slc_size = total_size * alloc
        slc_tokens = total_tokens * alloc
        if won:
            pnl += slc_tokens * 1.0 - slc_size
        else:
            pnl += -slc_size
    return round(pnl, 4)


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare single vs pyramid entry modes")
    ap.add_argument("--trades", default=str(TRADES_CSV), help="Trades CSV path")
    ap.add_argument("--trace", default=str(TRACE_CSV), help="Bid trace CSV path")
    args = ap.parse_args()

    trace_path = Path(args.trace)
    trades_path = Path(args.trades)
    max_bid = load_max_bid_by_condition(trace_path)
    closed = load_closed_reversal_trades(trades_path)

    print("=" * 70)
    print("Reversal Entry Mode Analysis: Single vs Pyramid")
    print("=" * 70)
    print(f"Trades: {trades_path} ({len(closed)} closed reversal)")
    print(f"Trace:  {trace_path} ({len(max_bid)} condition_ids with bid samples)")
    print()

    # Aggregate by entry_mode
    by_mode: dict[str, list[dict]] = {"single": [], "pyramid": []}
    for r in closed:
        mode = (r.get("entry_mode") or "single").strip().lower()
        if "pyramid" in mode:
            by_mode["pyramid"].append(r)
        else:
            by_mode["single"].append(r)

    single_pnl = sum(_safe_float(r.get("pnl_usdc")) for r in by_mode["single"])
    pyramid_pnl = sum(_safe_float(r.get("pnl_usdc")) for r in by_mode["pyramid"])
    single_wins = sum(1 for r in by_mode["single"] if (r.get("outcome") or "").upper() in ("WIN", "TP"))
    pyramid_wins = sum(1 for r in by_mode["pyramid"] if (r.get("outcome") or "").upper() in ("WIN", "TP"))

    print("--- Results by Actual Entry Mode ---")
    print(f"Single:  {len(by_mode['single'])} trades | PnL ${single_pnl:.2f} | Wins {single_wins}")
    print(f"Pyramid: {len(by_mode['pyramid'])} trades | PnL ${pyramid_pnl:.2f} | Wins {pyramid_wins}")
    print()

    # Early bounce vs deep dip (using max_bid from trace)
    early_bounce = []  # max_bid >= 0.45 without going deep
    deep_dip = []      # min entry <= 0.15 (we bought at 10c or 15c)
    for r in closed:
        cid = (r.get("condition_id") or "").strip()
        mb = max_bid.get(cid, 0)
        entry_str = r.get("entry_tiers") or str(r.get("entry_price", ""))
        tiers = [_safe_float(x) for x in entry_str.replace(",", " ").split() if x]
        min_entry = min(tiers) if tiers else _safe_float(r.get("entry_price"), 0.2)
        rcopy = dict(r)
        rcopy["max_bid"] = mb
        rcopy["min_entry"] = min_entry
        if min_entry <= 0.15:
            deep_dip.append(rcopy)
        if mb >= 0.40:
            early_bounce.append(rcopy)

    print("--- Trade Profile ---")
    print(f"Deep dip (entry <= 15c): {len(deep_dip)} trades")
    print(f"Reached 40c+ bid:        {len(early_bounce)} trades (early bounce potential)")
    print()

    # Recommendation
    print("--- Recommendation ---")
    if len(closed) < 20:
        print("Need more trades (20+) for a reliable recommendation.")
    else:
        single_better = single_pnl > pyramid_pnl
        mode = "single" if single_better else "pyramid"
        print(f"Based on PnL: use REVERSAL_ENTRY_MODE={mode} (env: REVERSAL_ENTRY_MODE={mode})")
        if len(early_bounce) > len(closed) * 0.5:
            print("  (High early-bounce rate: single often captures full move.)")
        if len(deep_dip) > len(closed) * 0.4:
            print("  (Frequent deep dips: pyramid reduces loss when wrong.)")
    print()
    print("Toggle via .env: REVERSAL_ENTRY_MODE=single or REVERSAL_ENTRY_MODE=pyramid")
    print("Restart the bot after changing.")


if __name__ == "__main__":
    main()
