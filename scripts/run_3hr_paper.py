"""
Run a 3-hour paper test with the optimised hybrid strategy.

- STRATEGY_MODE = hybrid (contrarian → momentum → fallback)
- Compounding: BANKROLL_START + cumulative PnL, RISK_PCT_PER_TRADE, capped by RISK_PER_TRADE_USDC
- Take profit / stop loss enabled
- Trades log: logs/trades_3hr_50usd.csv
- Creates STOP_BOT.txt after 3 hours.

Run: python run_3hr_paper.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import config
config.STRATEGY_MODE = "hybrid"

import execution
execution.set_trades_csv(os.path.join("logs", "trades_3hr_50usd.csv"))

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

THREE_HOURS_SEC = 3 * 3600  # 10800


def _reset_state() -> None:
    """Reset state.json for fresh 3hr run (incl. cumulative_pnl for compounding)."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_positions"] = {}
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    state["cumulative_pnl_usdc"] = 0.0
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 3hr paper test (hybrid + TP/SL + compounding).")


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
    logger.info("3-HOUR PAPER TEST: optimised hybrid (TP/SL + compounding)")
    logger.info("Trades log: logs/trades_3hr_50usd.csv | Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_3h, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
