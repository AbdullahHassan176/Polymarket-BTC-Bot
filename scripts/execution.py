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
import time
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from polymarket_client import PolymarketClient
import config

logger = logging.getLogger(__name__)

# Path to the trades CSV log.
# Call set_trades_csv() to override (e.g. for testing).
TRADES_CSV = os.path.join("logs", "trades.csv")
TRADE_ENTRIES_CSV = os.path.join("logs", "trade_entries.csv")
SIGNALS_EVALUATED_CSV = os.path.join("logs", "signals_evaluated.csv")
REVERSAL_BID_TRACE_CSV = os.path.join("logs", "reversal_bid_trace.csv")
PRICE_PATHS_CSV = os.path.join("logs", "price_paths.csv")


def set_trade_entries_csv(path: str) -> None:
    """Override trade entries CSV path (one row per trade with full signal at entry)."""
    global TRADE_ENTRIES_CSV
    TRADE_ENTRIES_CSV = path


def set_signals_evaluated_csv(path: str) -> None:
    """Override signals-evaluated CSV path (every signal, traded Y/N for refinement)."""
    global SIGNALS_EVALUATED_CSV
    SIGNALS_EVALUATED_CSV = path


def set_price_paths_csv(path: str) -> None:
    """Override price paths CSV (per-tick yes/no prices for paper run analysis)."""
    global PRICE_PATHS_CSV
    PRICE_PATHS_CSV = path


def set_paper_run_dir(dir_path: str) -> None:
    """Point all paper-run logs to the given directory (price paths, signals, paper trades)."""
    import os
    if not dir_path:
        return
    dir_path = os.path.normpath(dir_path)
    os.makedirs(dir_path, exist_ok=True)
    set_trades_csv(os.path.join(dir_path, "paper_trades.csv"))
    set_trade_entries_csv(os.path.join(dir_path, "trade_entries.csv"))
    set_signals_evaluated_csv(os.path.join(dir_path, "signals_evaluated.csv"))
    set_price_paths_csv(os.path.join(dir_path, "price_paths.csv"))
    logger.info("Paper run dir set: %s", dir_path)


PRICE_PATH_COLUMNS = (
    "timestamp_utc", "asset", "condition_id", "question", "yes_price", "no_price",
    "secs_remaining", "issues",
)


def log_price_path(
    asset: str,
    condition_id: str,
    question: str,
    yes_price: float,
    no_price: float,
    secs_remaining: float,
    issues: Optional[str] = None,
) -> None:
    """Append one row to price_paths CSV (per-second or per-tick for paper run)."""
    if not getattr(config, "LOG_PRICE_PATH", False):
        return
    path = PRICE_PATHS_CSV
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    file_exists = os.path.isfile(path)
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PRICE_PATH_COLUMNS, extrasaction="ignore")
            if not file_exists:
                w.writeheader()
            w.writerow({
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "asset": asset,
                "condition_id": (condition_id or "")[:64],
                "question": (question or "")[:200],
                "yes_price": round(yes_price, 4),
                "no_price": round(no_price, 4),
                "secs_remaining": round(secs_remaining, 1),
                "issues": issues or "",
            })
    except OSError as exc:
        logger.debug("Failed to write price path row: %s", exc)


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
    "entry_mode",           # single | pyramid (for reversal strategy comparison)
    "entry_tiers",          # e.g. "0.15" or "0.20,0.15,0.10" (tiers used)
]

# One row per trade at entry: full signal context for later outcome join (by condition_id).
TRADE_ENTRIES_COLUMNS = [
    "timestamp", "condition_id", "question", "direction", "entry_price", "size_usdc", "num_tokens",
    "strategy_tier", "entry_mode", "entry_tiers", "signal_reason", "ema_fast", "ema_slow", "atr_pct", "ibs",
    "yes_price", "no_price", "btc_spot", "secs_remaining", "in_late_window", "window_start_btc",
]

# Every signal evaluation: action, reason, indicators, traded (Y/N), risk_block_reason.
SIGNALS_EVALUATED_COLUMNS = [
    "timestamp", "condition_id", "action", "reason", "tier", "entry_mode", "yes_price", "no_price",
    "ema_fast", "ema_slow", "atr_pct", "ibs", "secs_remaining", "in_late_window",
    "traded", "risk_block_reason",
]

STRATEGY_COMPARISON_CSV = os.path.join("logs", "strategy_comparison.csv")


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

    entry_threshold = (signal_debug_info or {}).get("entry_threshold")
    entry_mode = (signal_debug_info or {}).get("entry_mode", "single")
    position = {
        "open":            True,
        "mode":            "PAPER",
        "original_num_tokens": num_tokens,
        "entry_threshold": entry_threshold,
        "entry_time":      now,
        "question":        market["question"],
        "condition_id":    market["condition_id"],
        "yes_token_id":    market["yes_token_id"],
        "no_token_id":     market["no_token_id"],
        "end_date_iso":    market["end_date_iso"],
        "slug":            market.get("slug", ""),
        "asset":           market.get("asset", "BTC"),
        "direction":       direction,
        "entry_price":     entry_price,
        "size_usdc":       size_usdc,
        "num_tokens":      num_tokens,
        "btc_spot_at_entry": btc_spot,
        "strategy_tier":   strategy_tier,
        "entry_mode":      entry_mode,
        "slices": (
            [{"entry_price": entry_price, "size_usdc": size_usdc, "num_tokens": num_tokens,
              "original_num_tokens": num_tokens, "entry_threshold": entry_threshold,
              "reversal_tiers_hit": []}]
            if entry_mode == "pyramid" else None
        ),
        "outcome":         None,
        "pnl_usdc":        None,
    }

    entry_tiers_str = str(entry_threshold) if entry_mode == "single" else str(entry_threshold)
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
        "entry_mode":      entry_mode,
        "entry_tiers":     entry_tiers_str,
    })
    if signal_debug_info is not None and context is not None:
        _log_trade_entry(market, direction, entry_price, size_usdc, num_tokens, strategy_tier,
                         signal_debug_info, context, btc_spot, now)

    return position


def paper_add_slice(
    existing_position: dict,
    market: dict,
    direction: str,
    entry_price: float,
    btc_spot: float,
    size_usdc: float,
    entry_threshold: float,
) -> dict:
    """Add a pyramid slice to existing position. Returns updated position."""
    num_tokens = round(size_usdc / entry_price, 4) if entry_price > 0 else 0
    slices = list(existing_position.get("slices") or [])
    slices.append({
        "entry_price": entry_price,
        "size_usdc": size_usdc,
        "num_tokens": num_tokens,
        "original_num_tokens": num_tokens,
        "reversal_tiers_hit": [],
    })
    total_size = sum(s["size_usdc"] for s in slices)
    total_tokens = sum(s["num_tokens"] for s in slices)
    updated = {
        **existing_position,
        "slices": slices,
        "size_usdc": total_size,
        "num_tokens": total_tokens,
        "entry_price": total_size / total_tokens if total_tokens else entry_price,
        "original_num_tokens": total_tokens,
    }
    logger.info(
        "PAPER ADD SLICE: %s @ %.2f | +$%.2f -> %.4f tokens | Total: $%.2f, %.4f tokens",
        direction, entry_price, size_usdc, num_tokens, total_size, total_tokens,
    )
    _log_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "OPEN",
        "mode": "PAPER",
        "question": market["question"],
        "condition_id": market["condition_id"],
        "direction": direction,
        "entry_price": entry_price,
        "size_usdc": size_usdc,
        "num_tokens": num_tokens,
        "outcome": "",
        "pnl_usdc": "",
        "pnl_pct": "",
        "btc_spot": btc_spot,
        "strategy_tier": existing_position.get("strategy_tier", "reversal"),
        "entry_mode": "pyramid",
        "entry_tiers": ",".join(str(s["entry_price"]) for s in slices),
    })
    return updated


def real_add_slice(
    existing_position: dict,
    market: dict,
    client: "PolymarketClient",
    direction: str,
    entry_price: float,
    btc_spot: float,
    size_usdc: float,
    entry_threshold: float,
) -> Optional[dict]:
    """Add a pyramid slice (real order). Returns updated position or None."""
    if not config.REAL_TRADING:
        return None
    token_id = market["yes_token_id"] if direction == "YES" else market["no_token_id"]
    resp = client.place_order(token_id=token_id, side="BUY", size_usdc=size_usdc, price=entry_price, fok=True)
    if resp is None:
        return None
    num_tokens = round(size_usdc / entry_price, 4) if entry_price > 0 else 0
    slices = list(existing_position.get("slices") or [])
    slices.append({
        "entry_price": entry_price,
        "size_usdc": size_usdc,
        "num_tokens": num_tokens,
        "original_num_tokens": num_tokens,
        "reversal_tiers_hit": [],
    })
    total_size = sum(s["size_usdc"] for s in slices)
    total_tokens = sum(s["num_tokens"] for s in slices)
    updated = {
        **existing_position,
        "slices": slices,
        "size_usdc": total_size,
        "num_tokens": total_tokens,
        "entry_price": total_size / total_tokens if total_tokens else entry_price,
        "original_num_tokens": total_tokens,
    }
    logger.info(
        "REAL ADD SLICE: %s @ %.2f | +$%.2f -> %.4f tokens | Total: $%.2f, %.4f tokens",
        direction, entry_price, size_usdc, num_tokens, total_size, total_tokens,
    )
    _log_trade({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "OPEN",
        "mode": "REAL",
        "question": market["question"],
        "condition_id": market["condition_id"],
        "direction": direction,
        "entry_price": entry_price,
        "size_usdc": size_usdc,
        "num_tokens": num_tokens,
        "outcome": "", "pnl_usdc": "", "pnl_pct": "",
        "btc_spot": btc_spot,
        "strategy_tier": existing_position.get("strategy_tier", "reversal"),
        "entry_mode": "pyramid",
        "entry_tiers": ",".join(str(round(s["entry_price"], 2)) for s in slices),
    })
    return updated


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
        "entry_mode":      position.get("entry_mode", ""),
        "entry_tiers":     ",".join(str(round(s.get("entry_price", 0), 2)) for s in (position.get("slices") or []))
        if position.get("slices") else str(position.get("entry_threshold", "")),
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


def paper_force_clear_stale(position: dict) -> dict:
    """
    Force-clear a paper position when we can't get bid (404) and market has ended.
    Records outcome as CLEARED_STALE, pnl=0. Prevents bot from being stuck forever.
    """
    size = position["size_usdc"]
    pnl = 0.0
    pnl_pct = 0.0
    info = _get_balance_info(pnl)
    logger.warning(
        "=== PAPER FORCE-CLEAR STALE ===\n"
        "  Position: %s | %s\n"
        "  Could not fetch bid (token 404). Market likely resolved. Cleared to unblock.\n"
        "  PnL recorded as $0 (outcome unknown). Balance: $%.2f",
        position.get("question", "?"),
        position.get("direction", "?"),
        info["current_balance_usdc"],
    )
    _log_trade({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "action":          "CLOSE",
        "mode":            "PAPER",
        "question":        position.get("question", ""),
        "condition_id":    position.get("condition_id", ""),
        "direction":       position.get("direction", ""),
        "entry_price":     position.get("entry_price", 0),
        "size_usdc":       size,
        "num_tokens":      position.get("num_tokens", 0),
        "outcome":         "CLEARED_STALE",
        "pnl_usdc":        pnl,
        "pnl_pct":         pnl_pct,
        "btc_spot":        0,
        "strategy_tier":   position.get("strategy_tier", ""),
    })
    return {**position, "open": False, "outcome": "CLEARED_STALE", "pnl_usdc": 0.0, "pnl_pct": 0.0}


def real_force_clear_stale(position: dict) -> dict:
    """
    Force-clear a real position from state when resolution is unavailable.
    Records CLEARED_STALE with the full stake as a LOSS (conservative accounting).
    Any genuine wins must be manually redeemed via claim_unclaimed.bat.

    Note: previously recorded pnl=0.0 which masked real losses in the PnL log.
    Now records pnl=-size_usdc so the bot's running PnL reflects reality.
    The reconcile script will correct this when a Polymarket export is provided.
    """
    size  = position.get("size_usdc", 0.0) or 0.0
    tokens = position.get("num_tokens", 0.0) or 0.0
    # Conservative: assume full loss of remaining stake. True PnL corrected by reconcile.
    pnl   = -round(size, 4)
    pnl_pct = -100.0 if size > 0 else 0.0
    info  = _get_balance_info(pnl)
    logger.warning(
        "=== REAL FORCE-CLEAR STALE ===\n"
        "  Position: %s | %s | %.4f tokens | $%.2f stake\n"
        "  Recording as -$%.2f loss (conservative). Run claim_unclaimed.bat if this was a WIN.\n"
        "  Balance: $%.2f",
        position.get("question", "?"),
        position.get("direction", "?"),
        tokens,
        size,
        size,
        info["current_balance_usdc"],
    )
    _log_trade({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "action":          "CLOSE",
        "mode":            "REAL",
        "question":        position.get("question", ""),
        "condition_id":    position.get("condition_id", ""),
        "direction":       position.get("direction", ""),
        "entry_price":     position.get("entry_price", 0),
        "size_usdc":       size,
        "num_tokens":      tokens,
        "outcome":         "CLEARED_STALE",
        "pnl_usdc":        pnl,
        "pnl_pct":         pnl_pct,
        "btc_spot":        0,
        "strategy_tier":   position.get("strategy_tier", ""),
    })
    # Return the actual negative pnl so bot.py can pass it to risk.record_trade_closed().
    # Previously returned 0.0 which caused cumulative_pnl_usdc in state to never decrement,
    # letting the bot trade as if it had full capital even after real losses.
    return {**position, "open": False, "outcome": "CLEARED_STALE", "pnl_usdc": pnl, "pnl_pct": pnl_pct}


def paper_close_partial(
    position: dict, bid: float, tier_price: float, pct: float, btc_spot: float,
    slice_idx: Optional[int] = None,
) -> Tuple[dict, float]:
    """
    Simulate partial sell for tiered TP. Sell pct% of original tokens at bid.
    For pyramid (slices), pass slice_idx to sell from that slice.
    Returns (updated_position, pnl_this_slice). Position stays open.
    """
    slices = position.get("slices")
    if slices and slice_idx is not None and 0 <= slice_idx < len(slices):
        s = slices[slice_idx]
        orig = s.get("original_num_tokens") or s["num_tokens"]
        size = s["size_usdc"]
    else:
        orig = position.get("original_num_tokens") or position["num_tokens"]
        size = position["size_usdc"]
    tokens_to_sell = round(orig * (pct / 100.0), 4)
    size_sold = round(size * (pct / 100.0), 4)
    pnl = round(bid * tokens_to_sell - size_sold, 4)
    if slices and slice_idx is not None and 0 <= slice_idx < len(slices):
        new_slices = [dict(s) for s in slices]
        new_slices[slice_idx] = {
            **new_slices[slice_idx],
            "num_tokens": round(new_slices[slice_idx]["num_tokens"] - tokens_to_sell, 4),
            "reversal_tiers_hit": list(new_slices[slice_idx].get("reversal_tiers_hit", [])) + [tier_price],
        }
        new_tokens = round(sum(s["num_tokens"] for s in new_slices), 4)
        tiers_hit = list(position.get("reversal_tiers_hit", []))
        tiers_hit.append(tier_price)
        updated = {
            **position,
            "slices": new_slices,
            "num_tokens": new_tokens,
            "reversal_tiers_hit": tiers_hit,
            "partial_pnl_usdc": position.get("partial_pnl_usdc", 0.0) + pnl,
        }
    else:
        new_tokens = round(position["num_tokens"] - tokens_to_sell, 4)
        tiers_hit = list(position.get("reversal_tiers_hit", []))
        tiers_hit.append(tier_price)
        updated = {
            **position,
            "num_tokens": new_tokens,
            "reversal_tiers_hit": tiers_hit,
            "partial_pnl_usdc": position.get("partial_pnl_usdc", 0.0) + pnl,
        }
    partial_pnl = position.get("partial_pnl_usdc", 0.0) + pnl
    info = _get_balance_info(pnl)
    logger.info(
        "=== PAPER TIERED TP (%.2f @ %.2f) ===\n"
        "  Sold %.4f tokens (%.0f%%) @ %.3f | PnL: $%.2f | Remaining: %.4f tokens\n"
        "  Cumulative partial: $%.2f | Balance: $%.2f",
        tier_price * 100, pct, tokens_to_sell, pct, bid, pnl, new_tokens,
        partial_pnl, info["current_balance_usdc"],
    )
    _log_trade({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "action":          "CLOSE",
        "mode":            "PAPER",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       position["direction"],
        "entry_price":     position["entry_price"],
        "size_usdc":       size_sold,
        "num_tokens":      tokens_to_sell,
        "outcome":         f"TP_TIER_{tier_price:.2f}",
        "pnl_usdc":        pnl,
        "pnl_pct":         round((pnl / size_sold * 100) if size_sold else 0, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })
    return updated, pnl


def _sell_tokens_chunked(
    client: PolymarketClient,
    token_id: str,
    tokens_to_sell: float,
    price: float,
) -> Tuple[float, bool]:
    """
    Place SELL order(s) using FOK (Fill-or-Kill) to guarantee real on-chain execution.
    GTC orders were silently left unfilled in the CLOB while the bot logged phantom PnL.

    Strategy:
      1. Try FOK at `price` (current bid).
      2. On failure, step down by SELL_FOK_PRICE_STEP (default 0.01) up to SELL_FOK_MAX_STEPS times.
      3. If all attempts fail, return (0, False) — position stays open, no fake PnL logged.

    Returns (tokens_actually_sold, all_success).
    """
    if not tokens_to_sell or price <= 0:
        return 0.0, False

    max_usdc  = getattr(config, "MAX_SELL_ORDER_USDC", 0.0)
    step      = getattr(config, "SELL_FOK_PRICE_STEP", 0.01)
    max_steps = getattr(config, "SELL_FOK_MAX_STEPS", 3)

    # Chunk size (to stay within notional cap per order)
    chunk_tokens = round(max_usdc / price, 4) if max_usdc > 0 and price > 0 else tokens_to_sell
    if chunk_tokens <= 0:
        chunk_tokens = tokens_to_sell

    sold      = 0.0
    remaining = round(tokens_to_sell, 4)

    while remaining > 1e-6:
        chunk = min(remaining, chunk_tokens)
        chunk = round(chunk, 4)
        if chunk <= 0:
            break

        # Try FOK at progressively lower prices until we get a fill
        filled = False
        attempt_price = price
        for step_i in range(max_steps + 1):
            attempt_price = round(price - step_i * step, 4)
            if attempt_price <= 0:
                break
            resp = client.place_order(
                token_id, "SELL", chunk, attempt_price, size_in_tokens=True, fok=True
            )
            if resp is not None:
                sold += chunk
                remaining = round(remaining - chunk, 4)
                if step_i > 0:
                    logger.info(
                        "FOK sell filled at %.3f (step -%d from %.3f).",
                        attempt_price, step_i, price,
                    )
                filled = True
                break
            logger.debug("FOK sell at %.3f rejected (step %d). Retrying lower...", attempt_price, step_i)

        if not filled:
            logger.warning(
                "FOK sell failed at all price steps (%.3f → %.3f). "
                "Sold %.4f of %.4f tokens. Position stays open.",
                price, attempt_price, sold, tokens_to_sell,
            )
            return round(sold, 4), sold >= 1e-6

    return round(sold, 4), abs(remaining) < 1e-6


def real_close_partial(
    position: dict, bid: float, tier_price: float, pct: float,
    client: PolymarketClient, btc_spot: float,
    slice_idx: Optional[int] = None,
) -> Optional[Tuple[dict, float]]:
    """
    Partial sell for tiered TP (real mode). For pyramid, pass slice_idx.
    Returns (updated_position, pnl) or None on failure.
    """
    direction = position["direction"]
    token_id = (
        position["yes_token_id"] if direction == "YES" else position["no_token_id"]
    )
    slices = position.get("slices")
    if slices and slice_idx is not None and 0 <= slice_idx < len(slices):
        s = slices[slice_idx]
        orig = s.get("original_num_tokens") or s["num_tokens"]
        size = s["size_usdc"]
    else:
        orig = position.get("original_num_tokens") or position["num_tokens"]
        size = position["size_usdc"]
    tokens_to_sell = round(orig * (pct / 100.0), 4)
    size_sold_full = round(size * (pct / 100.0), 4)
    tokens_actually_sold, _ = _sell_tokens_chunked(client, token_id, tokens_to_sell, bid)
    if tokens_actually_sold <= 0:
        logger.warning("Tiered TP sell at %.2f failed. Position unchanged.", tier_price)
        return None
    # If we sold less than requested (thin book), prorate size_sold and pnl
    size_sold = round(size * (tokens_actually_sold / orig), 4) if orig else 0
    pnl = round(bid * tokens_actually_sold - size_sold, 4)
    partial_pnl = position.get("partial_pnl_usdc", 0.0) + pnl
    if slices and slice_idx is not None and 0 <= slice_idx < len(slices):
        new_slices = [dict(s) for s in slices]
        new_slices[slice_idx] = {
            **new_slices[slice_idx],
            "num_tokens": round(new_slices[slice_idx]["num_tokens"] - tokens_actually_sold, 4),
            "reversal_tiers_hit": list(new_slices[slice_idx].get("reversal_tiers_hit", [])) + [tier_price],
        }
        new_tokens = round(sum(s["num_tokens"] for s in new_slices), 4)
        tiers_hit = list(position.get("reversal_tiers_hit", []))
        tiers_hit.append(tier_price)
        updated = {
            **position,
            "slices": new_slices,
            "num_tokens": new_tokens,
            "reversal_tiers_hit": tiers_hit,
            "partial_pnl_usdc": partial_pnl,
        }
    else:
        new_tokens = round(position["num_tokens"] - tokens_actually_sold, 4)
        tiers_hit = list(position.get("reversal_tiers_hit", []))
        tiers_hit.append(tier_price)
        updated = {
            **position,
            "num_tokens": new_tokens,
            "reversal_tiers_hit": tiers_hit,
            "partial_pnl_usdc": partial_pnl,
        }
    info = _get_balance_info(pnl)
    logger.info(
        "REAL TIERED TP (%.2f @ %.0f%%): sold %.4f tokens @ %.3f | PnL $%.2f | Remaining: %.4f",
        tier_price * 100, pct, tokens_actually_sold, bid, pnl, new_tokens,
    )
    _log_trade({
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "action":          "CLOSE",
        "mode":            "REAL",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       direction,
        "entry_price":     position["entry_price"],
        "size_usdc":       size_sold,
        "num_tokens":      tokens_actually_sold,
        "outcome":         f"TP_TIER_{tier_price:.2f}",
        "pnl_usdc":        pnl,
        "pnl_pct":         round((pnl / size_sold * 100) if size_sold else 0, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })
    return updated, pnl


def real_close_early(
    position: dict,
    exit_price: float,
    client: PolymarketClient,
    btc_spot: float,
    reason: str,
) -> Optional[dict]:
    """
    Close a real position early by selling tokens (TP/SL).
    Uses chunked sells when MAX_SELL_ORDER_USDC is set. On partial fill (thin book),
    returns dict with "partial": True and "updated_position" so caller can keep the remainder.
    """
    direction = position["direction"]
    token_id = (
        position["yes_token_id"] if direction == "YES" else position["no_token_id"]
    )
    tokens = position["num_tokens"]
    size = position["size_usdc"]

    tokens_sold, all_success = _sell_tokens_chunked(
        client, token_id, round(tokens, 4), exit_price
    )
    if tokens_sold <= 0:
        logger.warning("Early exit (%s) sell order failed. Position still open.", reason)
        return None

    # Prorate size and pnl by how much we actually sold
    size_sold = round(size * (tokens_sold / tokens), 4) if tokens > 0 else 0
    pnl = round(exit_price * tokens_sold - size_sold, 4)
    pnl_pct = (pnl / size_sold * 100) if size_sold > 0 else 0.0
    now = datetime.now(timezone.utc).isoformat()

    info = _get_balance_info(pnl)
    if all_success:
        logger.info(
            "REAL EARLY EXIT (%s): sold @ %.3f | Trade PnL: $%.2f (%.1f%%) | "
            "Overall profit: $%.2f | Current balance: $%.2f",
            reason, exit_price, pnl, pnl_pct,
            info["cumulative_pnl_usdc"],
            info["current_balance_usdc"],
        )
    else:
        logger.info(
            "REAL EARLY EXIT (%s) PARTIAL: sold %.4f of %.4f tokens @ %.3f | PnL $%.2f | remainder open",
            reason, tokens_sold, tokens, exit_price, pnl,
        )

    _log_trade({
        "timestamp":       now,
        "action":          "CLOSE",
        "mode":            "REAL",
        "question":        position["question"],
        "condition_id":    position["condition_id"],
        "direction":       direction,
        "entry_price":     position["entry_price"],
        "size_usdc":       size_sold,
        "num_tokens":      tokens_sold,
        "outcome":         reason,
        "pnl_usdc":        pnl,
        "pnl_pct":         round(pnl_pct, 2),
        "btc_spot":        btc_spot,
        "strategy_tier":   position.get("strategy_tier", ""),
    })

    if all_success:
        return {
            **position,
            "open":     False,
            "outcome":  reason,
            "pnl_usdc": pnl,
            "pnl_pct":  round(pnl_pct, 2),
        }
    # Partial: return updated position so bot keeps the remainder and retries next tick
    remaining_tokens = round(tokens - tokens_sold, 4)
    remaining_size = round(size - size_sold, 4)
    updated_position = {
        **position,
        "num_tokens":   remaining_tokens,
        "size_usdc":   remaining_size,
        "open":         True,
    }
    if position.get("slices"):
        # One effective slice with remainder so tiered TP can continue next tick
        first = position["slices"][0]
        updated_position["slices"] = [{
            **first,
            "num_tokens":        remaining_tokens,
            "size_usdc":         remaining_size,
            "original_num_tokens": first.get("original_num_tokens", remaining_tokens),
        }]
    return {
        "partial":           True,
        "pnl_usdc":          pnl,
        "updated_position":  updated_position,
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

    # Use FOK for BUY so the position is only recorded after a confirmed fill.
    # GTC buys are accepted by the CLOB immediately (returning an order ID) even if
    # they never fill, which would create a phantom position in state with no on-chain tokens.
    resp = client.place_order(
        token_id=token_id,
        side="BUY",
        size_usdc=size_usdc,
        price=entry_price,
        fok=True,
    )

    if resp is None:
        logger.error("Real BUY order not filled (FOK) for %s. No position recorded.", direction)
        return None

    now        = datetime.now(timezone.utc).isoformat()
    num_tokens = round(size_usdc / entry_price, 4)
    entry_threshold = (signal_debug_info or {}).get("entry_threshold")
    entry_mode = (signal_debug_info or {}).get("entry_mode", "single")

    position = {
        "open":              True,
        "mode":              "REAL",
        "original_num_tokens": num_tokens,
        "entry_threshold":   entry_threshold,
        "entry_mode":        entry_mode,
        "slices": (
            [{"entry_price": entry_price, "size_usdc": size_usdc, "num_tokens": num_tokens,
              "original_num_tokens": num_tokens, "entry_threshold": entry_threshold,
              "reversal_tiers_hit": []}]
            if entry_mode == "pyramid" else None
        ),
        "entry_time":        now,
        "question":          market["question"],
        "condition_id":      market["condition_id"],
        "yes_token_id":      market["yes_token_id"],
        "no_token_id":       market["no_token_id"],
        "end_date_iso":      market["end_date_iso"],
        "slug":              market.get("slug", ""),
        "asset":             market.get("asset", "BTC"),
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
        "entry_mode":      entry_mode,
        "entry_tiers":     str(entry_threshold) if entry_mode == "single" else str(entry_threshold),
    })
    if signal_debug_info is not None and context is not None:
        _log_trade_entry(market, direction, entry_price, size_usdc, num_tokens, strategy_tier,
                         signal_debug_info, context, btc_spot, now)

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
                neg_risk=True,
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
    row.setdefault("entry_mode", "")
    row.setdefault("entry_tiers", "")
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
        "entry_mode":      signal_debug_info.get("entry_mode", "single"),
        "entry_tiers":     str(signal_debug_info.get("entry_threshold", "")),
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


def log_reversal_bid_sample(
    condition_id: str, bid: float, entry_price: float, direction: str,
    num_tokens: float, size_usdc: float,
    entry_mode: str = "",
    entry_tiers: str = "",
) -> None:
    """Log bid price sample for reversal position (for tiered TP + strategy comparison)."""
    os.makedirs("logs", exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "condition_id": condition_id,
        "bid": round(bid, 4),
        "entry_price": round(entry_price, 4),
        "direction": direction,
        "num_tokens": round(num_tokens, 4),
        "size_usdc": round(size_usdc, 4),
        "entry_mode": entry_mode,
        "entry_tiers": entry_tiers,
    }
    cols = list(row.keys())
    file_exists = os.path.isfile(REVERSAL_BID_TRACE_CSV)
    try:
        with open(REVERSAL_BID_TRACE_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            if not file_exists:
                w.writeheader()
            w.writerow(row)
    except OSError as exc:
        logger.debug("Failed to write reversal bid trace: %s", exc)


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
