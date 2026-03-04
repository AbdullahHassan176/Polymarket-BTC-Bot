"""
bot.py  -  Main trading loop for the Polymarket BTC 5-Minute Direction Bot.

How it works each iteration:
  1. Kill switch check (STOP_BOT.txt).
  2. Daily counter reset at UTC midnight.
  3. Find the currently active BTC 5-minute market on Polymarket.
  4. Check if we already have an open position:
       - If yes: poll until it resolves, then record outcome and continue.
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

KILL_SWITCH_FILE = "STOP_BOT.txt"

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
    try:
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        start_ts = int(end_dt.timestamp()) - 300
        start_ts = (start_ts // 300) * 300
        return f"btc-updown-5m-{start_ts}"
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

            risk.record_trade_closed(pnl_usdc=closed.get("pnl_usdc", 0.0))
            return

        # Market not yet resolved - check again after sleep.
        logger.info("Market still open. Waiting %ds for resolution...", config.LOOP_INTERVAL_SECONDS)
        time.sleep(config.LOOP_INTERVAL_SECONDS)


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

    # Step 2: Check for existing open position - may need to wait for resolution.
    open_pos = risk.get_open_position()
    if open_pos is not None:
        # Only consider resolution once we're past the scheduled end time (prevents
        # false "closed" from API causing multiple entries in same window).
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

        condition_id = open_pos.get("condition_id", "")
        slug = _position_slug(open_pos)
        if (
            past_end
            and condition_id
            and client.is_market_closed(condition_id, slug=slug or None)
        ):
            result   = client.get_market_result(condition_id, slug=slug or None)
            btc_spot = data.get_btc_spot_price() or 0.0
            if result:
                if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                    closed = execution.real_record_outcome(open_pos, result, btc_spot)
                else:
                    closed = execution.paper_record_outcome(open_pos, result, btc_spot)
                risk.record_trade_closed(pnl_usdc=closed.get("pnl_usdc", 0.0))
                return
        # Market still open: check take profit / stop loss.
        token_id = (
            open_pos["yes_token_id"]
            if open_pos.get("direction") == "YES"
            else open_pos.get("no_token_id")
        )
        if token_id:
            bid = client.get_best_price(token_id, "SELL")
            if bid is not None:
                btc_spot = data.get_btc_spot_price() or 0.0
                if getattr(config, "TAKE_PROFIT_ENABLED", False) and bid >= getattr(
                    config, "TAKE_PROFIT_PRICE", 0.85
                ):
                    if open_pos.get("mode") == "REAL" and config.REAL_TRADING:
                        closed = execution.real_close_early(
                            open_pos, bid, client, btc_spot, "TP"
                        )
                    else:
                        closed = execution.paper_close_early(
                            open_pos, bid, btc_spot, "TP"
                        )
                    if closed is not None:
                        risk.record_trade_closed(
                            pnl_usdc=closed.get("pnl_usdc", 0.0)
                        )
                    return
                if getattr(config, "STOP_LOSS_ENABLED", False) and bid <= getattr(
                    config, "STOP_LOSS_PRICE", 0.25
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
                        risk.record_trade_closed(
                            pnl_usdc=closed.get("pnl_usdc", 0.0)
                        )
                    return
        logger.info("Open position exists (resolves %s). Monitoring...", end_iso or "?")
        return

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
    logger.info("BTC spot: $%.2f", btc_spot)

    # Step 6: Fetch YES and NO token prices from the CLOB.
    yes_price = client.get_mid_price(market["yes_token_id"])
    no_price  = client.get_mid_price(market["no_token_id"])

    if yes_price is None or no_price is None:
        logger.warning("Could not fetch YES/NO prices. Skipping.")
        return

    logger.info("YES price: %.3f | NO price: %.3f", yes_price, no_price)

    # Step 7: Evaluate signal.
    context = {}
    if in_late:
        window_start_ts = end_dt - timedelta(seconds=300)
        window_start_btc = data.get_btc_price_at_time(df, window_start_ts)
        context = {
            "in_late_window": True,
            "window_start_btc": window_start_btc,
            "current_btc": btc_spot,
        }
    action, debug_info = strategy.check_signal(indicators, yes_price, no_price, context)
    risk.update_last_signal(debug_info)

    if action == strategy.SKIP:
        logger.info("Signal SKIP (%s). No bet this window.", debug_info.get("reason", ""))
        return

    # Step 8: Risk gate.
    allowed, reason = risk.can_trade()
    if not allowed:
        logger.info("Risk gate BLOCKED: %s", reason)
        return

    # Step 9: Enter position.
    direction   = "YES" if action == strategy.BUY_YES else "NO"
    entry_price = yes_price if direction == "YES" else no_price

    size_usdc = risk.get_trade_size_usdc()
    logger.info(
        "All checks passed! Entering %s bet @ %.3f | size $%.2f | market: %s",
        direction, entry_price, size_usdc, market["question"],
    )

    if config.REAL_TRADING:
        position = execution.real_enter(
            client, market, direction, entry_price, btc_spot, size_usdc=size_usdc
        )
    else:
        position = execution.paper_enter(
            market, direction, entry_price, btc_spot, size_usdc=size_usdc
        )

    if position is not None:
        risk.record_trade_opened(position)
        logger.info("Position recorded. Waiting for resolution at %s.", market["end_date_iso"])
    else:
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
