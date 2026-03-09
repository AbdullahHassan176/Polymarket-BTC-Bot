"""
Run a 12-hour paper test (hybrid strategy, relaxed filters).

- STRATEGY_MODE = "hybrid" (contrarian first, momentum fallback)
- ENTRY_WINDOW_SECS = 180 (3 min to enter)
- Trades log: logs/trades_12hr_hybrid.csv
- Creates STOP_BOT.txt after 12 hours.

Run: python run_12hr_paper.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution
execution.set_trades_csv(os.path.join("logs", "trades_12hr_hybrid.csv"))

import config
config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

TWELVE_HR_SEC = 12 * 60 * 60  # 43200


def _reset_state() -> None:
    """Reset state.json for fresh 12hr run."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_position"] = None
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    state["starting_balance_usdc"] = getattr(config, "BANKROLL_START_USDC", 50.0)
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 12hr hybrid paper test.")


def _stop_after_12hr() -> None:
    """Create STOP_BOT.txt after 12 hours."""
    time.sleep(TWELVE_HR_SEC)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("12 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)

    _reset_state()

    logger.info("=" * 60)
    logger.info("12-HOUR PAPER TEST: hybrid strategy, 180s entry window")
    logger.info("Trades log: logs/trades_12hr_hybrid.csv")
    logger.info("Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_12hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
