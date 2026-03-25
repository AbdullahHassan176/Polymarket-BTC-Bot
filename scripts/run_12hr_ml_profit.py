"""
12-hour paper test: ML-enhanced hybrid, tuned for profit.

Same config as run_24hr_ml_profit.py (late_window → contrarian ML → momentum).
Outputs: logs/trades_12h_ml.csv, trade_entries_12h_ml.csv, signals_evaluated_12h_ml.csv

Run: python run_12hr_ml_profit.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution

execution.set_trades_csv(os.path.join("logs", "trades_12h_ml.csv"))
execution.set_trade_entries_csv(os.path.join("logs", "trade_entries_12h_ml.csv"))
execution.set_signals_evaluated_csv(os.path.join("logs", "signals_evaluated_12h_ml.csv"))

import config

config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180
config.LOOP_INTERVAL_SECONDS = 5
config.MODEL_USE_ML = True
config.CONTRARIAN_USE_MODEL_FAIR_VALUE = True
config.CONTRARIAN_MIN_EDGE = 0.03
config.CONTRARIAN_MAX_PRICE = 0.20
config.FALLBACK_ENABLED = False
config.MIN_EMA_SPREAD_USD = 20
config.MIN_ENTRY_PRICE = 0.35
config.MAX_ENTRY_PRICE = 0.65

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

TWELVE_HR_SEC = 12 * 60 * 60


def _reset_state() -> None:
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
    logger.info("State reset for 12hr ML profit run.")


def _stop_after_12hr() -> None:
    time.sleep(TWELVE_HR_SEC)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("12 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)
    for p in [
        os.path.join("logs", "trades_12h_ml.csv"),
        os.path.join("logs", "trade_entries_12h_ml.csv"),
        os.path.join("logs", "signals_evaluated_12h_ml.csv"),
    ]:
        if os.path.isfile(p):
            os.remove(p)

    _reset_state()

    logger.info("=" * 60)
    logger.info("12-HOUR ML PROFIT PAPER TEST")
    logger.info("  late_window → contrarian (ML) → momentum | no fallback")
    logger.info("  logs/trades_12h_ml.csv")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_12hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
