#!/usr/bin/env python3
"""
Compute true PnL from full Polymarket History export (all Buys, Sells, Redeems).

This is the cash-flow view: total spent (Buys) vs total received (Sells + Redeems).
Use it to compare wallet reality to the bot's state/trades.

If "Polymarket true" is negative but bot shows positive, the bot is likely
over-counting (e.g. partial TP logged, then position went stale/lost; or
CLEARED_STALE recorded as 0 instead of actual outcome). Run reconcile first,
then this script. Open positions (unrealized) are not in the export until Sold/Redeemed.

Export: Profile -> Trading History -> Export CSV

Usage:
  python scripts/compute_pnl_from_polymarket_history.py "path/to/Polymarket-History.csv"
  python scripts/compute_pnl_from_polymarket_history.py "path/to/Polymarket-History.csv" --trades logs/trades.csv
"""

import argparse
import csv
import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
sys.path.insert(0, _ROOT)

# Asset keywords for filtering (marketName startswith)
ASSET_KEYWORDS = [
    "Bitcoin Up or Down",
    "Ethereum Up or Down",
    "Solana Up or Down",
    "XRP Up or Down",
    "Dogecoin Up or Down",
]


def _asset_from_market_name(name: str) -> str:
    for kw in ASSET_KEYWORDS:
        if (name or "").strip().startswith(kw):
            if "Bitcoin" in kw:
                return "BTC"
            if "Ethereum" in kw:
                return "ETH"
            if "Solana" in kw:
                return "SOL"
            if "XRP" in kw:
                return "XRP"
            if "Dogecoin" in kw:
                return "DOGE"
    return "OTHER"


def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x) if x not in ("", None) else default
    except (TypeError, ValueError):
        return default


def load_polymarket_pnl(path: str):
    """
    Load Polymarket History CSV and compute PnL from Buy/Sell/Redeem.
    Returns (total_cost, total_proceeds, by_asset dict, row_count).
    """
    total_cost = 0.0
    total_proceeds = 0.0
    by_asset = {}  # asset -> {"cost": 0, "proceeds": 0, "pnl": 0}
    count_buy = count_sell = count_redeem = 0

    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = (row.get("action") or "").strip()
            name = (row.get("marketName") or "").strip()
            amt = _safe_float(row.get("usdcAmount"))

            asset = _asset_from_market_name(name)
            if asset == "OTHER":
                if action in ("Buy", "Sell", "Redeem") and name and "Up or Down" not in name:
                    continue  # skip non-5min markets
                if action == "Deposit":
                    continue
            if asset != "OTHER":
                by_asset.setdefault(asset, {"cost": 0.0, "proceeds": 0.0})
                if action == "Buy":
                    total_cost += amt
                    by_asset[asset]["cost"] += amt
                    count_buy += 1
                elif action == "Sell":
                    total_proceeds += amt
                    by_asset[asset]["proceeds"] += amt
                    count_sell += 1
                elif action == "Redeem":
                    total_proceeds += amt
                    by_asset[asset]["proceeds"] += amt
                    count_redeem += 1

    for a, d in by_asset.items():
        d["pnl"] = round(d["proceeds"] - d["cost"], 2)
    total_pnl = round(total_proceeds - total_cost, 2)
    return total_cost, total_proceeds, total_pnl, by_asset, (count_buy, count_sell, count_redeem)


def load_bot_trades_pnl(path: str) -> float:
    """Sum pnl_usdc for all CLOSE rows with mode=REAL in trades.csv."""
    total = 0.0
    if not os.path.isfile(path):
        return total
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("action") or "").strip() != "CLOSE":
                continue
            if (row.get("mode") or "").strip() != "REAL":
                continue
            total += _safe_float(row.get("pnl_usdc"))
    return round(total, 2)


def main():
    parser = argparse.ArgumentParser(
        description="Compute true PnL from Polymarket History (Buy/Sell/Redeem)"
    )
    parser.add_argument(
        "polymarket_csv",
        help="Path to Polymarket History export CSV",
    )
    parser.add_argument(
        "--trades",
        default=os.path.join(_ROOT, "logs", "trades.csv"),
        help="Path to bot trades.csv for comparison (default: logs/trades.csv)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.polymarket_csv):
        print("Error: Polymarket CSV not found:", args.polymarket_csv)
        sys.exit(1)

    total_cost, total_proceeds, total_pnl, by_asset, (nb, ns, nr) = load_polymarket_pnl(
        args.polymarket_csv
    )

    print("=" * 60)
    print("TRUE PnL FROM POLYMARKET HISTORY (Buy / Sell / Redeem)")
    print("=" * 60)
    print("File:", args.polymarket_csv)
    print("Counts: Buys=%d  Sells=%d  Redeems=%d" % (nb, ns, nr))
    print()
    print("Total cost (Buys):    $%.2f" % total_cost)
    print("Total proceeds (Sells + Redeems): $%.2f" % total_proceeds)
    print("Total PnL (proceeds - cost):  $%.2f" % total_pnl)
    print()
    if by_asset:
        print("By asset (5-min Up or Down only):")
        for asset in sorted(by_asset.keys()):
            d = by_asset[asset]
            print("  %s: cost $%.2f  proceeds $%.2f  -> PnL $%.2f" % (
                asset, d["cost"], d["proceeds"], d["pnl"]))
        print()

    bot_pnl = load_bot_trades_pnl(args.trades)
    if os.path.isfile(args.trades):
        print("Bot trades.csv (REAL CLOSE rows) sum pnl_usdc: $%.2f" % bot_pnl)
        print("Difference (Polymarket true - bot recorded):   $%.2f" % (total_pnl - bot_pnl))
    else:
        print("Bot trades.csv not found; no comparison.")
    print("=" * 60)


if __name__ == "__main__":
    main()
