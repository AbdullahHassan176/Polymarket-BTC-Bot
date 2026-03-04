"""
Run a 3-hour paper test with relaxed filters ($50, 5-min BTC markets).

- Entry window: 120s (relaxed from 90s)
- Price band: 0.30–0.70 (relaxed from 0.35–0.65)
- Trades log: logs/trades_3hr_50usd.csv
- Creates STOP_BOT.txt after 3 hours.

Run: python run_3hr_paper.py   (or: venv\Scripts\python run_3hr_paper.py)
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution
execution.set_trades_csv(os.path.join("logs", "trades_3hr_50usd.csv"))

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

THREE_HOURS_SEC = 3 * 3600  # 10800


def _reset_state() -> None:
    """Reset state.json for fresh 3hr run."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_position"] = None
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 3hr $50 paper test (relaxed filters).")


def _stop_after_3h() -> None:
    """Create STOP_BOT.txt after 3 hours."""
    time.sleep(THREE_HOURS_SEC)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("3 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)

    _reset_state()

    logger.info("=" * 60)
    logger.info("3-HOUR PAPER TEST: $50, relaxed filters (entry 120s, price 0.30-0.70)")
    logger.info("Trades log: logs/trades_3hr_50usd.csv")
    logger.info("Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_3h, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
