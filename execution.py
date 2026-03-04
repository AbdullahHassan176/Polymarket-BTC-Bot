"""
execution.py  -  Trade entry and outcome recording for Polymarket bets.

Paper mode:  Logs exactly what it would do. No real orders placed.
Real mode:   Places actual orders via polymarket_client.place_order().

Unlike the OKX bot, Polymarket positions auto-resolve at window end - there
is no manual exit order. This module handles entry and final outcome recording
once the market closes.

All trades are logged to logs/trades.csv for review.
"""

import csv
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from polymarket_client import PolymarketClient
import config

logger = logging.getLogger(__name__)

# Path to the trades CSV log.
# Call set_trades_csv() to override (e.g. for testing).
TRADES_CSV = os.path.join("logs", "trades.csv")

TRADE_COLUMNS = [
    "timestamp",
    "action",           # "OPEN" or "CLOSE"
    "mode",             # "PAPER" or "REAL"
    "question",         # Market question string
    "condition_id",     # On-chain market ID
    "direction",        # "YES" (UP) or "NO" (DOWN)
    "entry_price",      # Price paid per token (0-1)
    "size_usdc",        # USDC spent
    "num_tokens",       # Tokens received
    "outcome",          # "WIN", "LOSS", or "" (open)
    "pnl_usdc",         # Profit/loss in USDC
    "pnl_pct",          # PnL as percentage of size_usdc
    "btc_spot",         # BTC price at entry for context
]


def set_trades_csv(path: str) -> None:
    """Override the trades CSV path (call once at startup if needed)."""
    global TRADES_CSV
    TRADES_CSV = path


# ---------------------------------------------------------------------------
# PAPER TRADING - ENTER
# ---------------------------------------------------------------------------

def paper_enter(market: dict, direction: str, entry_price: float, btc_spot: float) -> dict:
    """
    Simulate entering a Polymarket bet in paper mode.

    Args:
        market:      Active market dict from polymarket_client.find_active_btc_market().
        direction:   "YES" (betting UP) or "NO" (betting DOWN).
        entry_price: Token price at time of entry (0.0 - 1.0).
        btc_spot:    Current BTC/USDT price (for context logging).

    Returns:
        Position dict stored in state.json.
    """
    now        = datetime.now(timezone.utc).isoformat()
    size_usdc  = config.RISK_PER_TRADE_USDC
    num_tokens = round(size_usdc / entry_price, 4) if entry_price > 0 else 0

    logger.info(
        "=== PAPER BET: %s ===\n"
        "  Market:      %s\n"
        "  Direction:   %s\n"
        "  Entry price: %.3f per token\n"
        "  Size:        $%.2f USDC -> %.4f tokens\n"
        "  BTC spot:    $%.2f\n"
        "  Closes:      %s",
        direction,
        market["question"],
        direction,
        entry_price,
        size_usdc,
        num_tokens,
        btc_spot,
        market["end_date_iso"],
    )

    position = {
        "open":         True,
        "mode":         "PAPER",
        "entry_time":   now,
        "question":     market["question"],
        "condition_id": market["condition_id"],
        "yes_token_id": market["yes_token_id"],
        "no_token_id":  market["no_token_id"],
        "end_date_iso": market["end_date_iso"],
        "slug":         market.get("slug", ""),
        "direction":    direction,
        "entry_price":  entry_price,
        "size_usdc":    size_usdc,
        "num_tokens":   num_tokens,
        "btc_spot_at_entry": btc_spot,
        "outcome":      None,
        "pnl_usdc":     None,
    }

    _log_trade({
        "timestamp":    now,
        "action":       "OPEN",
        "mode":         "PAPER",
        "question":     market["question"],
        "condition_id": market["condition_id"],
        "direction":    direction,
        "entry_price":  entry_price,
        "size_usdc":    size_usdc,
        "num_tokens":   num_tokens,
        "outcome":      "",
        "pnl_usdc":     "",
        "pnl_pct":      "",
        "btc_spot":     btc_spot,
    })

    return position


# ---------------------------------------------------------------------------
# PAPER TRADING - RECORD OUTCOME
# ---------------------------------------------------------------------------

def paper_record_outcome(position: dict, market_result: str, btc_spot: float) -> dict:
    """
    Record the final outcome of a paper trade after the market resolves.

    Args:
        position:      The open position dict from state.json.
        market_result: "YES" or "NO" - the winning outcome.
        btc_spot:      Current BTC price for context.

    Returns:
        Updated position dict with outcome and PnL filled in.
    """
    now       = datetime.now(timezone.utc).isoformat()
    direction = position["direction"]
    size      = position["size_usdc"]
    price     = position["entry_price"]
    tokens    = position["num_tokens"]

    # A winning token resolves to $1.00, a losing token to $0.00.
    won = (market_result == direction)
    if won:
        # We bought 'tokens' tokens at 'price', now worth $1.00 each.
        payout  = tokens * 1.0
        pnl     = payout - size
        outcome = "WIN"
    else:
        pnl     = -size   # Lost the full USDC we bet
        outcome = "LOSS"

    pnl_pct = (pnl / size * 100) if size > 0 else 0.0

    logger.info(
        "=== PAPER BET RESOLVED: %s ===\n"
        "  Market result: %s (we bet %s)\n"
        "  Outcome:       %s\n"
        "  PnL:           $%.2f (%.1f%%)",
        outcome,
        market_result,
        direction,
        outcome,
        pnl,
        pnl_pct,
    )

    _log_trade({
        "timestamp":    now,
        "action":       "CLOSE",
        "mode":         "PAPER",
        "question":     position["question"],
        "condition_id": position["condition_id"],
        "direction":    direction,
        "entry_price":  price,
        "size_usdc":    size,
        "num_tokens":   tokens,
        "outcome":      outcome,
        "pnl_usdc":     round(pnl, 4),
        "pnl_pct":      round(pnl_pct, 2),
        "btc_spot":     btc_spot,
    })

    return {
        **position,
        "open":     False,
        "outcome":  outcome,
        "pnl_usdc": round(pnl, 4),
        "pnl_pct":  round(pnl_pct, 2),
    }


# ---------------------------------------------------------------------------
# REAL TRADING - ENTER
# ---------------------------------------------------------------------------

def real_enter(
    client: PolymarketClient,
    market: dict,
    direction: str,
    entry_price: float,
    btc_spot: float,
) -> Optional[dict]:
    """
    Place a real Polymarket order for the given direction.

    Args:
        client:      PolymarketClient instance.
        market:      Active market dict.
        direction:   "YES" or "NO".
        entry_price: Current token price to use as limit price.
        btc_spot:    BTC spot price for logging.

    Returns:
        Position dict on success, None if order failed.
    """
    if not config.REAL_TRADING:
        logger.error("real_enter() called but REAL_TRADING=False. Aborting.")
        return None

    token_id  = market["yes_token_id"] if direction == "YES" else market["no_token_id"]
    size_usdc = config.RISK_PER_TRADE_USDC

    logger.info(
        "REAL BET: %s | Market: %s | Price: %.3f | Size: $%.2f USDC",
        direction, market["question"], entry_price, size_usdc,
    )

    resp = client.place_order(
        token_id=token_id,
        side="BUY",
        size_usdc=size_usdc,
        price=entry_price,
    )

    if resp is None:
        logger.error("Real order failed for %s. No position recorded.", direction)
        return None

    now        = datetime.now(timezone.utc).isoformat()
    num_tokens = round(size_usdc / entry_price, 4)

    position = {
        "open":              True,
        "mode":              "REAL",
        "entry_time":        now,
        "question":          market["question"],
        "condition_id":      market["condition_id"],
        "yes_token_id":      market["yes_token_id"],
        "no_token_id":       market["no_token_id"],
        "end_date_iso":      market["end_date_iso"],
        "slug":              market.get("slug", ""),
        "direction":         direction,
        "entry_price":       entry_price,
        "size_usdc":         size_usdc,
        "num_tokens":        num_tokens,
        "btc_spot_at_entry": btc_spot,
        "order_id":          resp.get("orderID", "") if isinstance(resp, dict) else "",
        "outcome":           None,
        "pnl_usdc":          None,
    }

    _log_trade({
        "timestamp":    now,
        "action":       "OPEN",
        "mode":         "REAL",
        "question":     market["question"],
        "condition_id": market["condition_id"],
        "direction":    direction,
        "entry_price":  entry_price,
        "size_usdc":    size_usdc,
        "num_tokens":   num_tokens,
        "outcome":      "",
        "pnl_usdc":     "",
        "pnl_pct":      "",
        "btc_spot":     btc_spot,
    })

    return position


def real_record_outcome(position: dict, market_result: str, btc_spot: float) -> dict:
    """
    Record the outcome of a real trade after the market resolves.

    Same PnL math as paper - the token either pays $1.00 (win) or $0.00 (loss).
    """
    now       = datetime.now(timezone.utc).isoformat()
    direction = position["direction"]
    size      = position["size_usdc"]
    tokens    = position["num_tokens"]

    won     = (market_result == direction)
    pnl     = (tokens * 1.0 - size) if won else -size
    outcome = "WIN" if won else "LOSS"
    pnl_pct = (pnl / size * 100) if size > 0 else 0.0

    logger.info(
        "REAL BET RESOLVED: %s | Result: %s | PnL: $%.2f (%.1f%%)",
        outcome, market_result, pnl, pnl_pct,
    )

    _log_trade({
        "timestamp":    now,
        "action":       "CLOSE",
        "mode":         "REAL",
        "question":     position["question"],
        "condition_id": position["condition_id"],
        "direction":    direction,
        "entry_price":  position["entry_price"],
        "size_usdc":    size,
        "num_tokens":   tokens,
        "outcome":      outcome,
        "pnl_usdc":     round(pnl, 4),
        "pnl_pct":      round(pnl_pct, 2),
        "btc_spot":     btc_spot,
    })

    return {**position, "open": False, "outcome": outcome,
            "pnl_usdc": round(pnl, 4), "pnl_pct": round(pnl_pct, 2)}


# ---------------------------------------------------------------------------
# CSV LOGGING
# ---------------------------------------------------------------------------

def _log_trade(row: dict) -> None:
    """Append a trade event row to the trades CSV file."""
    os.makedirs("logs", exist_ok=True)
    file_exists = os.path.isfile(TRADES_CSV)
    try:
        with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        logger.error("Failed to write trades CSV: %s", exc)
