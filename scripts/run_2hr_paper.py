"""
Run a 2-hour paper test (hybrid strategy + improvements from logs).

- STRATEGY_MODE = "hybrid"
- ENTRY_WINDOW_SECS = 180
- Uses: CONTRARIAN_MAX_PRICE 0.25, tighter fallback, ATR 0.03,
  cheap-entry TP/SL rules, one trade per window, strategy_tier logging.
- Trades log: logs/trades_2hr_hybrid.csv
- Creates STOP_BOT.txt after 2 hours.

Run: python run_2hr_paper.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution
execution.set_trades_csv(os.path.join("logs", "trades_2hr_tight.csv"))

import config
config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

TWO_HR_SEC = 2 * 60 * 60  # 7200


def _reset_state() -> None:
    """Reset state.json for fresh 2hr run."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_positions"] = {}
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    state["starting_balance_usdc"] = getattr(config, "BANKROLL_START_USDC", 50.0)
    state["last_traded_condition_id"] = ""
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 2hr hybrid paper test.")


def _stop_after_2hr() -> None:
    """Create STOP_BOT.txt after 2 hours."""
    time.sleep(TWO_HR_SEC)
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
    logger.info("2-HOUR PAPER TEST: hybrid + improvements (one trade/window, cheap TP/SL)")
    logger.info("Trades log: logs/trades_2hr_tight.csv")
    logger.info("Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_2hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
