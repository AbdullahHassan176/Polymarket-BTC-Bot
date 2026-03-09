"""
Run a 24-hour paper test with a profitability-first model profile.

Profile goals:
- Fewer, higher-conviction trades
- Disable weak fallback trend entries
- Require model-implied edge after cost buffer before entering any trade

Outputs:
  - logs/trades_24h_profit.csv
  - logs/trade_entries_24h_profit.csv
  - logs/signals_evaluated_24h_profit.csv
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution

execution.set_trades_csv(os.path.join("logs", "trades_24h_profit.csv"))
execution.set_trade_entries_csv(os.path.join("logs", "trade_entries_24h_profit.csv"))
execution.set_signals_evaluated_csv(os.path.join("logs", "signals_evaluated_24h_profit.csv"))

import config

# Profitability-first profile.
config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180
config.FALLBACK_TREND_ENABLED = False
config.FALLBACK_PRICE_MAX = 0.40
config.CONTRARIAN_USE_MODEL_FAIR_VALUE = True
config.CONTRARIAN_MIN_EDGE = 0.04
config.MODEL_EV_GATE_ENABLED = True
config.MODEL_EV_MIN_EDGE = 0.03
config.MODEL_EV_COST_BUFFER = 0.015

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
    logger.info("State reset for 24hr profitability-first paper test.")


def _stop_after_24hr() -> None:
    time.sleep(TWENTY_FOUR_HR_SEC)
    with open(KILL_SWITCH_FILE, "w", encoding="utf-8") as f:
        f.write("")
    logger.info("24 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()
    os.makedirs("logs", exist_ok=True)

    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)
    for p in [
        os.path.join("logs", "trades_24h_profit.csv"),
        os.path.join("logs", "trade_entries_24h_profit.csv"),
        os.path.join("logs", "signals_evaluated_24h_profit.csv"),
    ]:
        if os.path.isfile(p):
            os.remove(p)

    _reset_state()

    logger.info("=" * 60)
    logger.info("24-HOUR PAPER TEST: profitability-first model profile")
    logger.info("  trades_24h_profit.csv / trade_entries_24h_profit.csv / signals_evaluated_24h_profit.csv")
    logger.info(
        "  Overrides: FALLBACK_TREND_ENABLED=False, FALLBACK_PRICE_MAX=0.40, "
        "CONTRARIAN_MIN_EDGE=0.04, MODEL_EV_GATE_ENABLED=True, MODEL_EV_MIN_EDGE=0.03, MODEL_EV_COST_BUFFER=0.015"
    )
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_24hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
