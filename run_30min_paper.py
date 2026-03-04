"""
Run a 30-minute paper test (hybrid strategy, relaxed filters).

- STRATEGY_MODE = "hybrid" (contrarian first, momentum fallback)
- ENTRY_WINDOW_SECS = 180 (3 min to enter)
- Trades log: logs/trades_30min_contrarian.csv
- Creates STOP_BOT.txt after 30 minutes.

Run: python run_30min_paper.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution
execution.set_trades_csv(os.path.join("logs", "trades_30min_contrarian.csv"))

import config
config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

THIRTY_MIN_SEC = 30 * 60  # 1800


def _reset_state() -> None:
    """Reset state.json for fresh 30min run."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_position"] = None
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 30min hybrid paper test.")


def _stop_after_30min() -> None:
    """Create STOP_BOT.txt after 30 minutes."""
    time.sleep(THIRTY_MIN_SEC)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("30 minutes elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)

    _reset_state()

    logger.info("=" * 60)
    logger.info("30-MIN PAPER TEST: hybrid strategy, 180s entry window")
    logger.info("Trades log: logs/trades_30min_contrarian.csv")
    logger.info("Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_30min, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
