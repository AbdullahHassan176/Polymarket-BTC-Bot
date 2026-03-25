"""
bot.py  -  Main trading loop for the Polymarket BTC 5-Minute Direction Bot.

How it works each iteration:
  1. Kill switch check (STOP_BOT.txt).
  2. Daily counter reset at UTC midnight.
  3. Process all open positions: resolution, TP/SL, tiered TP, force-clear.
  4. Find the currently active BTC 5-minute market.
  5. Check entry timing: early window (first Ns) or late window (last Ns, strong move + mispricing).
  6. Fetch BTC candles, compute EMA/ATR indicators.
  7. Fetch YES/NO token prices from the Polymarket CLOB.
  8. Evaluate signal: BUY_YES, BUY_NO, or SKIP.
  9. Risk gate: daily limits, existing position check.
  10. Enter paper or real position.
  11. Sleep LOOP_INTERVAL_SECONDS and repeat.

CLI usage:
  python bot.py --paper          # Paper mode (default), runs forever
  python bot.py --paper --once   # Single iteration then exit (debug)
  python bot.py --real           # Real mode (config.REAL_TRADING must be True)

Kill switch:
  Create STOP_BOT.txt in the project root to stop the bot gracefully.
"""

import argparse
import io
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import config
import data
import strategy
import execution
from polymarket_client import PolymarketClient
from risk import RiskManager
from reversal_window_mins import update_window_mins

# Resolve relative to project root (parent of scripts/)
_script_dir = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
KILL_SWITCH_FILE = os.path.join(_ROOT, "STOP_BOT.txt")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Configure console (UTF-8) and rotating file logging."""
    os.makedirs("logs", exist_ok=True)
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter  = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console: force UTF-8 to avoid cp1252 crashes on Windows.
    console = logging.StreamHandler(
        io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    )
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file: 5 MB per file, 3 backups.
    fh = logging.handlers.RotatingFileHandler(
        os.path.join("logs", "bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    root.addHandler(fh)


# ---------------------------------------------------------------------------
# KILL SWITCH
# ---------------------------------------------------------------------------

def kill_switch_active() -> bool:
    """Return True if STOP_BOT.txt exists in the project root."""
    return os.path.isfile(KILL_SWITCH_FILE)


# ---------------------------------------------------------------------------
# RESOLUTION POLLING
# ---------------------------------------------------------------------------

def _position_slug(position: dict) -> str:
    """Get slug for resolution lookup; derive from end_date_iso if not stored (legacy positions)."""
    slug = position.get("slug", "")
    if slug:
        return slug
    end_iso = position.get("end_date_iso", "")
    if not end_iso:
        return ""
    asset = position.get("asset", "BTC")
    prefix = (getattr(config, "ASSETS_CONFIG", None) or {}).get(asset, {}).get("slug_prefix", "btc-updown-5m-")
    try:
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        start_ts = int(end_dt.timestamp()) - 300
        start_ts = (start_ts // 300) * 300
        return f"{prefix}{start_ts}"
    except (ValueError, TypeError):
        return ""


def _wait_for_resolution(client: PolymarketClient, position: dict, risk: RiskManager) -> None:
    """
    Block until the open market window resolves, then record the outcome.

    Called when a position is open and we're past the entry window.
    Polls every LOOP_INTERVAL_SECONDS until closed=True in the Gamma API.
    """
    condition_id = position["condition_id"]
    slug = _position_slug(position)
    logger.info("Waiting for market to resolve: %s", position["question"])

    while not kill_switch_active():
        if client.is_market_closed(condition_id, slug=slug or None):
            logger.info("Market resolved. Fetching result...")
            result = client.get_market_result(condition_id, slug=slug or None)
            btc_spot = data.get_btc_spot_price() or 0.0

            if result is None:
                logger.warning("Could not determine market result. Retrying in 30s.")
                time.sleep(30)
                continue

            logger.info("Market result: %s (we bet %s)", result, position["direction"])

            # Record outcome in paper or real mode.
            if position.get("mode") == "REAL" and config.REAL_TRADING:
                closed = execution.real_record_outcome(position, result, btc_spot)
            else:
                closed = execution.paper_record_outcome(position, result, btc_spot)

            risk.record_trade_closed(
                pnl_usdc=closed.get("pnl_usdc", 0.0),
                mode=position.get("mode"),
            )
            _update_sl_streak(closed.get("outcome", "WIN"))
            return

        # Market not yet resolved - check again after sleep.
        logger.info("Market still open. Waiting %ds for resolution...", config.LOOP_INTERVAL_SECONDS)
        time.sleep(config.LOOP_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# SL STREAK TRACKER (module-level state, per asset)
# Time-based cooldown: after N consecutive SLs, block entries for
# REVERSAL_SL_COOLDOWN_SECS seconds (default 30 min), regardless of how
# many trades happen in that window. This correctly handles low-frequency
# assets (BTC/SOL/XRP ~1 trade/hr) the same as high-frequency ones (DOGE).
# ---------------------------------------------------------------------------
_SL_STATE: dict = {}  # asset_key -> {"streak": int, "cooldown_until": float}
# Throttle the post-end result polling to once per 30s per condition_id.
# Without throttling we'd hammer Gamma every 5s (one per bot loop).
_last_resolution_check: dict = {}  # condition_id -> unix timestamp of last get_market_result call


def _update_sl_streak(outcome: str) -> None:
    """Call after any full position close. Tracks consecutive SLs per asset."""
    asset_key   = getattr(config, "TRADING_ASSET", "BTC")
    sl_limit    = getattr(config, "REVERSAL_CONSECUTIVE_SL_LIMIT", 3)
    cooldown_s  = getattr(config, "REVERSAL_SL_COOLDOWN_SECS", 1800)  # 30 min default
    if asset_key not in _SL_STATE:
        _SL_STATE[asset_key] = {"streak": 0, "cooldown_until": 0.0}
    s = _SL_STATE[asset_key]
    if outcome == "SL":
        s["streak"] += 1
        logger.info("SL streak for %s: %d (limit %d)", asset_key, s["streak"], sl_limit)
        if sl_limit > 0 and s["streak"] >= sl_limit:
            s["cooldown_until"] = time.time() + cooldown_s
            s["streak"] = 0
            logger.warning(
                "Asset %s hit %d consecutive SLs — cooling down for %.0f min.",
                asset_key, sl_limit, cooldown_s / 60,
            )
    elif outcome not in ("", "CLEARED_STALE"):
        if s["streak"] > 0:
            logger.debug("SL streak for %s reset (outcome: %s)", asset_key, outcome)
        s["streak"] = 0


# ---------------------------------------------------------------------------
# ONE ITERATION
# ---------------------------------------------------------------------------

def run_one_iteration(client: PolymarketClient, risk: RiskManager) -> None:
    """
    Execute one complete iteration of the bot loop.

    This function is designed to be easy to test independently via --once flag.
    """
    # Step 1: Daily reset.
    risk.reset_if_new_day()
    risk.reload()

    # Step 1b: Cancel stale GTC orders once per minute (not every 5s tick).
    # Clears any orphaned orders without hammering the CLOB API.
    _last_cancel = getattr(run_one_iteration, "_last_cancel_ts", 0)
    if config.REAL_TRADING and (time.time() - _last_cancel) >= 60:
        client.cancel_all_orders()
        run_one_iteration._last_cancel_ts = time.time()

    # Step 2: Process all open positions (resolution, TP/SL, tiered TP, force-clear).
    # We process each and continue - do not block entry into new windows.
    for open_pos in list(risk.get_open_positions()):
        condition_id = open_pos.get("condition_id", "")
        end_iso = open_pos.get("end_date_iso", "")
        end_dt = None
        if end_iso:
            try:
                end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        now = datetime.now(timezone.utc)
        past_end = end_dt is not None and now >= end_dt
        secs_remaining_pos = (end_dt - now).total_seconds() if end_dt and not past_end else 999.0
        slug = _position_slug(open_pos)

        # Resolution check (once past end time).
        # CRITICAL: Do NOT gate on is_market_closed(). Gamma lags the closed flag by
        # minutes, but the outcome fields (outcomePrices/winners) are often available
        # sooner. Poll get_market_result() every 30s after end until result found.
        # Throttled to avoid hammering Gamma on every 5s bot loop.
        if past_end and condition_id:
            _now_ts = time.time()
            _last_check = _last_resolution_check.get(condition_id, 0)
            _poll_interval = getattr(config, "RESOLUTION_POLL_INTERVAL_SECS", 30)
            if _now_ts - _last_check >= _poll_interval:
                _last_resolution_check[condition_id] = _now_ts
                result = client.get_market_result(condition_id, slug=slug or None)
                if result:
                    btc_spot = data.get_btc_spot_price() or 0.0
                    logger.info(
                        "Market resolved (early poll): %s → %s",
                        condition_id[:16], result,
                    )
                    if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                        closed = execution.real_record_outcome(open_pos, result, btc_spot)
                    else:
                        closed = execution.paper_record_outcome(open_pos, result, btc_spot)
                    risk.record_trade_closed(
                        pnl_usdc=closed.get("pnl_usdc", 0.0),
                        condition_id=condition_id,
                        mode=open_pos.get("mode"),
                    )
                    _update_sl_streak(closed.get("outcome", "WIN"))
                    _last_resolution_check.pop(condition_id, None)
                    continue
        # Market still open: check take profit / stop loss
        token_id = (
            open_pos["yes_token_id"]
            if open_pos.get("direction") == "YES"
            else open_pos.get("no_token_id")
        )
        if token_id:
            bid = client.get_best_price(token_id, "SELL")
            # If bid >= 0.95 the market has already resolved to 1.0 — the CLOB is
            # showing the settled value. Don't try to FOK sell; instead fall through
            # to the resolution-check path below which will redeem the winning position.
            if bid is not None and bid >= 0.95:
                logger.info(
                    "Bid=%.3f >= 0.95 for %s — market resolved, skipping FOK sell, "
                    "falling through to resolution check.",
                    bid, condition_id[:16],
                )
                bid = None  # forces skip of all TP/SL/exit logic below
            if bid is not None:
                btc_spot = data.get_btc_spot_price() or 0.0
                entry_p = open_pos.get("entry_price") or 0.0
                tier = (open_pos.get("strategy_tier") or "").strip()
                is_reversal = tier == "reversal"
                if is_reversal and getattr(config, "LOG_REVERSAL_BID_TRACE", True):
                    slices = open_pos.get("slices")
                    tiers_str = ",".join(str(round(s.get("entry_price", s.get("entry_threshold", 0)), 2)) for s in slices) if slices else str(open_pos.get("entry_threshold", entry_p))
                    execution.log_reversal_bid_sample(
                        condition_id, bid, entry_p,
                        open_pos.get("direction", ""),
                        open_pos.get("num_tokens", 0),
                        open_pos.get("size_usdc", 0),
                        entry_mode=open_pos.get("entry_mode", "single"),
                        entry_tiers=tiers_str,
                    )
                hold_resolution = (
                    entry_p <= getattr(config, "CHEAP_ENTRY_HOLD_TO_RESOLUTION_THRESHOLD", 0.10)
                    and not is_reversal
                )
                no_sl_cheap = entry_p < getattr(
                    config, "CHEAP_ENTRY_NO_SL_THRESHOLD", 0.25
                )
                # HOLD_MIN_SECS: don't let stuck-bid or SL fire until position has been held long enough.
                hold_min_secs = getattr(config, "REVERSAL_HOLD_MIN_SECS", 0) if is_reversal else 0
                secs_held = 999.0
                if hold_min_secs > 0:
                    entry_time_iso = open_pos.get("entry_time", "")
                    if entry_time_iso:
                        try:
                            entry_dt = datetime.fromisoformat(
                                entry_time_iso.replace("Z", "+00:00")
                            )
                            if entry_dt.tzinfo is None:
                                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                            secs_held = (now - entry_dt).total_seconds()
                        except (ValueError, TypeError):
                            secs_held = 999.0
                in_hold_period = hold_min_secs > 0 and secs_held < hold_min_secs
                # Max position duration: force exit at bid when we've been in the window long enough (Oracle-bot style).
                window_secs = 300
                max_hold = getattr(config, "REVERSAL_MAX_POSITION_DURATION_SECS", 0)
                if (
                    is_reversal
                    and not past_end
                    and max_hold > 0
                    and secs_remaining_pos <= max(0, window_secs - max_hold)
                    and round(open_pos.get("num_tokens", 0), 4) > 0
                ):
                    reason = "TP" if bid >= entry_p else "SL"
                    if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                        closed = execution.real_close_early(open_pos, bid, client, btc_spot, reason)
                    else:
                        closed = execution.paper_close_early(open_pos, bid, btc_spot, reason)
                    if closed is not None:
                        if closed.get("partial"):
                            risk.update_open_position(closed["updated_position"])
                            risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                        else:
                            risk.record_trade_closed(
                                pnl_usdc=closed.get("pnl_usdc", 0.0),
                                condition_id=condition_id,
                                mode=open_pos.get("mode"),
                            )
                            _update_sl_streak(reason)
                        logger.info(
                            "Max position duration (%.0fs): exited at bid %.3f -> %s",
                            max_hold, bid, closed.get("pnl_usdc", 0),
                        )
                    continue
                # Last N seconds: exit at current bid (TP or SL) so we don't hold to resolution.
                exit_last_secs = getattr(config, "REVERSAL_EXIT_AT_MARKET_LAST_SECS", 30)
                if (
                    is_reversal
                    and not past_end
                    and secs_remaining_pos <= exit_last_secs
                    and round(open_pos.get("num_tokens", 0), 4) > 0
                ):
                    reason = "TP" if bid >= entry_p else "SL"
                    if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                        closed = execution.real_close_early(open_pos, bid, client, btc_spot, reason)
                    else:
                        closed = execution.paper_close_early(open_pos, bid, btc_spot, reason)
                    if closed is not None:
                        if closed.get("partial"):
                            risk.update_open_position(closed["updated_position"])
                            risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                        else:
                            risk.record_trade_closed(
                                pnl_usdc=closed.get("pnl_usdc", 0.0),
                                condition_id=condition_id,
                                mode=open_pos.get("mode"),
                            )
                            _update_sl_streak(reason)
                        logger.info(
                            "Exited at market (last %.0fs): %s @ %.3f -> %s",
                            secs_remaining_pos, reason, bid, closed.get("pnl_usdc", 0),
                        )
                    continue
                # Lock 50% or 100% profit mode: close entire position as soon as bid hits threshold.
                exit_mode = getattr(config, "REVERSAL_EXIT_MODE", "tiered").strip().lower()
                if is_reversal and round(open_pos.get("num_tokens", 0), 4) > 0:
                    if exit_mode == "lock_50" and bid >= entry_p * 1.5:
                        if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                            closed = execution.real_close_early(open_pos, bid, client, btc_spot, "TP_LOCK_50")
                        else:
                            closed = execution.paper_close_early(open_pos, bid, btc_spot, "TP_LOCK_50")
                        if closed is not None:
                            if closed.get("partial"):
                                risk.update_open_position(closed["updated_position"])
                                risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                            else:
                                risk.record_trade_closed(
                                    pnl_usdc=closed.get("pnl_usdc", 0.0),
                                    condition_id=condition_id,
                                    mode=open_pos.get("mode"),
                                )
                            logger.info("Lock 50%%: closed @ %.3f (entry %.3f)", bid, entry_p)
                        continue
                    if exit_mode == "lock_100" and bid >= entry_p * 2.0:
                        if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                            closed = execution.real_close_early(open_pos, bid, client, btc_spot, "TP_LOCK_100")
                        else:
                            closed = execution.paper_close_early(open_pos, bid, btc_spot, "TP_LOCK_100")
                        if closed is not None:
                            if closed.get("partial"):
                                risk.update_open_position(closed["updated_position"])
                                risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                            else:
                                risk.record_trade_closed(
                                    pnl_usdc=closed.get("pnl_usdc", 0.0),
                                    condition_id=condition_id,
                                    mode=open_pos.get("mode"),
                                )
                            logger.info("Lock 100%%: closed @ %.3f (entry %.3f)", bid, entry_p)
                        continue
                slices = open_pos.get("slices")
                tiered_enabled = is_reversal and getattr(config, "REVERSAL_TIERED_TP_ENABLED", False)
                closed_this_pos = False
                tiers = []  # ensure defined before single TP check
                if tiered_enabled and getattr(config, "TAKE_PROFIT_ENABLED", False):
                    # For pyramid: iterate slices; for single: one virtual slice
                    items = (
                        [(i, s) for i, s in enumerate(slices)]
                        if slices
                        else [(None, {"entry_price": entry_p, "entry_threshold": open_pos.get("entry_threshold"), "reversal_tiers_hit": open_pos.get("reversal_tiers_hit") or []})]
                    )
                    did_partial = False
                    for slice_idx, slc in items:
                        entry_thr = slc.get("entry_threshold")
                        if entry_thr is None:
                            ep = slc.get("entry_price", entry_p)
                            entry_thr = 0.05 if ep <= 0.10 else (0.10 if ep <= 0.125 else (0.15 if ep <= 0.175 else (0.18 if ep <= 0.195 else 0.20)))
                        # Per-asset TP tiers take priority; fall back to global REVERSAL_TP_BY_ENTRY.
                        _asset_key = getattr(config, "TRADING_ASSET", "BTC")
                        _asset_cfg_tp = ((getattr(config, "ASSETS_CONFIG", None) or {}).get(_asset_key) or {})
                        _per_asset_tp = _asset_cfg_tp.get("reversal_tp_tiers") or {}
                        tp_by_entry = _per_asset_tp if _per_asset_tp else (getattr(config, "REVERSAL_TP_BY_ENTRY", None) or {})
                        tiers = (tp_by_entry.get(entry_thr) if entry_thr is not None else None) or getattr(config, "REVERSAL_TP_TIERS", None) or []
                        if not tiers:
                            continue
                        tiers_hit = slc.get("reversal_tiers_hit") or []
                        for tier_price, pct in tiers:
                            if bid >= tier_price and tier_price not in tiers_hit:
                                if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                                    res = execution.real_close_partial(
                                        open_pos, bid, tier_price, pct, client, btc_spot,
                                        slice_idx=slice_idx,
                                    )
                                else:
                                    res = execution.paper_close_partial(
                                        open_pos, bid, tier_price, pct, btc_spot,
                                        slice_idx=slice_idx,
                                    )
                                if res is not None:
                                    updated_pos, pnl = res
                                    risk.record_partial_pnl(pnl, mode=open_pos.get("mode"))
                                    risk.update_open_position(updated_pos)
                                    open_pos = updated_pos
                                    did_partial = True
                                    if round(open_pos.get("num_tokens", 0), 4) <= 0:
                                        risk.record_trade_closed(
                                            pnl_usdc=0.0,
                                            condition_id=condition_id,
                                            mode=open_pos.get("mode"),
                                        )
                                        closed_this_pos = True
                                    break
                        if closed_this_pos or did_partial:
                            break
                    if not closed_this_pos and round(open_pos.get("num_tokens", 0), 4) <= 0:
                        risk.record_trade_closed(
                            pnl_usdc=0.0,
                            condition_id=condition_id,
                            mode=open_pos.get("mode"),
                        )
                        closed_this_pos = True
                    if closed_this_pos:
                        continue
                    # Stuck-bid fallback: if bid above min but below next TP tier for too long, exit at bid
                    stuck_enabled = getattr(config, "REVERSAL_STUCK_FALLBACK_ENABLED", False)
                    min_bid = getattr(config, "REVERSAL_STUCK_FALLBACK_MIN_BID", 0.20)
                    timeout_secs = getattr(config, "REVERSAL_STUCK_BID_TIMEOUT_SECS", 60)
                    if (
                        stuck_enabled
                        and not closed_this_pos
                        and not in_hold_period  # don't exit via stuck-bid during hold period
                        and round(open_pos.get("num_tokens", 0), 4) > 0
                    ):
                        all_tiers_set = set()
                        all_hit = set()
                        for _idx, slc in items:
                            ep = slc.get("entry_price", entry_p)
                            thr = slc.get("entry_threshold") or (0.05 if ep <= 0.10 else (0.10 if ep <= 0.125 else (0.15 if ep <= 0.175 else (0.18 if ep <= 0.195 else 0.20))))
                            tp_by = getattr(config, "REVERSAL_TP_BY_ENTRY", None) or {}
                            ti = (tp_by.get(thr) if thr else None) or getattr(config, "REVERSAL_TP_TIERS", None) or []
                            for t, _ in ti:
                                all_tiers_set.add(t)
                            all_hit.update(slc.get("reversal_tiers_hit") or [])
                        next_tier = min((t for t in all_tiers_set if t not in all_hit), default=None)
                        if next_tier is not None:
                            stuck = min_bid <= bid < next_tier
                            now_ts = time.time()
                            if stuck:
                                if "stuck_bid_since" not in open_pos:
                                    open_pos["stuck_bid_since"] = now_ts
                                    risk.update_open_position(open_pos)
                                elapsed = now_ts - open_pos.get("stuck_bid_since", now_ts)
                                if elapsed >= timeout_secs:
                                    reason = "TP_STUCK_BID"
                                    if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                                        closed = execution.real_close_early(open_pos, bid, client, btc_spot, reason)
                                    else:
                                        closed = execution.paper_close_early(open_pos, bid, btc_spot, reason)
                                    if closed is not None:
                                        if closed.get("partial"):
                                            risk.update_open_position(closed["updated_position"])
                                            risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                                        else:
                                            risk.record_trade_closed(
                                                pnl_usdc=closed.get("pnl_usdc", 0.0),
                                                condition_id=condition_id,
                                                mode=open_pos.get("mode"),
                                            )
                                    continue
                            else:
                                if "stuck_bid_since" in open_pos:
                                    open_pos.pop("stuck_bid_since", None)
                                    risk.update_open_position(open_pos)
                # Single TP (non-tiered or non-reversal)
                tp_price = (
                    getattr(config, "REVERSAL_TAKE_PROFIT_PRICE", 0.50)
                    if is_reversal
                    else getattr(config, "TAKE_PROFIT_PRICE", 0.85)
                )
                if not hold_resolution and getattr(config, "TAKE_PROFIT_ENABLED", False) and bid >= tp_price:
                    if not (tiered_enabled and tiers):
                        if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                            closed = execution.real_close_early(
                                open_pos, bid, client, btc_spot, "TP"
                            )
                        else:
                            closed = execution.paper_close_early(
                                open_pos, bid, btc_spot, "TP"
                            )
                        if closed is not None:
                            if closed.get("partial"):
                                risk.update_open_position(closed["updated_position"])
                                risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                            else:
                                risk.record_trade_closed(
                                    pnl_usdc=closed.get("pnl_usdc", 0.0),
                                    condition_id=condition_id,
                                    mode=open_pos.get("mode"),
                                )
                        continue
                if (
                    not no_sl_cheap
                    and not in_hold_period  # don't SL during hold period; let reversal develop
                    and getattr(config, "STOP_LOSS_ENABLED", False)
                    and bid <= getattr(config, "STOP_LOSS_PRICE", 0.25)
                ):
                    if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                        closed = execution.real_close_early(
                            open_pos, bid, client, btc_spot, "SL"
                        )
                    else:
                        closed = execution.paper_close_early(
                            open_pos, bid, btc_spot, "SL"
                        )
                    if closed is not None:
                        if closed.get("partial"):
                            risk.update_open_position(closed["updated_position"])
                            risk.record_partial_pnl(closed["pnl_usdc"], mode=open_pos.get("mode"))
                        else:
                            risk.record_trade_closed(
                                pnl_usdc=closed.get("pnl_usdc", 0.0),
                                condition_id=condition_id,
                                mode=open_pos.get("mode"),
                            )
                            _update_sl_streak("SL")
                    continue
        # Force-clear if past end and stale.
        # CRITICAL: Do NOT gate resolution on is_market_closed(). Gamma API frequently
        # lags updating the closed flag, so is_market_closed() can return False even
        # 90+ seconds after the market actually ended. Previously this caused the bot to
        # skip resolution entirely and force-clear every trade as CLEARED_STALE.
        # Now: once we're past end_date by stale_secs, attempt result lookup unconditionally.
        # Only force-clear if ALL quick retries fail (true unresolvable outcome).
        secs_past_end = (now - end_dt).total_seconds() if end_dt else 0
        stale_secs = getattr(config, "CLEAR_STALE_POSITION_AFTER_SECS", 120)
        if past_end and secs_past_end >= stale_secs:
            result = None
            quick_retries = getattr(config, "RESOLUTION_QUICK_RETRY_COUNT", 3)
            quick_delay = getattr(config, "RESOLUTION_QUICK_RETRY_DELAY_SECS", 5)
            for attempt in range(quick_retries):
                result = client.get_market_result(condition_id, slug=slug or None)
                if result:
                    break
                if attempt < quick_retries - 1:
                    logger.info(
                        "Stale-force resolution attempt %d/%d (no is_market_closed gate). "
                        "Retrying in %ds...",
                        attempt + 1, quick_retries, quick_delay,
                    )
                    time.sleep(quick_delay)
            btc_spot = data.get_btc_spot_price() or 0.0
            if result:
                logger.info("Stale position resolved: %s → %s", condition_id[:16], result)
                if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                    closed = execution.real_record_outcome(open_pos, result, btc_spot)
                else:
                    closed = execution.paper_record_outcome(open_pos, result, btc_spot)
                risk.record_trade_closed(
                    pnl_usdc=closed.get("pnl_usdc", 0.0),
                    condition_id=condition_id,
                    mode=open_pos.get("mode"),
                )
                _update_sl_streak(closed.get("outcome", "WIN"))
                continue
            # Genuine failure: result unavailable after all retries — force-clear
            logger.warning(
                "Could not resolve %s after %d attempts. Force-clearing.",
                condition_id[:16], quick_retries,
            )
            _last_resolution_check.pop(condition_id, None)
            if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                cleared = execution.real_force_clear_stale(open_pos)
            else:
                cleared = execution.paper_force_clear_stale(open_pos)
            risk.record_trade_closed(
                pnl_usdc=cleared.get("pnl_usdc", 0.0),
                condition_id=condition_id,
                mode=open_pos.get("mode"),
            )
            _update_sl_streak("CLEARED_STALE")
            continue
        logger.info(
            "Open position: %s (resolves %s). Monitoring...",
            open_pos.get("question", condition_id)[:50], end_iso or "?",
        )

    # Step 3: Find the active 5-minute market window.
    market = client.find_active_btc_market()
    if market is None:
        logger.info("No active BTC 5-min market found. Waiting...")
        return

    # Step 4: Entry timing - evaluate throughout the full window (0-300s).
    now     = datetime.now(timezone.utc)
    end_dt  = market["end_date"]
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    secs_remaining = (end_dt - now).total_seconds()
    window_secs    = 300  # 5 minutes
    secs_elapsed   = window_secs - secs_remaining

    if secs_remaining <= 0:
        logger.info("Market window already closed. Skipping.")
        return

    late_enabled = getattr(config, "LATE_ENTRY_ENABLED", False)
    late_secs    = getattr(config, "LATE_WINDOW_SECS", 90)
    in_late      = late_enabled and secs_remaining <= late_secs

    logger.info(
        "Active market: %s | %.0fs remaining | %.0fs elapsed%s",
        market["question"], secs_remaining, secs_elapsed,
        " | LATE" if in_late else "",
    )

    # Step 5: Fetch BTC candles and compute indicators.
    df = data.fetch_candles()
    if df.empty:
        logger.warning("No candle data available. Skipping.")
        return
    df = data.compute_indicators(df)
    indicators = data.get_latest_indicators(df)
    if indicators is None:
        logger.warning("Not enough data for indicators. Skipping.")
        return

    btc_spot = data.get_btc_spot_price() or indicators.get("close", 0.0)
    risk.update_btc_spot(btc_spot)
    asset = getattr(config, "TRADING_ASSET", "BTC")
    logger.info("%s spot: $%.2f", asset, btc_spot)

    # Step 6: Fetch YES and NO token prices from the CLOB.
    yes_price = client.get_mid_price(market["yes_token_id"])
    no_price  = client.get_mid_price(market["no_token_id"])

    if yes_price is None or no_price is None:
        logger.warning("Could not fetch YES/NO prices. Skipping.")
        return

    logger.info("YES price: %.3f | NO price: %.3f", yes_price, no_price)

    cid = market.get("condition_id", "")
    # Per-second price path logging (paper run with LOG_PRICE_PATH=True, loop interval 1s).
    if getattr(config, "LOG_PRICE_PATH", False):
        issues_list = []
        if abs(yes_price + no_price - 1.0) > 0.05:
            issues_list.append("sum_off")
        execution.log_price_path(
            asset=getattr(config, "TRADING_ASSET", "BTC"),
            condition_id=cid,
            question=(market.get("question") or "")[:200],
            yes_price=yes_price,
            no_price=no_price,
            secs_remaining=secs_remaining,
            issues=";".join(issues_list) if issues_list else "",
        )
    # Step 7: Evaluate signal.
    window_start_ts = end_dt - timedelta(seconds=300)
    window_start_btc = data.get_btc_price_at_time(df, window_start_ts)
    context = {
        "window_start_btc": window_start_btc,
        "secs_remaining": secs_remaining,
        "current_btc": btc_spot,
    }
    if getattr(config, "REVERSAL_USE_DYNAMIC_LOWEST", False):
        dynamic_tier = update_window_mins(asset, cid, yes_price, no_price)
        if dynamic_tier is not None:
            context["dynamic_lowest_tier"] = dynamic_tier
    if in_late:
        context["in_late_window"] = True
    # Pyramid add-slice: pass filled tiers from existing position in this market
    existing_pos = risk.get_open_position(cid)
    if existing_pos and (existing_pos.get("entry_mode") or "").strip() == "pyramid":
        slices = existing_pos.get("slices") or []
        context["filled_entry_tiers"] = [
            s.get("entry_threshold") or s.get("entry_price")
            for s in slices
            if s.get("entry_threshold") is not None or s.get("entry_price") is not None
        ]
        context["existing_position"] = existing_pos

        # Pyramid slice gap: don't add second tranche within MIN_SLICE_GAP_SECS of first entry.
        # Prevents instant double-entry when market is already at the low tier on first observation.
        min_slice_gap = getattr(config, "REVERSAL_MIN_SLICE_GAP_SECS", 45)
        if slices and min_slice_gap > 0:
            last_slice_time = max(
                (s.get("open_timestamp_unix") or 0) for s in slices
            )
            if last_slice_time and (time.time() - last_slice_time) < min_slice_gap:
                context["block_add_slice"] = True

    # Sustained-dip filter: price must stay at/below entry threshold for MIN seconds
    # but no longer than MAX seconds (beyond MAX it's resolving, not reversing).
    min_dip_secs = getattr(config, "REVERSAL_MIN_DIP_SECS", 20)
    max_dip_secs = getattr(config, "REVERSAL_MAX_DIP_SECS", 90)
    if not existing_pos:
        _dip_tracker  = getattr(run_one_iteration, "_dip_tracker", {})
        entry_thr_cfg = getattr(config, "REVERSAL_PRICE_THRESHOLD", 0.15)
        cheap_price   = min(yes_price, no_price)
        if cheap_price <= entry_thr_cfg:
            if cid not in _dip_tracker:
                _dip_tracker[cid] = time.time()
            dip_age = time.time() - _dip_tracker[cid]
            if min_dip_secs > 0 and dip_age < min_dip_secs:
                context["dip_too_fresh"] = True
                logger.debug("Dip %.0fs old (need %ds min). Waiting...", dip_age, min_dip_secs)
            elif max_dip_secs > 0 and dip_age > max_dip_secs:
                context["dip_too_stale"] = True
                logger.info(
                    "Dip at %.3f has been low for %.0fs > %ds max — market resolving, skip.",
                    cheap_price, dip_age, max_dip_secs,
                )
        else:
            _dip_tracker.pop(cid, None)
        run_one_iteration._dip_tracker = _dip_tracker

    # Consecutive SL cooldown: after N SLs in a row on this asset, pause for K seconds.
    _asset_key = getattr(config, "TRADING_ASSET", "BTC")
    _cooldown_until = _SL_STATE.get(_asset_key, {}).get("cooldown_until", 0.0)
    if not existing_pos and time.time() < _cooldown_until:
        mins_left = (_cooldown_until - time.time()) / 60
        context["sl_cooldown"] = True
        logger.info("Asset %s SL cooldown: %.0f min remaining. Skipping.", _asset_key, mins_left)

    # Trading hours filter: per-asset whitelist of UTC hours.
    # Empty list = trade all hours (e.g. DOGE). Non-empty = only those hours.
    if not existing_pos:
        import datetime as _dt
        _asset_cfg_cur = (config.ASSETS_CONFIG.get(_asset_key) or {})
        _allowed_hours = _asset_cfg_cur.get("trading_hours_utc", [])
        if _allowed_hours:
            _utc_hour = _dt.datetime.utcnow().hour
            if _utc_hour not in _allowed_hours:
                context["outside_trading_hours"] = True
                logger.debug(
                    "Asset %s: UTC hour %d not in allowed hours %s — skipping.",
                    _asset_key, _utc_hour, _allowed_hours,
                )

    action, debug_info = strategy.check_signal(indicators, yes_price, no_price, context)
    risk.update_last_signal(debug_info)

    if action == strategy.SKIP:
        logger.info("Signal SKIP (%s). No bet this window.", debug_info.get("reason", ""))
        execution.log_signal_evaluated(cid, action, debug_info, yes_price, no_price, context, traded=False)
        return

    # Hard gate: never enter in the last 60s (no matter what strategy said).
    min_secs_entry = getattr(config, "ENTRY_MIN_SECS_REMAINING", 60)
    if secs_remaining < min_secs_entry:
        logger.info("Entry BLOCKED: %.0fs left (min %ds). No buying in last 60s.", secs_remaining, min_secs_entry)
        execution.log_signal_evaluated(cid, strategy.SKIP, debug_info, yes_price, no_price, context, traded=False, risk_block_reason="last 60s no entry")
        return
    # Only allow entry within the first N seconds of the window (e.g. 120s for reversals).
    max_elapsed = getattr(config, "ENTRY_MAX_ELAPSED_SECS", 0)
    if max_elapsed > 0 and secs_elapsed > max_elapsed:
        logger.info("Entry BLOCKED: %.0fs elapsed (max %ds). Buys only in first %ds.", secs_elapsed, max_elapsed, max_elapsed)
        execution.log_signal_evaluated(cid, strategy.SKIP, debug_info, yes_price, no_price, context, traded=False, risk_block_reason="past first %ds" % max_elapsed)
        return

    is_add_slice = bool(debug_info.get("is_add_slice"))
    # Step 8: Risk gate.
    allowed, reason = risk.can_trade(market=market, is_add_slice=is_add_slice)
    if not allowed:
        logger.info("Risk gate BLOCKED: %s", reason)
        execution.log_signal_evaluated(cid, action, debug_info, yes_price, no_price, context, traded=False, risk_block_reason=reason)
        return

    # Step 9: Enter position.
    direction   = "YES" if action == strategy.BUY_YES else "NO"
    entry_price = yes_price if direction == "YES" else no_price

    # Optional profitability-first EV gate using model-implied probability.
    if getattr(config, "MODEL_EV_GATE_ENABLED", False):
        try:
            from btc_5m_fair_value import model_implied_p_up
            p_yes = model_implied_p_up(
                context.get("window_start_btc"),
                btc_spot,
                secs_remaining,
                indicators.get("atr_pct", 0.0),
                ema_fast=indicators.get("ema_fast"),
                ema_slow=indicators.get("ema_slow"),
                ibs=indicators.get("ibs"),
            )
            model_prob = p_yes if direction == "YES" else (1.0 - p_yes)
            cost_buffer = getattr(config, "MODEL_EV_COST_BUFFER", 0.015)
            min_edge = getattr(config, "MODEL_EV_MIN_EDGE", 0.03)
            net_edge = model_prob - entry_price - cost_buffer
            if net_edge < min_edge:
                reason = (
                    f"model EV gate: net_edge={net_edge:.3f} < min_edge={min_edge:.3f} "
                    f"(model={model_prob:.3f}, entry={entry_price:.3f}, cost={cost_buffer:.3f})"
                )
                logger.info("Signal SKIP (%s).", reason)
                execution.log_signal_evaluated(
                    cid, action, debug_info, yes_price, no_price, context,
                    traded=False, risk_block_reason=reason,
                )
                return
        except Exception as exc:
            logger.debug("Model EV gate skipped due to error: %s", exc)

    strategy_tier = debug_info.get("tier", "")
    entry_threshold = debug_info.get("entry_threshold")
    entry_mode = debug_info.get("entry_mode", "single")
    if strategy_tier == "reversal" and getattr(config, "REVERSAL_BET_USDC", None) is not None:
        if entry_mode == "pyramid" and entry_threshold is not None:
            alloc = getattr(config, "REVERSAL_PYRAMID_ALLOCATION", {}) or {}
            frac = alloc.get(entry_threshold, 1.0 / 3.0)
            size_usdc = round(config.REVERSAL_BET_USDC * frac, 2)
        else:
            size_usdc = config.REVERSAL_BET_USDC
        # Cap reversal bet to RISK_PER_TRADE_USDC so it respects the same hard limit
        # as the risk manager. Without this cap, REVERSAL_BET_USDC=$5 with 3 concurrent
        # positions = 100% of a $15 bankroll at risk simultaneously.
        size_usdc = min(size_usdc, config.RISK_PER_TRADE_USDC)
    else:
        # ml_v2: use Kelly fraction from model if available, else fall back to fixed sizing
        ml_half_kelly = debug_info.get("half_kelly") if getattr(config, "ML_V2_KELLY_SIZING", False) else None
        size_usdc = risk.get_trade_size_usdc(half_kelly=ml_half_kelly)
    logger.info(
        "All checks passed! %s %s bet @ %.3f | size $%.2f | market: %s",
        "Adding slice" if is_add_slice else "Entering",
        direction, entry_price, size_usdc, market["question"],
    )
    if is_add_slice and existing_pos:
        if config.REAL_TRADING:
            position = execution.real_add_slice(
                existing_pos, market, client, direction, entry_price, btc_spot,
                size_usdc, entry_threshold or entry_price,
            )
        else:
            position = execution.paper_add_slice(
                existing_pos, market, direction, entry_price, btc_spot,
                size_usdc, entry_threshold or entry_price,
            )
        if position is not None:
            risk.update_open_position(position)
            execution.log_signal_evaluated(cid, action, debug_info, yes_price, no_price, context, traded=True)
        else:
            logger.error("Add slice returned None")
            execution.log_signal_evaluated(cid, action, debug_info, yes_price, no_price, context, traded=False)
        return
    if config.REAL_TRADING:
        position = execution.real_enter(
            client, market, direction, entry_price, btc_spot,
            size_usdc=size_usdc, strategy_tier=strategy_tier,
            signal_debug_info=debug_info, context=context,
        )
    else:
        position = execution.paper_enter(
            market, direction, entry_price, btc_spot,
            size_usdc=size_usdc, strategy_tier=strategy_tier,
            signal_debug_info=debug_info, context=context,
        )

    if position is not None:
        execution.log_signal_evaluated(cid, action, debug_info, yes_price, no_price, context, traded=True)
        risk.record_trade_opened(position)
        logger.info("Position recorded. Waiting for resolution at %s.", market["end_date_iso"])
    else:
        execution.log_signal_evaluated(cid, action, debug_info, yes_price, no_price, context, traded=False, risk_block_reason="entry returned None")
        logger.error("Entry returned None - position not recorded.")


# ---------------------------------------------------------------------------
# BOT LOOP
# ---------------------------------------------------------------------------

def run_bot_loop(
    override_paper: bool = False,
    override_real:  bool = False,
    run_once:       bool = False,
    interval:       int  = None,
) -> None:
    """
    The main trading loop. Runs indefinitely until stopped.

    Args:
        override_paper: Force REAL_TRADING=False regardless of config.
        override_real:  Enable real trading (config.REAL_TRADING must also be True).
        run_once:       Run a single iteration then exit.
        interval:       Loop sleep interval in seconds (default: config.LOOP_INTERVAL_SECONDS).
    """
    if override_paper:
        config.REAL_TRADING = False
        logger.info("Override: PAPER mode forced.")

    # Paper trades go to a separate file for feasibility assessment (unless PAPER_RUN_DIR set).
    if not config.REAL_TRADING and not getattr(config, "PAPER_RUN_DIR", ""):
        paper_csv = os.path.join(_ROOT, "logs", "paper_trades.csv")
        execution.set_trades_csv(paper_csv)
        logger.info("Paper trades will be logged to: %s", paper_csv)

    if override_real:
        if not config.REAL_TRADING:
            logger.error(
                "Cannot enable real trading: set REAL_TRADING=True in config.py first."
            )
            return
        logger.warning("REAL TRADING MODE ACTIVE. Real bets will be placed on Polymarket!")

    loop_interval = interval or config.LOOP_INTERVAL_SECONDS
    mode_label    = "REAL" if config.REAL_TRADING else "PAPER"

    logger.info("=" * 60)
    logger.info("Polymarket BTC 5-Min Direction Bot starting.")
    logger.info("Mode: %s | Interval: %ds | Once: %s", mode_label, loop_interval, run_once)
    logger.info("Kill switch: create %s to stop.", KILL_SWITCH_FILE)
    logger.info("=" * 60)

    client = PolymarketClient()
    risk   = RiskManager()
    risk.set_bot_running(True)

    # Startup: log USDC balance in real mode.
    if config.REAL_TRADING and client.has_credentials:
        usdc = client.get_usdc_balance()
        min_size = risk.get_trade_size_usdc()
        if usdc is not None:
            logger.info("USDC balance available for trading: $%.4f", usdc)
            if usdc < min_size:
                logger.warning(
                    "USDC balance ($%.4f) is below current trade size ($%.2f). "
                    "Top up your wallet before trades can fire.",
                    usdc, min_size,
                )
        else:
            logger.warning("Could not fetch USDC balance. Check credentials.")

    try:
        while True:
            if kill_switch_active():
                logger.info("%s detected. Exiting gracefully.", KILL_SWITCH_FILE)
                break

            try:
                run_one_iteration(client, risk)
            except Exception as exc:
                logger.exception("Unexpected error in bot iteration: %s", exc)

            if run_once:
                logger.info("--once flag set. Exiting.")
                break

            logger.debug("Sleeping %ds...", loop_interval)
            time.sleep(loop_interval)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt - shutting down.")

    finally:
        risk.set_bot_running(False)
        logger.info("Bot stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Polymarket BTC 5-Minute Direction Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bot.py                    # Paper mode, runs forever
  python bot.py --paper --once     # Single iteration, then exit (debug)
  python bot.py --real             # Real mode (set REAL_TRADING=True in config.py first)

Kill switch:
  Create STOP_BOT.txt in the project root to stop the bot gracefully.
        """,
    )
    parser.add_argument("--paper", action="store_true",
                        help="Force paper mode (no real orders)")
    parser.add_argument("--real",  action="store_true",
                        help="Enable real trading (config.py must have REAL_TRADING=True)")
    parser.add_argument("--once",  action="store_true",
                        help="Run one iteration and exit (for debugging)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Override loop interval in seconds")
    return parser.parse_args()


if __name__ == "__main__":
    setup_logging()
    args = parse_args()

    if args.paper and args.real:
        print("Error: --paper and --real cannot both be set.")
        sys.exit(1)

    run_bot_loop(
        override_paper=args.paper,
        override_real=args.real,
        run_once=args.once,
        interval=args.interval,
    )
