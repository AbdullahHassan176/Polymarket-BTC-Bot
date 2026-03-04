"""
Run a 2-hour paper test starting from $50 on the 5-min BTC markets.

- Uses logs/trades_2hr_50usd.csv for trade log (clean for analysis).
- Creates STOP_BOT.txt after 2 hours to exit gracefully.
- Resets state for a fresh start.

Run: python run_2hr_paper.py
"""

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

# Set trades CSV for this run BEFORE bot imports execution
import execution
execution.set_trades_csv(os.path.join("logs", "trades_2hr_50usd.csv"))

import config
from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)


def _reset_state() -> None:
    """Reset state.json for fresh 2hr run."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_position"] = None
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 2hr $50 paper test.")


def _stop_after_2h() -> None:
    """Create STOP_BOT.txt after 2 hours."""
    time.sleep(7200)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("2 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)

    _reset_state()

    logger.info("=" * 60)
    logger.info("2-HOUR PAPER TEST: $50 starting capital, 5-min BTC markets")
    logger.info("Trades log: logs/trades_2hr_50usd.csv")
    logger.info("Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_2h, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
