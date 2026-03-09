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
from typing import Any, Optional

from polymarket_client import PolymarketClient
import config

logger = logging.getLogger(__name__)

# Path to the trades CSV log.
# Call set_trades_csv() to override (e.g. for testing).
TRADES_CSV = os.path.join("logs", "trades.csv")
TRADE_ENTRIES_CSV = os.path.join("logs", "trade_entries.csv")
SIGNALS_EVALUATED_CSV = os.path.join("logs", "signals_evaluated.csv")


def set_trade_entries_csv(path: str) -> None:
    """Override trade entries CSV path (one row per trade with full signal at entry)."""
    global TRADE_ENTRIES_CSV
    TRADE_ENTRIES_CSV = path


def set_signals_evaluated_csv(path: str) -> None:
    """Override signals-evaluated CSV path (every signal, traded Y/N for refinement)."""
    global SIGNALS_EVALUATED_CSV
    SIGNALS_EVALUATED_CSV = path


TRADE_COLUMNS = [
    "timestamp",
    "action",               # "OPEN" or "CLOSE"
    "mode",                 # "PAPER" or "REAL"
    "question",             # Market question string
    "condition_id",         # On-chain market ID
    "direction",            # "YES" (UP) or "NO" (DOWN)
    "entry_price",          # Price paid per token (0-1)
    "size_usdc",            # USDC spent
    "num_tokens",           # Tokens received
    "outcome",              # "WIN", "LOSS", or "" (open)
    "pnl_usdc",             # Profit/loss in USDC (this trade)
    "pnl_pct",              # PnL as percentage of size_usdc
    "btc_spot",             # BTC price at entry for context
    "starting_balance_usdc",
    "trade_pnl_usdc",       # PnL from this trade (same as pnl_usdc for CLOSE)
    "cumulative_pnl_usdc",  # Overall profit after this trade
    "current_balance_usdc", # Starting + cumulative
    "strategy_tier",        # contrarian | momentum | fallback | late_window
]

# One row per trade at entry: full signal context for later outcome join (by condition_id).
TRADE_ENTRIES_COLUMNS = [
    "timestamp", "condition_id", "question", "direction", "entry_price", "size_usdc", "num_tokens",
    "strategy_tier", "signal_reason", "ema_fast", "ema_slow", "atr_pct", "ibs",
    "yes_price", "no_price", "btc_spot", "secs_remaining", "in_late_window", "window_start_btc",
]

# Every signal evaluation: action, reason, indicators, traded (Y/N), risk_block_reason.
SIGNALS_EVALUATED_COLUMNS = [
    "timestamp", "condition_id", "action", "reason", "tier", "yes_price", "no_price",
    "ema_fast", "ema_slow", "atr_pct", "ibs", "secs_remaining", "in_late_window",
    "traded", "risk_block_reason",
]


def set_trades_csv(path: str) -> None:
    """Override the trades CSV path (call once at startup if needed)."""
    global TRADES_CSV
    TRADES_CSV = path


# ---------------------------------------------------------------------------
# PAPER TRADING - ENTER
# ---------------------------------------------------------------------------

def paper_enter(
    market: dict,
    direction: str,
    entry_price: float,
    btc_spot: float,
    size_usdc: Optional[float] = None,
    strategy_tier: str = "",
    signal_debug_info: Optional[dict] = None,
    context: Optional[dict] = None,
) -> dict:
    """
    Simulate entering a Polymarket bet in paper mode.

    Args:
        market:      Active market dict from polymarket_client.find_active_btc_market().
        direction:   "YES" (betting UP) or "NO" (betting DOWN).
        entry_price: Token price at time of entry (0.0 - 1.0).
        btc_spot:    Current BTC/USDT price (for context logging).
        size_usdc:   USDC to risk. If None, uses config.RISK_PER_TRADE_USDC.

    Returns:
        Position dict stored in state.json.
    """
    now = datetime.now(timezone.utc).isoformat()
    if size_usdc is None:
        size_usdc = config.RISK_PER_TRADE_USDC
    num_tokens = round(size_usdc / entry_price, 4) if entry_price > 0 else 0

    info = _get_balance_info()
    logger.info(
        "=== PAPER BET: %s ===\n"
        "  Market:      %s\n"
        "  Direction:   %s\n"
        "  Entry price: %.3f per token\n"
        "  Size:        $%.2f USDC -> %.4f tokens\n"
        "  BTC spot:    $%.2f\n"
        "  Balance:     $%.2f (started $%.2f, overall profit: $%.2f)\n"
        "  Closes:      %s",
        direction,
        market["question"],
        direction,
        entry_price,
        size_usdc,
        num_tokens,
        btc_spot,
        info["current_balance_usdc"],
        info["starting_balance_usdc"],
        info["cumulative_pnl_usdc"],
        market["end_date_iso"],
    )

    position = {
        "open":            True,
        "mode":            "PAPER",
        "entry_time":      now,
        "question":        market["question"],
        "condition_id":    market["condition_id"],
        "yes_token_id":    market["yes_token_id"],
        "no_token_id":     market["no_token_id"],
        "end_date_iso":    market["end_date_iso"],
        "slug":            market.get("slug", ""),
        "direction":       direction,
        "entry_price":     entry_price,
        "size_usdc":       size_usdc,
        "num_tokens":      num_tokens,
        "btc_spot_at_entry": btc_spot,
        "strategy_tier":   strategy_tier,
        "outcome":         None,
        "pnl_usdc":        None,
    }

    _log_trade({
        "timestamp":       now,
        "action":          "OPEN",
        "mode":            "PAPER",
        "question":        market["question"],
        "condition_id":    market["condition_id"],
        "direction":       direction,
        "entry_price":     entry_price,
        "size_usdc":       size_usdc,
        "num_tokens":      num_tokens,
        "outcome":         "",
        "pnl_usdc":        "",
        "pnl_pct":         "",
        "btc_spot":        btc_spot,
        "strategy_tier":   strategy_tier,
    })
    if signal_debug_info is not None and context is not None:
        _log_trade_entry(market, direction, entry_price, size_usdc, num_tokens, strategy_tier, signal_debug_info, context, btc_spot, now)

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
    info = _get_balance_info(pnl)

    logger.info(
        "=== PAPER BET RESOLVED: %s ===\n"
        "  Market result: %s (we bet %s)\n"
        "  Outcome:       %s | Trade PnL: $%.2f (%.1f%%)\n"
        "  Overall profit: $%.2f | Current balance: $%.2f (started $%.2f)",
        outcome,
        market_result,
        direction,
        outcome,
        pnl,
        pnl_pct,
        info["cumulative_pnl_usdc"],
        info["current_balance_usdc"],
        info["starting_balance_usdc"],
    )

    _log_trade({
        "timestamp":       now,
        "action":          "CLOSE",
        "mode":            "PAPER",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       direction,
        "entry_price":     price,
        "size_usdc":       size,
        "num_tokens":      tokens,
        "outcome":         outcome,
        "pnl_usdc":        round(pnl, 4),
        "pnl_pct":         round(pnl_pct, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })

    return {
        **position,
        "open":     False,
        "outcome":  outcome,
        "pnl_usdc": round(pnl, 4),
        "pnl_pct":  round(pnl_pct, 2),
    }


# ---------------------------------------------------------------------------
# EARLY EXIT (Take Profit / Stop Loss)
# ---------------------------------------------------------------------------

def paper_close_early(
    position: dict, exit_price: float, btc_spot: float, reason: str
) -> dict:
    """
    Record an early close (TP/SL) for a paper position.
    PnL = exit_price * num_tokens - size_usdc.
    """
    now = datetime.now(timezone.utc).isoformat()
    size = position["size_usdc"]
    tokens = position["num_tokens"]
    pnl = round(exit_price * tokens - size, 4)
    pnl_pct = (pnl / size * 100) if size > 0 else 0.0
    info = _get_balance_info(pnl)

    logger.info(
        "=== PAPER EARLY EXIT (%s) ===\n"
        "  Direction:   %s | Exit price: %.3f\n"
        "  Trade PnL:  $%.2f (%.1f%%)\n"
        "  Overall profit: $%.2f | Current balance: $%.2f (started $%.2f)",
        reason,
        position["direction"],
        exit_price,
        pnl,
        pnl_pct,
        info["cumulative_pnl_usdc"],
        info["current_balance_usdc"],
        info["starting_balance_usdc"],
    )

    _log_trade({
        "timestamp":       now,
        "action":          "CLOSE",
        "mode":            "PAPER",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       position["direction"],
        "entry_price":     position["entry_price"],
        "size_usdc":       size,
        "num_tokens":      tokens,
        "outcome":         reason,
        "pnl_usdc":        pnl,
        "pnl_pct":         round(pnl_pct, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })

    return {
        **position,
        "open":     False,
        "outcome":  reason,
        "pnl_usdc": pnl,
        "pnl_pct":  round(pnl_pct, 2),
    }


def real_close_early(
    position: dict,
    exit_price: float,
    client: PolymarketClient,
    btc_spot: float,
    reason: str,
) -> Optional[dict]:
    """
    Close a real position early by selling tokens (TP/SL).
    Places a SELL order at exit_price, then records outcome.
    """
    direction = position["direction"]
    token_id = (
        position["yes_token_id"] if direction == "YES" else position["no_token_id"]
    )
    tokens = position["num_tokens"]
    size = position["size_usdc"]

    resp = client.place_order(
        token_id,
        "SELL",
        tokens,
        exit_price,
        size_in_tokens=True,
    )
    if resp is None:
        logger.warning("Early exit (%s) sell order failed. Position still open.", reason)
        return None

    pnl = round(exit_price * tokens - size, 4)
    pnl_pct = (pnl / size * 100) if size > 0 else 0.0
    now = datetime.now(timezone.utc).isoformat()

    info = _get_balance_info(pnl)
    logger.info(
        "REAL EARLY EXIT (%s): sold @ %.3f | Trade PnL: $%.2f (%.1f%%) | "
        "Overall profit: $%.2f | Current balance: $%.2f",
        reason, exit_price, pnl, pnl_pct,
        info["cumulative_pnl_usdc"],
        info["current_balance_usdc"],
    )

    _log_trade({
        "timestamp":       now,
        "action":          "CLOSE",
        "mode":            "REAL",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       direction,
        "entry_price":     position["entry_price"],
        "size_usdc":       size,
        "num_tokens":      tokens,
        "outcome":         reason,
        "pnl_usdc":        pnl,
        "pnl_pct":         round(pnl_pct, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })

    return {
        **position,
        "open":     False,
        "outcome":  reason,
        "pnl_usdc": pnl,
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
    size_usdc: Optional[float] = None,
    strategy_tier: str = "",
    signal_debug_info: Optional[dict] = None,
    context: Optional[dict] = None,
) -> Optional[dict]:
    """
    Place a real Polymarket order for the given direction.

    Args:
        client:      PolymarketClient instance.
        market:      Active market dict.
        direction:   "YES" or "NO".
        entry_price: Current token price to use as limit price.
        btc_spot:    BTC spot price for logging.
        size_usdc:   USDC to risk. If None, uses config.RISK_PER_TRADE_USDC.

    Returns:
        Position dict on success, None if order failed.
    """
    if not config.REAL_TRADING:
        logger.error("real_enter() called but REAL_TRADING=False. Aborting.")
        return None

    if size_usdc is None:
        size_usdc = config.RISK_PER_TRADE_USDC
    token_id = market["yes_token_id"] if direction == "YES" else market["no_token_id"]

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
        "strategy_tier":     strategy_tier,
        "order_id":          resp.get("orderID", "") if isinstance(resp, dict) else "",
        "outcome":           None,
        "pnl_usdc":          None,
    }

    _log_trade({
        "timestamp":       now,
        "action":          "OPEN",
        "mode":            "REAL",
        "question":        market["question"],
        "condition_id":    market["condition_id"],
        "direction":       direction,
        "entry_price":     entry_price,
        "size_usdc":       size_usdc,
        "num_tokens":      num_tokens,
        "outcome":         "",
        "pnl_usdc":        "",
        "pnl_pct":         "",
        "btc_spot":        btc_spot,
        "strategy_tier":   strategy_tier,
    })
    if signal_debug_info is not None and context is not None:
        _log_trade_entry(market, direction, entry_price, size_usdc, num_tokens, strategy_tier, signal_debug_info, context, btc_spot, now)

    return position


def real_record_outcome(position: dict, market_result: str, btc_spot: float) -> dict:
    """
    Record the outcome of a real trade after the market resolves.

    Same PnL math as paper - the token either pays $1.00 (win) or $0.00 (loss).
    If AUTO_REDEEM_ENABLED, redeems winning tokens to USDC.
    """
    now       = datetime.now(timezone.utc).isoformat()
    direction = position["direction"]
    size      = position["size_usdc"]
    tokens    = position["num_tokens"]

    won     = (market_result == direction)
    pnl     = (tokens * 1.0 - size) if won else -size
    outcome = "WIN" if won else "LOSS"
    pnl_pct = (pnl / size * 100) if size > 0 else 0.0

    info = _get_balance_info(pnl)
    logger.info(
        "REAL BET RESOLVED: %s | Result: %s | Trade PnL: $%.2f (%.1f%%) | "
        "Overall profit: $%.2f | Current balance: $%.2f",
        outcome, market_result, pnl, pnl_pct,
        info["cumulative_pnl_usdc"],
        info["current_balance_usdc"],
    )

    if won and getattr(config, "AUTO_REDEEM_ENABLED", False):
        try:
            from redeem import redeem_winning_position
            tx = redeem_winning_position(
                condition_id=position["condition_id"],
                direction=direction,
                num_tokens=tokens,
                neg_risk=False,
            )
            if tx:
                logger.info("AUTO_REDEEM: Winnings redeemed. USDC returned to wallet.")
            else:
                logger.warning("AUTO_REDEEM: Redeem skipped or failed. Claim manually on Polymarket.")
        except Exception as exc:
            logger.warning("AUTO_REDEEM: Error during redeem: %s", exc)

    _log_trade({
        "timestamp":       now,
        "action":          "CLOSE",
        "mode":            "REAL",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       direction,
        "entry_price":     position["entry_price"],
        "size_usdc":       size,
        "num_tokens":      tokens,
        "outcome":         outcome,
        "pnl_usdc":        round(pnl, 4),
        "pnl_pct":         round(pnl_pct, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })

    return {**position, "open": False, "outcome": outcome,
            "pnl_usdc": round(pnl, 4), "pnl_pct": round(pnl_pct, 2)}


# ---------------------------------------------------------------------------
# CSV LOGGING
# ---------------------------------------------------------------------------

def _get_balance_info(pnl_usdc: Optional[float] = None) -> dict:
    """Get starting balance, cumulative PnL, current balance from risk state."""
    from risk import RiskManager
    rm = RiskManager()
    rm.reload()
    starting = rm.state.get("starting_balance_usdc", 0) or config.BANKROLL_START_USDC
    cumulative = rm.state.get("cumulative_pnl_usdc", 0.0)
    if pnl_usdc is not None:
        cumulative = round(cumulative + pnl_usdc, 4)
    current = round(starting + cumulative, 2)
    return {
        "starting_balance_usdc": starting,
        "cumulative_pnl_usdc": cumulative,
        "current_balance_usdc": current,
    }


def _log_trade(row: dict) -> None:
    """Append a trade event row to the trades CSV file."""
    row.setdefault("strategy_tier", "")
    pnl = row.get("pnl_usdc")
    if pnl != "" and pnl is not None:
        info = _get_balance_info(float(pnl))
        row["trade_pnl_usdc"] = pnl
    else:
        info = _get_balance_info()
        row["trade_pnl_usdc"] = ""
    row["starting_balance_usdc"] = info["starting_balance_usdc"]
    row["cumulative_pnl_usdc"] = info["cumulative_pnl_usdc"]
    row["current_balance_usdc"] = info["current_balance_usdc"]

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


def _log_trade_entry(
    market: dict,
    direction: str,
    entry_price: float,
    size_usdc: float,
    num_tokens: float,
    strategy_tier: str,
    signal_debug_info: dict,
    context: dict,
    btc_spot: float,
    timestamp: str,
) -> None:
    """Append one row per trade with full signal at entry (for outcome join by condition_id)."""
    secs = context.get("secs_remaining")
    in_late = context.get("in_late_window", False)
    window_btc = context.get("window_start_btc")
    row = {
        "timestamp":       timestamp,
        "condition_id":    market.get("condition_id", ""),
        "question":        market.get("question", ""),
        "direction":       direction,
        "entry_price":     entry_price,
        "size_usdc":       size_usdc,
        "num_tokens":      num_tokens,
        "strategy_tier":   strategy_tier,
        "signal_reason":   signal_debug_info.get("reason", ""),
        "ema_fast":        signal_debug_info.get("ema_fast", ""),
        "ema_slow":        signal_debug_info.get("ema_slow", ""),
        "atr_pct":         signal_debug_info.get("atr_pct", ""),
        "ibs":             signal_debug_info.get("ibs", ""),
        "yes_price":       signal_debug_info.get("yes_price", ""),
        "no_price":        signal_debug_info.get("no_price", ""),
        "btc_spot":        btc_spot,
        "secs_remaining":  secs if secs is not None else "",
        "in_late_window":  in_late,
        "window_start_btc": window_btc if window_btc is not None else "",
    }
    os.makedirs("logs", exist_ok=True)
    file_exists = os.path.isfile(TRADE_ENTRIES_CSV)
    try:
        with open(TRADE_ENTRIES_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_ENTRIES_COLUMNS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        logger.error("Failed to write trade entries CSV: %s", exc)


def log_signal_evaluated(
    condition_id: str,
    action: str,
    debug_info: dict,
    yes_price: float,
    no_price: float,
    context: dict,
    traded: bool,
    risk_block_reason: str = "",
) -> None:
    """
    Log every signal evaluation for refinement (join to trades by condition_id when traded=Y).
    Call from bot after check_signal and after risk gate / entry decision.
    """
    now = datetime.now(timezone.utc).isoformat()
    secs = context.get("secs_remaining")
    in_late = context.get("in_late_window", False)
    row = {
        "timestamp":         now,
        "condition_id":      condition_id,
        "action":            action,
        "reason":            debug_info.get("reason", ""),
        "tier":              debug_info.get("tier", ""),
        "yes_price":         yes_price,
        "no_price":          no_price,
        "ema_fast":          debug_info.get("ema_fast", ""),
        "ema_slow":          debug_info.get("ema_slow", ""),
        "atr_pct":           debug_info.get("atr_pct", ""),
        "ibs":               debug_info.get("ibs", ""),
        "secs_remaining":    secs if secs is not None else "",
        "in_late_window":    in_late,
        "traded":            "Y" if traded else "N",
        "risk_block_reason": risk_block_reason,
    }
    os.makedirs("logs", exist_ok=True)
    file_exists = os.path.isfile(SIGNALS_EVALUATED_CSV)
    try:
        with open(SIGNALS_EVALUATED_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SIGNALS_EVALUATED_COLUMNS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        logger.error("Failed to write signals evaluated CSV: %s", exc)
