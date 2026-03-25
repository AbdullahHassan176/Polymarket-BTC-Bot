"""
Run a 12-hour paper test: reversal strategy only.

- STRATEGY_MODE = "reversal"
- Buy when YES or NO <= 10c; sell when bid >= 50c (mean reversion).
- Trades log: logs/trades_12hr_reversal.csv
- Creates STOP_BOT.txt after 12 hours.

Run: python run_12hr_reversal.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution

execution.set_trades_csv(os.path.join("logs", "trades_12hr_reversal.csv"))

import config

config.STRATEGY_MODE = "reversal"
config.REVERSAL_PRICE_THRESHOLD = 0.10
config.REVERSAL_BET_USDC = 5.0
config.REVERSAL_TAKE_PROFIT_PRICE = 0.50

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

TWELVE_HR_SEC = 12 * 60 * 60  # 43200


def _reset_state() -> None:
    """Reset state.json for fresh 12hr reversal run."""
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
    logger.info("State reset for 12hr reversal paper test.")


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
    logger.info("12-HOUR PAPER TEST: reversal (buy <=10c, TP at 50c)")
    logger.info("Trades log: logs/trades_12hr_reversal.csv")
    logger.info("Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_12hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
