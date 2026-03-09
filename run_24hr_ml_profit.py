"""
24-hour paper test: ML-enhanced hybrid, tuned for profit.

Stack: late_window → contrarian (ML filter) → momentum. No fallback.
- MODEL_USE_ML=True: sklearn RF for P(UP) in contrarian filter
- CONTRARIAN_USE_MODEL_FAIR_VALUE=True: only contrarian when model edge
- FALLBACK_ENABLED=False: skip (0/9 in prior test)
- ENTRY_WINDOW_SECS=180, LOOP_INTERVAL=5 for more checkpoints

Outputs: logs/trades_24h_ml.csv, trade_entries_24h_ml.csv, signals_evaluated_24h_ml.csv

Run: python run_24hr_ml_profit.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution

execution.set_trades_csv(os.path.join("logs", "trades_24h_ml.csv"))
execution.set_trade_entries_csv(os.path.join("logs", "trade_entries_24h_ml.csv"))
execution.set_signals_evaluated_csv(os.path.join("logs", "signals_evaluated_24h_ml.csv"))

import config

config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180
config.LOOP_INTERVAL_SECONDS = 5

# ML + contrarian
config.MODEL_USE_ML = True
config.CONTRARIAN_USE_MODEL_FAIR_VALUE = True
config.CONTRARIAN_MIN_EDGE = 0.03
config.CONTRARIAN_MAX_PRICE = 0.20

# Contrarian already uses model filter; no global EV gate (would block momentum/late-window)

# No fallback (lost money)
config.FALLBACK_ENABLED = False

# Momentum: strict enough
config.MIN_EMA_SPREAD_USD = 20
config.MIN_ENTRY_PRICE = 0.35
config.MAX_ENTRY_PRICE = 0.65

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

TWENTY_FOUR_HR_SEC = 24 * 60 * 60


def _reset_state() -> None:
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_position"] = None
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    state["starting_balance_usdc"] = getattr(config, "BANKROLL_START_USDC", 50.0)
    state["last_traded_condition_id"] = ""
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 24hr ML profit run.")


def _stop_after_24hr() -> None:
    time.sleep(TWENTY_FOUR_HR_SEC)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("24 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)
    for p in [
        os.path.join("logs", "trades_24h_ml.csv"),
        os.path.join("logs", "trade_entries_24h_ml.csv"),
        os.path.join("logs", "signals_evaluated_24h_ml.csv"),
    ]:
        if os.path.isfile(p):
            os.remove(p)

    _reset_state()

    logger.info("=" * 60)
    logger.info("24-HOUR ML PROFIT PAPER TEST")
    logger.info("  late_window → contrarian (ML) → momentum | no fallback")
    logger.info("  MODEL_USE_ML=True, CONTRARIAN model filter")
    logger.info("  logs/trades_24h_ml.csv")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_24hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
